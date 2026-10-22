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
from kiln.vision import ControllerState, KilnVision

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


class SequenceError(RuntimeError):
    """Raised when a press didn't produce the expected display state within
    its timeout — most commonly a missed timing window. The caller (usually
    ``run_profile``) should treat this as "back out to IdLE and stop", not
    retry blindly."""


class ControllerErrorState(SequenceError):
    """Raised when vision reads one of protocol.ERROR_MESSAGES. The kiln has
    already shut itself off — see docs/controller_protocol.md."""

    def __init__(self, state: ControllerState):
        super().__init__(f"controller reported error: {state.message}")
        self.state = state


ProgressCallback = Callable[[str], None]


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

    def _progress(self, message: str) -> None:
        log.info(message)
        if self.on_progress:
            self.on_progress(message)

    # -- low-level helpers --------------------------------------------------

    def _press(self, key: Key) -> None:
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
            if state.is_error:
                return True  # let it through so we can raise below
            return predicate(state)

        state = self.vision.wait_for(guarded, timeout_s)
        if state is None:
            raise SequenceError(f"timed out waiting for: {description}")
        if state.is_error:
            raise ControllerErrorState(state)
        return state

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
    ) -> ControllerState:
        """Assumes the display is already showing one option of a cyclic
        menu; presses UP repeatedly (per the manual's "scroll to X, ENTER"
        instructions) until ``target`` is shown, or raises after
        ``max_steps`` (menus are short and cyclic, so this should never
        legitimately take more than a handful of presses).
        """

        state = self._read()
        for _ in range(max_steps):
            if state.message is target:
                return state
            state = self._press_until_changed(Key.UP, state)
        raise SequenceError(f"couldn't scroll to {target.value} within {max_steps} steps")

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

    def select_mode(self, mode: Mode) -> None:
        if mode in (Mode.PRO, Mode.LOAD, Mode.OPTIONS):
            self.ensure_advanced_unlocked()
        else:
            self.goto_idle()
        self._press(Key.ENTER)
        target = DisplayMessage(mode.value)
        self._scroll_to(target)
        self._press(Key.ENTER)

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
        self._press(Key.ENTER)
        self._scroll_to(DisplayMessage.AUTO)
        self._press(Key.ENTER)

        self._scroll_to(DisplayMessage(speed.value))
        self._press(Key.ENTER)

        self._scroll_to(DisplayMessage(process.value))
        self._press(Key.ENTER)

        # Whether MGF is on determines what happens next: either the
        # process temp/hold prompts (flashing values, p.15 step 5) or
        # straight to rEdI. Either is valid; branch on what we actually see.
        state = self._wait_for(
            lambda s: s.message is DisplayMessage.READY or s.numeric_value is not None,
            description="rEdI or MGF temperature prompt",
        )
        if state.message is not DisplayMessage.READY:
            self._progress("MGF active: confirming process temperature/hold")
            if temp_override_f is not None:
                self._press(Key.UP)  # stop flashing
                self._dial_numeric(temp_override_f)
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
        self._press(Key.ENTER)
        self._wait_for_message(DisplayMessage.ON)

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
            self._dial_numeric(seg.rate_f_per_hr, tolerance=1.0)
            self._press(Key.ENTER)

            self._wait_for(
                lambda s: s.numeric_value is not None, description="temperature prompt"
            )
            self._dial_numeric(seg.target_temp_f, tolerance=1.0)
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
        self._press(Key.ENTER)
        self._wait_for_message(DisplayMessage.ON)

    def load_program(self, slot: int, start: bool = True) -> None:
        if not 1 <= slot <= 5:
            raise ValueError(f"slot must be 1-5, got {slot}")
        self._progress(f"loading Ramp & Hold slot {slot}")
        self.select_mode(Mode.LOAD)
        self._dial_numeric(float(slot), tolerance=0.01)
        self._press(Key.ENTER)
        self._wait_for_message(DisplayMessage.READY)
        if start:
            self._press(Key.ENTER)
            self._wait_for_message(DisplayMessage.ON)

    # -- Profile entry point --------------------------------------------------

    def run_profile(self, profile: Profile) -> None:
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

    def toggle_mgf(self) -> None:
        self._progress("toggling Modify GlassFire mode")
        self.select_mode(Mode.OPTIONS)
        self._scroll_to(DisplayMessage.MGF)
        self._press(Key.ENTER)
        self._press(Key.UP)  # toggle current value
        self._press(Key.ENTER)
        self.goto_idle()

    def set_temperature_scale(self, celsius: bool) -> None:
        self._progress(f"setting temperature scale: {'C' if celsius else 'F'}")
        self.select_mode(Mode.OPTIONS)
        self._scroll_to(DisplayMessage.CHANGE_SCALE)
        self._press(Key.ENTER)
        target = DisplayMessage.DEG_C if celsius else DisplayMessage.DEG_F
        self._scroll_to(target)
        self._press(Key.ENTER)
        self.goto_idle()

    def factory_reset(self) -> None:
        """WIPES stored PRO programs 1-4 and restores the factory casting
        program to slot 5 (p.22). Callers (UI) must get explicit human
        confirmation before invoking this — the sequencer itself does not
        prompt."""

        self._progress("factory reset")
        self.select_mode(Mode.OPTIONS)
        self._scroll_to(DisplayMessage.RESET)
        self._press(Key.ENTER)
        self.level = ProgrammingLevel.BEGINNER
        self.goto_idle()

    def stop(self) -> None:
        """A plain (short) ENTER press stops a running program (p.10, p.12:
        'StOP' briefly shown). Distinct from goto_idle's 4-second hold,
        which is the unconditional escape hatch."""

        self._progress("stopping")
        self._press(Key.ENTER)

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

        for _ in range(increments):
            self._progress("add temperature (+5°F)")
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
