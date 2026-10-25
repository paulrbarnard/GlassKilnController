"""Turns a Profile into the actual GM LT button-press sequence.

Every method here follows the same shape: press a key, then confirm via
``vision`` that the display actually changed the way the manual says it
should, before pressing the next key. See docs/architecture.md ("Why
'press, then verify' instead of a fixed script") for why this project
never fires blind.

This module is deliberately independent of Qt — see ``ui/`` for the
touchscreen front-end, which should only ever call into ``Sequencer``, never
into ``bridge``/``vision`` directly.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from kiln.bridge import KilnBridge
from kiln.profiles import Profile, ProfileKind
from kiln.protocol import (
    EDIT_PROMPT_WINDOW_ADD_SECONDS,
    EDIT_PROMPT_WINDOW_SKIP_SECONDS,
    SSEG_PROMPT_WINDOW_SECONDS,
    DisplayMessage,
    Key,
    Mode,
    Process,
    ProgrammingLevel,
    RampHoldSegment,
    Speed,
)
from kiln.programs import KilnProgram, ReviewParseError, parse_review, same_segments
from kiln.vision import ControllerState, KilnVision
from kiln.settings import f_to_c, rate_f_to_c

log = logging.getLogger(__name__)

# Generic timeouts for prompts the manual doesn't give an explicit window
# for (mode menu, speed/process selection, rEdI, ON, ...). These are
# deliberately generous; TODO(hardware): tighten once real controller
# latency is measured.
DEFAULT_TIMEOUT_S = 5.0
MAX_MENU_SCROLL_STEPS = 8
# A menu press normally shows within ~1 s; after this long, assume the
# membrane button didn't register and press again (up to the attempts).
MENU_CHANGE_TIMEOUT_S = 3.0
MENU_PRESS_ATTEMPTS = 3


# How a held UP/DOWN scrolls a value (recorded frame by frame on the lever
# mount, 2026-10-25, scripts/scroll_profile.py; DOWN matched UP): nothing
# for ~2 s, then faster and faster, to hundreds of steps a second by 20 s.
# Deterministic, but too fast at the end for a timed press to land on a
# value, so holds watch the display and release on the value
# (_hold_towards); this curve only sets each hold's time limit.
# (seconds held, steps moved):
SCROLL_CURVE = (
    (2.0, 0), (3.0, 2), (4.0, 4), (5.0, 13), (6.0, 24), (8.0, 48), (10.0, 76),
    (12.0, 111), (14.0, 154), (16.0, 213), (18.0, 304), (20.0, 524),
)
SCROLL_HOLD_MAX_S = 30.0  # at duty 80 a 30 s hold costs ~3.0 of the 6.0 heat budget
TAP_LIMIT = 12  # changes up to this many steps are tapped, not scrolled
TAP_GAP_S = 0.25  # between taps in a burst
# Closed-loop scroll holds (_hold_towards): how far back to measure the
# scroll speed, how much further the value runs after release is asked for
# (camera, Bridge and button).
SCROLL_RATE_WINDOW_S = 0.3
RELEASE_LAG_S = 0.25
RELEASE_LEAD = 1.5
SETTLE_GAP_S = 0.4  # two readings this far apart must agree for a value to count as settled


def scroll_hold_for(steps: float) -> float:
    """How long to hold UP/DOWN to scroll about ``steps`` (capped). Between
    measured points the steps grow geometrically (the controller keeps
    accelerating), so interpolate on log(steps): a straight line overstated
    the steps for a hold and every hold landed well short."""

    if steps <= SCROLL_CURVE[1][1]:
        return SCROLL_CURVE[1][0]
    for (t0, n0), (t1, n1) in zip(SCROLL_CURVE[1:], SCROLL_CURVE[2:]):
        if steps <= n1:
            return t0 + (t1 - t0) * math.log(steps / n0) / math.log(n1 / n0)
    return SCROLL_HOLD_MAX_S


class SequenceError(RuntimeError):
    """Raised when a press didn't produce the expected display state within
    its timeout — most commonly a missed timing window. The caller (usually
    ``run_profile``) should treat this as "back out to IdLE and stop", not
    retry blindly."""


class SequenceAborted(SequenceError):
    """Raised inside a running sequence when stop() has been requested, so
    programming stops before its next press instead of carrying on."""


class ProgramMismatch(SequenceError):
    """The kiln slot doesn't hold the program the app expected (edited at
    the kiln, or the app's record was wrong). Nothing was started."""

    def __init__(self, slot: int, found: Optional[KilnProgram]):
        super().__init__(f"kiln slot {slot} doesn't hold the expected program")
        self.slot = slot
        self.found = found


class ControllerErrorState(SequenceError):
    """Raised when vision reads one of protocol.ERROR_MESSAGES. The kiln has
    already shut itself off — see docs/controller_protocol.md."""

    def __init__(self, state: ControllerState):
        super().__init__(f"controller reported error: {state.message}")
        self.state = state


ProgressCallback = Callable[[str], None]

_MODE_MESSAGES = (
    DisplayMessage.AUTO,
    DisplayMessage.PRO,
    DisplayMessage.LOAD,
    DisplayMessage.OPTIONS,
)


@dataclass
class Sequencer:
    bridge: KilnBridge
    vision: KilnVision
    on_progress: Optional[ProgressCallback] = None
    # Best-effort tracking of controller programming level so we don't
    # unlock Advanced mode more than necessary. Not authoritative — the
    # controller has no way to report this via display, so a mismatch here
    # just costs an extra (harmless) unlock attempt, never a wrong press.
    level: ProgrammingLevel = ProgrammingLevel.BEGINNER
    # The unit the GM LT is set to. Profiles are stored in °F, so every
    # temperature and rate is converted to this unit before it's dialled in
    # (the controller itself only ever sees its own unit). Kept in step with
    # the app's settings by set_temperature_scale().
    controller_celsius: bool = True
    # Set by stop() so a sequence running on another thread gives up before
    # its next press or display wait (see _press/_wait_for).
    abort: threading.Event = field(default_factory=threading.Event)

    def _progress(self, message: str) -> None:
        log.info(message)
        if self.on_progress:
            self.on_progress(message)

    # -- low-level helpers --------------------------------------------------

    def _controller_temp(self, temp_f: float) -> float:
        """A °F temperature in the controller's unit, rounded to a whole
        degree (the controller only takes whole degrees)."""

        return float(round(f_to_c(temp_f) if self.controller_celsius else temp_f))

    def _controller_rate(self, rate_f_per_hr: float) -> float:
        return float(round(rate_f_to_c(rate_f_per_hr) if self.controller_celsius else rate_f_per_hr))

    def _check_abort(self) -> None:
        if self.abort.is_set():
            raise SequenceAborted("stopped by request")

    def _press(self, key: Key) -> None:
        self._check_abort()
        self.bridge.press(key)

    def _read(self) -> ControllerState:
        state = self.vision.read()
        if state.is_error:
            raise ControllerErrorState(state)
        return state

    def _wait_for(
        self,
        predicate: Callable[[ControllerState], bool],
        timeout_s: float = DEFAULT_TIMEOUT_S,
        description: str = "expected state",
    ) -> ControllerState:
        def guarded(state: ControllerState) -> bool:
            if state.is_error or self.abort.is_set():
                return True  # let it through so we can raise below
            return predicate(state)

        self._check_abort()
        state = self.vision.wait_for(guarded, timeout_s)
        self._check_abort()
        if state is None:
            raise SequenceError(f"timed out waiting for: {description}")
        if state.is_error:
            raise ControllerErrorState(state)
        return state

    def _wait_for_firing(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> ControllerState:
        """After the final ENTER on rEdI: ON shows briefly, then the
        temperature (p.15). Accept either, so a missed glimpse of ON can't
        turn a firing that has started into a reported failure."""

        return self._wait_for(
            lambda s: s.message is DisplayMessage.ON
            or (s.message is None and s.numeric_value is not None),
            timeout_s,
            description="ON or a temperature after starting",
        )

    def _wait_for_message(
        self, message: DisplayMessage, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> ControllerState:
        return self._wait_for(
            lambda s: s.message is message, timeout_s, description=message.value
        )

    def _scroll_to(
        self,
        target: DisplayMessage,
        max_steps: int = MAX_MENU_SCROLL_STEPS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        state: Optional[ControllerState] = None,
    ) -> ControllerState:
        """Assumes the display is already showing one option of a cyclic
        menu; presses UP repeatedly (per the manual's "scroll to X, ENTER"
        instructions) until ``target`` is shown, or raises after
        ``max_steps`` (menus are short and cyclic, so this should never
        legitimately take more than a handful of presses).
        """

        state = state or self._read()
        for _ in range(max_steps):
            if state.message is target:
                return state
            state = self._press_until_changed(Key.UP, state)
        raise SequenceError(f"couldn't scroll to {target.value} within {max_steps} steps")

    def _enter_from(
        self,
        current: DisplayMessage,
        accept: Optional[Callable[[ControllerState], bool]] = None,
        attempts: int = MENU_PRESS_ATTEMPTS,
    ) -> ControllerState:
        """Press ENTER on ``current`` and wait for the next screen. Press
        again only if the display still shows ``current`` (at IdLE, also its
        alternating temperature): that's the only case where a repeat can't
        select something twice. On the kiln the first try once read IdLE
        straight after ENTER and failed (2026-10-22)."""

        accept = accept or (lambda s: s.message is not None and s.message is not current)
        for _ in range(attempts):
            self._press(Key.ENTER)
            state = self.vision.wait_for(
                lambda s: s.is_error or self.abort.is_set() or accept(s),
                MENU_CHANGE_TIMEOUT_S,
            )
            self._check_abort()
            if state is not None:
                if state.is_error:
                    raise ControllerErrorState(state)
                return state
            now = self._read()
            still_there = now.message is current or (
                current is DisplayMessage.IDLE and now.message is None and now.numeric_value is not None
            )
            if not still_there:
                raise SequenceError(f"unexpected {now.raw_text!r} after ENTER on {current.value}")
        raise SequenceError(f"ENTER on {current.value} didn't register after {attempts} presses")

    def _start_from_ready(self, attempts: int = 2) -> ControllerState:
        """The final ENTER on rEdI starts the firing. Re-press only while the
        display still shows rEdI: once ON or a temperature is showing, a
        second ENTER would stop the program."""

        for _ in range(attempts):
            self._press(Key.ENTER)
            try:
                return self._wait_for_firing()
            except SequenceError:
                if self._read().message is not DisplayMessage.READY:
                    raise
        raise SequenceError(f"ENTER on rEdI didn't register after {attempts} presses")

    def _press_until_changed(
        self, key: Key, before: ControllerState, attempts: int = MENU_PRESS_ATTEMPTS
    ) -> ControllerState:
        """Press ``key`` and wait for the display to move off ``before``'s
        menu option, pressing again if it doesn't. The membrane buttons
        don't register every solenoid press, and the display takes ~0.5 s
        to update, so reading straight after a press can still show the
        old option (seen on the kiln, 2026-10-22)."""

        for _ in range(attempts):
            self._press(key)
            try:
                return self._wait_for(
                    lambda s: s.message is not None and s.message is not before.message,
                    MENU_CHANGE_TIMEOUT_S,
                    description="menu option to change",
                )
            except SequenceError:
                continue
        raise SequenceError(f"{key.name} didn't change {before.raw_text!r} after {attempts} presses")

    def _dial_numeric(
        self,
        target: float,
        tolerance: float = 0.5,
        max_steps: int = 400,
        timeout_s: float = 2.0,
    ) -> None:
        """Adjust a flashing numeric value (temperature, rate, hold, ...)
        toward ``target`` using UP/DOWN, reading the value back after each
        press until within ``tolerance``. Used for MGF temp/hold entry and
        PRO-mode rate/temp/hold entry (pp.16-19).

        TODO(hardware): the manual notes holding UP/DOWN accelerates the
        rate of change (p.10) — once real timing is known, use held presses
        for large deltas and single taps near the target instead of always
        single-stepping, which will be slow for e.g. a 1465°F entry.
        """

        state = self._wait_for(
            lambda s: s.numeric_value is not None, timeout_s, description="numeric value"
        )
        steps = 0
        while abs((state.numeric_value or 0.0) - target) > tolerance and steps < max_steps:
            key = Key.UP if (state.numeric_value or 0.0) < target else Key.DOWN
            self._press(key)
            state = self._wait_for(
                lambda s: s.numeric_value is not None,
                timeout_s,
                description="numeric value",
            )
            steps += 1
        if abs((state.numeric_value or 0.0) - target) > tolerance:
            raise SequenceError(
                f"couldn't dial value to {target} (stuck at {state.numeric_value})"
            )

    # -- universal navigation -----------------------------------------------

    def goto_idle(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        """The universal escape hatch (p.8, p.10): hold ENTER 4s from
        anywhere. Safe to call as a recovery step even if we're not sure
        what state the controller is in."""

        # Already there (IdLE alternates with the temperature about twice a
        # second, so 2 s is plenty to see it): skip the 5 s hold. Most
        # actions start from IdLE, and each long press warms the coil.
        if self.vision.wait_for(lambda s: s.message is DisplayMessage.IDLE, 2.0) is not None:
            return
        self._check_abort()
        self._progress("returning to IdLE")
        self.bridge.hold_long(Key.ENTER)
        # Stop at whichever of IdLE/PF appears first, rather than polling
        # straight through to IdLE — on real hardware PF does NOT clear
        # itself, so we must observe and explicitly press through it (p.30),
        # not just wait longer.
        state = self._wait_for(
            lambda s: s.message in (DisplayMessage.IDLE, DisplayMessage.POWER_FAILURE),
            timeout_s,
            description="IdLE or PF",
        )
        if state.message is DisplayMessage.POWER_FAILURE:
            self._press(Key.ENTER)
            self._wait_for_message(DisplayMessage.IDLE, timeout_s)

    def ensure_advanced_unlocked(self) -> None:
        if self.level is ProgrammingLevel.ADVANCED:
            return
        self.goto_idle()
        self._progress("unlocking Advanced level")
        self.bridge.hold_long(Key.UP)
        self.level = ProgrammingLevel.ADVANCED

    def relock_beginner(self) -> None:
        self.goto_idle()
        self._progress("relocking Beginner level")
        self.bridge.hold_long(Key.DOWN)
        self.level = ProgrammingLevel.BEGINNER

    def select_mode(self, mode: Mode) -> ControllerState:
        """Open the mode menu from IdLE and select ``mode``.

        The level is read off the controller rather than assumed: on
        Advanced the menu loops Pro/LoAd/OPt/AUto, on Beginner it only offers
        AUto, so UP can't move it. Only if scrolling to an Advanced mode
        fails is the level unlocked (hold UP at IdLE) and the menu retried.
        """

        target = DisplayMessage(mode.value)
        for attempt in range(2):
            self.goto_idle()
            state = self._enter_from(
                DisplayMessage.IDLE, accept=lambda s: s.message in _MODE_MESSAGES
            )
            try:
                self._scroll_to(target, state=state)
            except SequenceError:
                if attempt or mode is Mode.AUTO:
                    raise
                self.level = ProgrammingLevel.BEGINNER
                self.ensure_advanced_unlocked()
                continue
            if mode is not Mode.AUTO:
                self.level = ProgrammingLevel.ADVANCED
            # A missed ENTER here once left the scale change scrolling the
            # mode menu for CHG° (2026-10-22). The next screen may be a word
            # (AUto's speeds, OPt's list) or a number (Pro/LoAd's slot).
            return self._enter_from(
                target,
                accept=lambda s: s.message is not target
                and (s.message is not None or s.numeric_value is not None),
            )

    def _require_idle(self, action: str, timeout_s: float = 3.0) -> None:
        """Make sure the controller is at IdLE before ``action``.

        If it's in a menu or at a prompt (left there by an interrupted
        action), return it to IdLE by holding ENTER and carry on. If it seems
        to be firing (only the temperature showing), don't: holding ENTER
        would stop the firing. With the front panel covered there's no
        manual way back, so when the controller won't return to IdLE the
        error says to power-cycle the kiln.
        """

        if self.vision.wait_for(lambda s: s.message is DisplayMessage.IDLE, timeout_s) is not None:
            return
        self._require_display(action)
        if self._looks_like_firing():
            raise SequenceError(
                f"not {action}: the kiln seems to be firing (only the temperature is "
                "showing). Stop the firing first."
            )
        self._progress("the controller isn't at IdLE; returning it there first")
        try:
            self.goto_idle()
        except SequenceError:
            pass
        if self.vision.wait_for(lambda s: s.message is DisplayMessage.IDLE, timeout_s) is None:
            raise SequenceError(
                f"not {action}: couldn't return the controller to IdLE. Switch the kiln "
                "off and on at its power switch, then try again."
            )

    def _looks_like_firing(self, watch_s: float = 4.0) -> bool:
        """During a firing the display shows only the temperature (or ON);
        a menu or prompt shows a label within a second or two (value screens
        alternate label and value). So: firing if nothing but plain numbers
        or ON is seen for ``watch_s``."""

        deadline = time.monotonic() + watch_s
        while time.monotonic() < deadline:
            state = self.vision.read()
            if state.message is DisplayMessage.ON:
                return True
            if state.message is not None:
                return False
        return True

    def _require_display(self, action: str) -> None:
        """Raise a clear error if the camera can't read the display at all
        (rather than blaming what the controller is showing)."""

        if self.vision.read().raw_text == "?":
            raise SequenceError(
                f"not {action}: the app can't see the kiln display (camera problem). "
                "Restarting the app usually clears it."
            )

    # -- AUTO / GlassFire (p.14-15) ------------------------------------------

    def run_glassfire(
        self,
        speed: Speed,
        process: Process,
        temp_override_f: Optional[float] = None,
        hold_override_minutes: Optional[float] = None,
    ) -> None:
        self._progress(f"programming GlassFire: {process.value}/{speed.value}")
        self.goto_idle()
        state = self._enter_from(DisplayMessage.IDLE, accept=lambda s: s.message in _MODE_MESSAGES)
        self._scroll_to(DisplayMessage.AUTO, state=state)
        state = self._enter_from(DisplayMessage.AUTO)

        speed_msg = DisplayMessage(speed.value)
        self._scroll_to(speed_msg, state=state)
        state = self._enter_from(speed_msg)

        # Whether MGF is on determines what happens next: either the
        # process temp/hold prompts (flashing values, p.15 step 5) or
        # straight to rEdI. Either is valid; branch on what we actually see.
        process_msg = DisplayMessage(process.value)
        self._scroll_to(process_msg, state=state)
        state = self._enter_from(
            process_msg,
            accept=lambda s: s.message is DisplayMessage.READY or s.numeric_value is not None,
        )
        if state.message is not DisplayMessage.READY:
            self._progress("MGF active: confirming process temperature/hold")
            if temp_override_f is not None:
                self._press(Key.UP)  # stop flashing
                self._dial_numeric(self._controller_temp(temp_override_f))
            self._press(Key.ENTER)
            self._wait_for(
                lambda s: s.numeric_value is not None, description="hold time prompt"
            )
            if hold_override_minutes is not None:
                self._press(Key.UP)
                self._dial_numeric(hold_override_minutes)
            self._press(Key.ENTER)
            self._wait_for_message(DisplayMessage.READY)

        self._progress("ready — starting")
        self._start_from_ready()

    # -- PRO / Ramp & Hold (p.18-19) -----------------------------------------

    def program_ramp_hold(self, slot: int, segments: list[RampHoldSegment]) -> None:
        if not 1 <= slot <= 5:
            raise ValueError(f"slot must be 1-5, got {slot}")
        self._progress(f"programming Ramp & Hold slot {slot} ({len(segments)} segments)")
        self.select_mode(Mode.PRO)

        self._dial_numeric(float(slot), tolerance=0.01)
        self._press(Key.ENTER)

        self._dial_numeric(float(len(segments)), tolerance=0.01)
        self._press(Key.ENTER)

        for i, seg in enumerate(segments, start=1):
            self._progress(f"segment {i}/{len(segments)}")
            self._wait_for(
                lambda s: s.message is DisplayMessage.RATE_PROMPT
                and s.segment_number == i,
                description=f"rate prompt segment {i}",
            )
            self._dial_numeric(self._controller_rate(seg.rate_f_per_hr), tolerance=1.0)
            self._press(Key.ENTER)

            self._wait_for(
                lambda s: s.numeric_value is not None, description="temperature prompt"
            )
            self._dial_numeric(self._controller_temp(seg.target_temp_f), tolerance=1.0)
            self._press(Key.ENTER)

            self._wait_for(
                lambda s: s.message is DisplayMessage.HOLD_PROMPT
                and s.segment_number == i,
                description=f"hold prompt segment {i}",
            )
            # Hold is entered as Hours.Minutes (p.19); we work in total
            # minutes in the data model, so convert at this boundary.
            hours, minutes = divmod(seg.hold_minutes, 60)
            self._dial_numeric(hours + minutes / 100.0, tolerance=0.01)
            self._press(Key.ENTER)

        self._wait_for_message(DisplayMessage.READY)
        self._progress("ready — starting")
        self._start_from_ready()

    def load_program(self, slot: int, start: bool = True) -> None:
        if not 1 <= slot <= 5:
            raise ValueError(f"slot must be 1-5, got {slot}")
        self._progress(f"loading Ramp & Hold slot {slot}")
        self.select_mode(Mode.LOAD)
        self._dial_numeric(float(slot), tolerance=0.01)
        self._press(Key.ENTER)
        self._wait_for_message(DisplayMessage.READY)
        if start:
            self._start_from_ready()

    # -- Reading PRO slots (LoAd > slot > rEdI > DOWN = Review) ----------------

    def _slot_number(self, timeout_s: float = 3.0) -> int:
        """The slot shown on LoAd/Pro's slot screen, which alternates "Pro"
        with the number ("0005")."""

        state = self.vision.wait_for(
            lambda s: s.message is None and s.numeric_value is not None, timeout_s
        )
        if state is None:
            raise SequenceError("no slot number on the display")
        return int(state.numeric_value)

    def _choose_slot(self, slot: int) -> None:
        """On the slot screen, step to ``slot`` (UP/DOWN move one slot; 1-5,
        no wrap) and confirm the number each time."""

        current = self._slot_number()
        for _ in range(8):
            if current == slot:
                return
            key = Key.DOWN if current > slot else Key.UP
            for _attempt in range(MENU_PRESS_ATTEMPTS):
                self._press(key)
                state = self.vision.wait_for(
                    lambda s: s.message is None
                    and s.numeric_value is not None
                    and int(s.numeric_value) != current,
                    MENU_CHANGE_TIMEOUT_S,
                )
                self._check_abort()
                if state is not None:
                    current = int(state.numeric_value)
                    break
            else:
                raise SequenceError(f"{key.name} didn't change slot {current}")
        raise SequenceError(f"couldn't reach slot {slot}")

    def _load_slot(self, slot: int) -> None:
        """LoAd ``slot``, ending on rEdI (nothing is started)."""

        self.select_mode(Mode.LOAD)
        self._choose_slot(slot)
        for _ in range(MENU_PRESS_ATTEMPTS):
            self._press(Key.ENTER)
            state = self.vision.wait_for(
                lambda s: s.message is DisplayMessage.READY or s.is_error, MENU_CHANGE_TIMEOUT_S
            )
            self._check_abort()
            if state is not None:
                if state.is_error:
                    raise ControllerErrorState(state)
                return
            now = self._read()
            if not (now.message is DisplayMessage.PRO or now.numeric_value is not None):
                raise SequenceError(f"unexpected {now.raw_text!r} after choosing slot {slot}")
        raise SequenceError(f"ENTER on slot {slot} didn't register")

    def _review(self, max_s: float = 60.0) -> list[str]:
        """From rEdI, press DOWN and record every screen of the Review until
        it comes back to rEdI. Each screen shows for ~0.5 s."""

        for _ in range(MENU_PRESS_ATTEMPTS):
            self._press(Key.DOWN)
            screens: list[str] = []
            last = None
            pressed = time.monotonic()
            while time.monotonic() - pressed < max_s:
                self._check_abort()
                state = self.vision.read()
                if state.is_error:
                    raise ControllerErrorState(state)
                text = state.raw_text
                if "?" in text or text == last:
                    if not screens and time.monotonic() - pressed > MENU_CHANGE_TIMEOUT_S:
                        break  # DOWN didn't register: press again
                    continue
                last = text
                if state.message is DisplayMessage.READY:
                    if screens:
                        return screens
                    if time.monotonic() - pressed > MENU_CHANGE_TIMEOUT_S:
                        break
                    continue
                screens.append(text)
            else:
                raise SequenceError("the Review never returned to rEdI")
        raise SequenceError("DOWN on rEdI didn't start the Review")

    def read_program(self, slot: int, reviews: int = 2) -> KilnProgram:
        """Read PRO ``slot`` off the controller without changing it: LoAd
        it, Review it ``reviews`` times, and only accept the program if every
        Review parses to the same thing (a missed screen fails to parse or
        disagrees). Returns to IdLE. Doesn't start anything."""

        if not 1 <= slot <= 5:
            raise ValueError(f"slot must be 1-5, got {slot}")
        self._require_idle("reading the kiln's programs")
        self._progress(f"reading program slot {slot}")
        programs: list[KilnProgram] = []

        def action() -> None:
            self._load_slot(slot)
            for _ in range(reviews + 1):  # one spare Review for a bad read
                try:
                    programs.append(parse_review(self._review()))
                except ReviewParseError as exc:
                    log.info("slot %d review didn't parse (%s); reviewing again", slot, exc)
                if len(programs) >= reviews:
                    break
            self.goto_idle()

        self._backing_out_on_failure(action)
        if len(programs) < reviews or any(p != programs[0] for p in programs):
            raise SequenceError(f"couldn't get {reviews} matching reads of slot {slot}")
        if programs[0].slot != slot:
            raise SequenceError(f"asked for slot {slot} but the controller showed slot {programs[0].slot}")
        return programs[0]

    # -- Writing PRO slots (Pro > slot > SEG > rA/°/HLd per segment) ----------

    def _value(self, scale: int = 1, timeout_s: float = 3.0) -> int:
        """The value on an edit screen (which alternates its label with the
        value), as an integer: ``scale`` 100 turns a hold's H.MM into HHMM."""

        state = self.vision.wait_for(
            lambda s: s.message is None and s.numeric_value is not None, timeout_s
        )
        if state is None:
            raise SequenceError("no value on the display")
        return round(state.numeric_value * scale)

    def _set_value(self, target: int, scale: int = 1, max_steps: int = 40) -> None:
        """Bring the value being edited to ``target``. Big changes use a held
        UP/DOWN sized from SCROLL_CURVE to land a little short (the
        controller waits ~2 s, then scrolls faster and faster); the last few
        steps are a quick burst of taps, then one reading, repeated if any
        tap was missed. Overshoot is corrected the same way. The heat budget
        (bridge.py) adds any cooling pause a coil needs."""

        current = self._value(scale)
        for _ in range(max_steps):
            self._check_abort()
            delta = target - current
            if delta == 0:
                return
            key = Key.UP if delta > 0 else Key.DOWN
            if abs(delta) > TAP_LIMIT:
                before = current
                released_at = self._hold_towards(key, target, current, scale)
                current = self._settled_value(scale)
                log.info(
                    "held %s: %d -> %d (released at %s, target %d)",
                    key.name, before, current, released_at, target,
                )
                continue
            # A burst of taps without checking each one (the display only
            # shows the value every other half second), then one reading.
            for _tap in range(abs(delta)):
                self._press(key)
                time.sleep(TAP_GAP_S)
            before = current
            # The display takes a moment to show the taps; wait for the
            # value to move, then for it to settle, before judging them.
            moved = self.vision.wait_for(
                lambda s: s.message is None
                and s.numeric_value is not None
                and round(s.numeric_value * scale) != before,
                MENU_CHANGE_TIMEOUT_S,
            )
            if moved is None:
                raise SequenceError(f"{key.name} taps didn't change the value {before}")
            current = self._settled_value(scale)
        raise SequenceError(f"couldn't set the value to {target} (stuck at {current})")

    def _settled_value(self, scale: int, timeout_s: float = 4.0) -> int:
        """The value once it has stopped moving: two readings at least
        SETTLE_GAP_S apart that agree. After a scroll the value can still be
        coasting, and after taps the display lags them; a single reading
        taken too soon was wrong in both cases on the kiln (2026-10-25)."""

        deadline = time.monotonic() + timeout_s
        last = None
        while time.monotonic() < deadline:
            value = self._value(scale)
            if value == last:
                return value
            last = value
            time.sleep(SETTLE_GAP_S)
        if last is None:
            raise SequenceError("no value on the display")
        return last

    def _hold_towards(self, key: Key, target: int, current: int, scale: int) -> Optional[int]:
        """Hold ``key`` while watching the value, and release when it's about
        to reach ``target``: the GM LT accelerates the longer a button is
        held (to hundreds of steps a second), so this stops on the value, not
        on a time. The speed is estimated from the last SCROLL_RATE_WINDOW_S
        of readings and the release allows for RELEASE_LAG_S of further
        movement. Readings that go the wrong way or jump implausibly far (a
        frame caught mid-change) are ignored. Returns the value at release."""

        sign = 1 if key is Key.UP else -1
        max_s = min(SCROLL_HOLD_MAX_S, scroll_hold_for(abs(target - current)) + 3.0)
        self.bridge.hold_start(key, int(max_s * 1000))
        started = time.monotonic()
        last = current
        history: list[tuple[float, int]] = [(started, current)]
        try:
            while time.monotonic() - started < max_s:
                self._check_abort()
                state = self.vision.read_fast()
                now = time.monotonic()
                if state.message is not None or state.numeric_value is None:
                    continue
                value = round(state.numeric_value * scale)
                moved = (value - last) * sign
                if moved < 0 or moved > max(60, abs(target - current)):
                    continue  # mid-change frame or misread
                last = value
                history.append((now, value))
                recent = [(t, v) for t, v in history if now - t <= SCROLL_RATE_WINDOW_S]
                rate = 0.0
                if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
                    rate = abs(recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
                # The window's average speed trails the current one on an
                # accelerating scroll, hence the lead factor on the lag.
                if (target - (last + sign * rate * RELEASE_LAG_S * RELEASE_LEAD)) * sign <= 0:
                    return last
            return last
        finally:
            self.bridge.hold_release()

    def _next_field(self, current: DisplayMessage, current_segment: Optional[int], expect) -> ControllerState:
        """ENTER saves the value showing and moves to the next field. Press
        again only while the display still shows the current field (its label
        or its value), never once the next one has appeared."""

        for _ in range(MENU_PRESS_ATTEMPTS):
            self._press(Key.ENTER)
            state = self.vision.wait_for(
                lambda s: s.is_error or self.abort.is_set() or expect(s), MENU_CHANGE_TIMEOUT_S
            )
            self._check_abort()
            if state is not None:
                if state.is_error:
                    raise ControllerErrorState(state)
                return state
            now = self._read()
            still_here = (now.message is current and now.segment_number == current_segment) or (
                now.message is None and now.numeric_value is not None
            )
            if not still_here:
                raise SequenceError(f"unexpected {now.raw_text!r} after ENTER on {current.value}")
        raise SequenceError(f"ENTER on {current.value} didn't register")

    def write_program(self, slot: int, segments: list[RampHoldSegment]) -> None:
        """Enter ``segments`` into PRO ``slot`` without starting it: Pro >
        slot > SEG count > per segment rate, temperature (in the
        controller's unit) and hold > rEdI, then back to IdLE. The caller
        should read the slot back (read_program) to check it."""

        if not 1 <= slot <= 5:
            raise ValueError(f"slot must be 1-5, got {slot}")
        if not 1 <= len(segments) <= 8:
            raise ValueError("a program has 1-8 segments")
        self._require_idle("writing a program to the kiln")
        self.check_controller_unit()
        temp_label = DisplayMessage.DEG_C if self.controller_celsius else DisplayMessage.DEG_F
        self._progress(f"writing program slot {slot} ({len(segments)} segments)")

        def action() -> None:
            self.select_mode(Mode.PRO)
            self._choose_slot(slot)
            self._next_field(
                DisplayMessage.PRO, None, lambda s: s.message is DisplayMessage.SEGMENT_COUNT
            )
            self._set_value(len(segments))
            for i, seg in enumerate(segments, start=1):
                self._next_field(
                    DisplayMessage.SEGMENT_COUNT if i == 1 else DisplayMessage.HOLD_PROMPT,
                    None if i == 1 else i - 1,
                    lambda s, i=i: s.message is DisplayMessage.RATE_PROMPT and s.segment_number == i,
                )
                self._progress(f"slot {slot} segment {i}: rate")
                self._set_value(int(self._controller_rate(seg.rate_f_per_hr)))
                self._next_field(
                    DisplayMessage.RATE_PROMPT, i,
                    lambda s, i=i: s.message is temp_label and s.segment_number == i,
                )
                self._progress(f"slot {slot} segment {i}: temperature")
                self._set_value(int(self._controller_temp(seg.target_temp_f)))
                self._next_field(
                    temp_label, i,
                    lambda s, i=i: s.message is DisplayMessage.HOLD_PROMPT and s.segment_number == i,
                )
                self._progress(f"slot {slot} segment {i}: hold")
                hours, minutes = divmod(int(round(seg.hold_minutes)), 60)
                self._set_value(hours * 100 + minutes, scale=100)
            self._next_field(
                DisplayMessage.HOLD_PROMPT, len(segments),
                lambda s: s.message is DisplayMessage.READY,
            )
            self.goto_idle()

        self._backing_out_on_failure(action)
        self._progress(f"slot {slot} written")

    def start_slot(self, slot: int, expected: list[RampHoldSegment]) -> None:
        """Start the program in PRO ``slot``, but only after checking it is
        ``expected``: LoAd the slot, Review it at rEdI (one pass, a second
        if it doesn't parse) and compare, then ENTER to start. Raises
        ProgramMismatch (back at IdLE, nothing started) if it differs."""

        self.check_controller_unit()
        self._progress(f"loading kiln slot {slot}")

        def action() -> None:
            self._load_slot(slot)
            found = None
            for _ in range(2):
                try:
                    found = parse_review(self._review())
                    break
                except ReviewParseError as exc:
                    log.info("slot %d review didn't parse (%s); reviewing again", slot, exc)
            if found is None or found.slot != slot or not same_segments(expected, found.segments):
                raise ProgramMismatch(slot, found)

        self._backing_out_on_failure(action)
        self._progress("ready — starting")
        self._start_from_ready()

    # -- Profile entry point --------------------------------------------------

    def run_profile(self, profile: Profile) -> None:
        self.check_controller_unit()
        if profile.kind is ProfileKind.GLASSFIRE:
            assert profile.glassfire is not None
            self.run_glassfire(
                speed=profile.glassfire.speed,
                process=profile.glassfire.process,
                temp_override_f=profile.glassfire.temp_override_f,
                hold_override_minutes=profile.glassfire.hold_override_minutes,
            )
        else:
            assert profile.segments is not None
            slot = profile.pro_slot or 1
            self.program_ramp_hold(slot, profile.segments)

    # -- OPTIONS mode (p.22) --------------------------------------------------
    #
    # TODO(hardware): the manual (p.22) describes the OPTIONS flow generically
    # ("select the feature you wish to edit... toggle to your selection") but
    # doesn't spell out MGF's/CHG°'s own on/off display text as precisely as
    # the AUTO/PRO flows. The scroll-then-press-UP toggle below is a
    # best-effort reading of that description; verify the actual prompt
    # sequence against real hardware and tighten this once confirmed.

    def _backing_out_on_failure(self, action: Callable[[], None]) -> None:
        """Run an Options action; if it fails partway, return the controller
        to IdLE before reporting, rather than leaving it in a menu."""

        try:
            action()
        except SequenceAborted:
            raise
        except Exception:  # noqa: BLE001 - a Bridge timeout too, not just SequenceError
            try:
                self.goto_idle()
            except Exception:  # noqa: BLE001
                log.warning("couldn't return to IdLE after a failed action")
            raise

    def toggle_mgf(self) -> None:
        self._require_idle("toggling MGF")
        self._progress("toggling Modify GlassFire mode")

        def action() -> None:
            # Options list (verified on the kiln): MGF -> rSEt -> CHG° -> MGF
            # with UP; ENTER on MGF shows its value, "0FF" or "0N".
            state = self.select_mode(Mode.OPTIONS)
            self._scroll_to(DisplayMessage.MGF, state=state)
            state = self._enter_from(
                DisplayMessage.MGF,
                accept=lambda s: s.message in (DisplayMessage.OFF, DisplayMessage.ON),
            )
            self._press_until_changed(Key.UP, state)  # toggle the value
            self._press(Key.ENTER)
            self.goto_idle()

        self._backing_out_on_failure(action)

    def set_temperature_scale(self, celsius: bool) -> None:
        self._require_idle("changing the temperature scale")
        self._progress(f"setting temperature scale: {'C' if celsius else 'F'}")
        target = DisplayMessage.DEG_C if celsius else DisplayMessage.DEG_F

        def action() -> None:
            # OPt > CHG° > (shows the current unit) > UP to the other > ENTER.
            state = self.select_mode(Mode.OPTIONS)
            self._scroll_to(DisplayMessage.CHANGE_SCALE, state=state)
            state = self._enter_from(
                DisplayMessage.CHANGE_SCALE,
                accept=lambda s: s.message in (DisplayMessage.DEG_C, DisplayMessage.DEG_F),
            )
            self._scroll_to(target, state=state)
            self._press(Key.ENTER)
            self.goto_idle()

        # The final ENTER can't be checked on its own screen, so check the
        # result: a missed press once reported °C while the controller
        # stayed in °F. Try once more before giving up.
        for attempt in range(2):
            self._backing_out_on_failure(action)
            if self.read_controller_celsius() == celsius:
                self.controller_celsius = celsius
                return
            self._progress("the controller didn't take the change; trying again")
        raise SequenceError(
            f"the controller is still not in °{'C' if celsius else 'F'} after two tries"
        )

    def read_controller_celsius(self, timeout_s: float = 3.0) -> Optional[bool]:
        """Which unit the controller is in, from its °C indicator dot; None
        if the display shows nothing readable (e.g. the kiln is off)."""

        state = self.vision.wait_for(lambda s: s.shows_celsius is not None, timeout_s)
        return None if state is None else state.shows_celsius

    def check_controller_unit(self) -> None:
        """Refuse to program anything if the controller's unit isn't the
        one the app dials values in: every temperature would be wrong."""

        shown = self.read_controller_celsius()
        if shown is None:
            self._require_display("starting")
            raise SequenceError("can't tell the controller's unit: is the kiln switched on?")
        if shown != self.controller_celsius:
            app, kiln = ("C", "F") if self.controller_celsius else ("F", "C")
            raise SequenceError(
                f"the controller is in °{kiln} but the app is set to °{app}; "
                f"set the unit in Settings first"
            )

    def factory_reset(self) -> None:
        """WIPES stored PRO programs 1-4 and restores the factory casting
        program to slot 5 (p.22). Callers (UI) must get explicit human
        confirmation before invoking this — the sequencer itself does not
        prompt."""

        self._require_idle("factory resetting")
        self._progress("factory reset")
        self.select_mode(Mode.OPTIONS)
        self._scroll_to(DisplayMessage.RESET)
        self._press(Key.ENTER)
        self.level = ProgrammingLevel.BEGINNER
        self.goto_idle()

    def stop(self, attempts: int = 2) -> ControllerState:
        """Stop the program, whatever the controller is doing, and confirm it.

        Holds ENTER (the manual's "from anywhere back to IdLE", p.8/p.10),
        which stops a running program; a plain short ENTER also stops one
        (p.12) but would select something if the controller is in a menu.
        Success is only reported once the display shows StOP, IdLE or PF;
        otherwise it holds ENTER again. Any sequence still running on another
        thread should be aborted first (see ``abort``) so its presses don't
        interleave with these.
        """

        for attempt in range(1, attempts + 1):
            self._progress(f"stopping: holding ENTER (attempt {attempt})")
            self.bridge.hold_long(Key.ENTER)
            state = self.vision.wait_for(
                lambda s: s.message
                in (DisplayMessage.STOPPED, DisplayMessage.IDLE, DisplayMessage.POWER_FAILURE),
                8.0,
            )
            if state is not None:
                self._progress(f"stopped: controller shows {state.raw_text.strip()!r}")
                return state
        raise SequenceError(
            "stop not confirmed: the display never showed StOP or IdLE. "
            "Switch the kiln off at its power switch."
        )

    # -- Live Edit Mode (p.21-22) — only valid while firing ------------------

    def _enter_edit_prompt(self, window_s: float) -> None:
        self._press(Key.UP)
        self._wait_for(
            lambda s: s.message is DisplayMessage.EDIT_PROMPT,
            timeout_s=window_s + 1.0,
            description="EdIt prompt",
        )
        self._press(Key.ENTER)

    def skip_segment(self) -> None:
        self._progress("skip segment")
        self._enter_edit_prompt(EDIT_PROMPT_WINDOW_SKIP_SECONDS)
        self._wait_for(
            lambda s: s.message is DisplayMessage.SKIP_SEGMENT,
            timeout_s=SSEG_PROMPT_WINDOW_SECONDS + 1.0,
            description="SSEG prompt",
        )
        self._press(Key.ENTER)

    def add_time(self, increments: int = 1) -> None:
        """Adds 5 minutes to the segment-2 (process) hold per increment
        (p.21). Only valid while the display reads HLd2 — check via
        ``view_segment()`` before calling."""

        for _ in range(increments):
            self._progress("add time (+5 min)")
            self._enter_edit_prompt(EDIT_PROMPT_WINDOW_ADD_SECONDS)
            self._wait_for(
                lambda s: s.message is DisplayMessage.SKIP_SEGMENT,
                timeout_s=SSEG_PROMPT_WINDOW_SECONDS + 1.0,
                description="SSEG prompt",
            )
            self._press(Key.UP)  # SSEG -> tME
            self._wait_for_message(DisplayMessage.ADD_TIME)
            self._press(Key.ENTER)

    def add_temperature(self, increments: int = 1) -> None:
        """Adds 5°F (3°C) to the segment-2 process temperature per
        increment (p.22). Only valid while the display reads HLd2."""

        step = "+3°C" if self.controller_celsius else "+5°F"
        for _ in range(increments):
            self._progress(f"add temperature ({step})")
            self._enter_edit_prompt(EDIT_PROMPT_WINDOW_ADD_SECONDS)
            self._wait_for(
                lambda s: s.message is DisplayMessage.SKIP_SEGMENT,
                timeout_s=SSEG_PROMPT_WINDOW_SECONDS + 1.0,
                description="SSEG prompt",
            )
            self._press(Key.UP)  # SSEG -> tME
            self._wait_for_message(DisplayMessage.ADD_TIME)
            self._press(Key.UP)  # tME -> tMP
            self._wait_for_message(DisplayMessage.ADD_TEMP)
            self._press(Key.ENTER)

    def view_segment(self, timeout_s: float = 2.0) -> ControllerState:
        """Non-destructive: reports current segment/rate-vs-hold without
        changing controller state (p.20). Safe to call any time while
        firing, e.g. to decide whether add_time/add_temperature are valid
        right now."""

        self._press(Key.UP)
        state = self._wait_for(
            lambda s: s.message in (DisplayMessage.RATE_PROMPT, DisplayMessage.HOLD_PROMPT),
            timeout_s,
            description="segment status",
        )
        # Manual: EdIt prompt follows briefly, then reverts to temperature
        # display (p.20) — we don't act on it here, just let it pass.
        return state
