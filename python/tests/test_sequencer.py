"""Sequencer tests against MockBridge/MockVision — verifies the exact
button-press sequence a given controller interaction produces, with no
camera, kiln, or Uno Q involved. See docs/architecture.md "Testability".

These double as executable documentation of docs/controller_protocol.md:
each test's ``script`` is the sequence of display states the manual says to
expect, in order.
"""

from kiln.bridge import MockBridge
from kiln.protocol import Key, LONG_PRESS_SECONDS, Mode, Process, SHORT_PRESS_MS, Speed
from kiln.sequencer import Sequencer
from kiln.vision import MockVision

LONG_MS = int(LONG_PRESS_SECONDS * 1000)


def make_sequencer(script: list[str]) -> tuple[Sequencer, MockBridge]:
    bridge = MockBridge()
    vision = MockVision(script=script)
    return Sequencer(bridge=bridge, vision=vision), bridge


def test_select_mode_auto_when_already_showing_auto():
    # Pressing ENTER from IdLE shows the current mode (p.9); if it's
    # already "AUto", no scrolling is needed before selecting it.
    sequencer, bridge = make_sequencer(["IdLE", "AUto", "FA5t"])
    sequencer.select_mode(Mode.AUTO)
    assert bridge.presses == [
        # already at IdLE: no long hold to get there
        (Key.ENTER, SHORT_PRESS_MS),  # open mode menu
        (Key.ENTER, SHORT_PRESS_MS),  # select AUTO
    ]


def test_run_glassfire_full_fuse_slow_no_mgf():
    # Exact walkthrough from docs/controller_protocol.md "Run a GlassFire
    # (AUTO) program", assuming MGF is off (straight to rEdI) and the
    # controller already shows the target speed/process on first read.
    sequencer, bridge = make_sequencer(["IdLE", "AUto", "SLo", "FULL", "rEdI", "ON"])
    sequencer.run_glassfire(Speed.SLOW, Process.FULL_FUSE)
    assert bridge.presses == [
        (Key.ENTER, SHORT_PRESS_MS),  # open mode menu (already at IdLE)
        (Key.ENTER, SHORT_PRESS_MS),  # select AUTO
        (Key.ENTER, SHORT_PRESS_MS),  # select speed SLo
        (Key.ENTER, SHORT_PRESS_MS),  # select process FULL
        (Key.ENTER, SHORT_PRESS_MS),  # start (from rEdI)
    ]


def test_run_glassfire_scrolls_when_not_already_on_target():
    # Controller currently on PRO / MEd / SLP; must scroll to AUTO / FASt /
    # FULL before selecting each.
    sequencer, bridge = make_sequencer(
        [
            "IdLE",
            "Pro",
            "AUto",  # one UP scroll: Pro -> Auto
            "MEd",
            "FASt",  # one UP scroll: MEd -> FASt
            "SLP",
            "FULL",  # one UP scroll: SLP -> FULL
            "rEdI",
            "ON",
        ]
    )
    sequencer.run_glassfire(Speed.FAST, Process.FULL_FUSE)
    assert bridge.presses == [
        (Key.ENTER, SHORT_PRESS_MS),  # open mode menu (shows Pro)
        (Key.UP, SHORT_PRESS_MS),  # scroll Pro -> Auto
        (Key.ENTER, SHORT_PRESS_MS),  # select AUTO
        (Key.UP, SHORT_PRESS_MS),  # scroll MEd -> FASt
        (Key.ENTER, SHORT_PRESS_MS),  # select FASt
        (Key.UP, SHORT_PRESS_MS),  # scroll SLP -> FULL
        (Key.ENTER, SHORT_PRESS_MS),  # select FULL
        (Key.ENTER, SHORT_PRESS_MS),  # start
    ]


def test_goto_idle_clears_power_failure():
    # p.30: PF on connect must be cleared with ENTER before IdLE is
    # trustworthy.
    class PowerFailed:
        """Shows PF until ENTER is tapped, as the controller does."""

        def __init__(self):
            self.screen, self.presses = "PF", []

        def press(self, key, hold_ms=SHORT_PRESS_MS):
            self.presses.append((key, hold_ms))
            if key is Key.ENTER and hold_ms < LONG_MS:
                self.screen = "IdLE"

        def hold_long(self, key):
            self.press(key, LONG_MS)

        def read(self):
            from kiln.vision import build_controller_state

            return build_controller_state(self.screen)

        def wait_for(self, predicate, timeout_s, poll_interval_s=0.0):
            state = self.read()
            return state if predicate(state) else None

    fake = PowerFailed()
    Sequencer(bridge=fake, vision=fake).goto_idle()
    assert fake.presses == [
        (Key.ENTER, LONG_MS),  # not at IdLE, so hold ENTER
        (Key.ENTER, SHORT_PRESS_MS),  # clear PF
    ]


def test_goto_idle_skips_the_long_hold_when_already_idle():
    sequencer, bridge = make_sequencer(["IdLE"])
    sequencer.goto_idle()
    assert bridge.presses == []


class _MenuController:
    """Bridge + vision fake for one cyclic menu that ignores chosen presses,
    like the membrane buttons did on the kiln (2026-10-22)."""

    def __init__(self, options: list[str], ignore_presses: set[int]):
        self.options, self.ignore, self.index, self.presses = options, ignore_presses, 0, []

    # bridge side
    def press(self, key: Key, hold_ms: int = SHORT_PRESS_MS) -> None:
        self.presses.append(key)
        if len(self.presses) not in self.ignore and key is Key.UP:
            self.index = (self.index + 1) % len(self.options)

    def ping(self) -> bool:
        return True

    # vision side
    def read(self):
        from kiln.vision import build_controller_state

        return build_controller_state(self.options[self.index])

    def wait_for(self, predicate, timeout_s, poll_interval_s=0.0):
        state = self.read()
        return state if predicate(state) else None


def test_scroll_presses_again_when_a_press_does_not_register(monkeypatch):
    import kiln.sequencer as seq
    from kiln.protocol import DisplayMessage

    monkeypatch.setattr(seq, "MENU_CHANGE_TIMEOUT_S", 0.0)
    fake = _MenuController(["Pro", "LoAd", "0Pt", "AUto"], ignore_presses={1})
    sequencer = Sequencer(bridge=fake, vision=fake)

    sequencer._scroll_to(DisplayMessage.OPTIONS)

    # First UP was ignored and retried; then one more UP: Pro -> LoAd -> 0Pt.
    assert fake.presses == [Key.UP, Key.UP, Key.UP]
    assert fake.read().message is DisplayMessage.OPTIONS


def test_temperatures_and_rates_are_dialled_in_the_controller_unit():
    sequencer, _ = make_sequencer(["IdLE"])
    sequencer.controller_celsius = True
    assert sequencer._controller_temp(1465.0) == 796.0  # Full Fuse
    assert sequencer._controller_rate(600.0) == 333.0  # Medium segment 2
    sequencer.controller_celsius = False
    assert sequencer._controller_temp(1465.0) == 1465.0
    assert sequencer._controller_rate(600.0) == 600.0


def test_options_actions_refuse_unless_idle():
    import pytest
    from kiln.sequencer import SequenceError

    # Mid-firing the display shows temperatures, never IdLE.
    sequencer, bridge = make_sequencer(["  796."])
    sequencer.vision.wait_for = lambda predicate, timeout_s, poll_interval_s=0.15: None
    with pytest.raises(SequenceError, match="seems to be firing"):
        sequencer.set_temperature_scale(celsius=True)
    assert bridge.presses == []  # nothing pressed, so no hold-ENTER stop


def test_select_mode_unlocks_only_when_the_menu_will_not_scroll(monkeypatch):
    import kiln.sequencer as seq
    from kiln.protocol import DisplayMessage, ProgrammingLevel

    monkeypatch.setattr(seq, "MENU_CHANGE_TIMEOUT_S", 0.0)

    class Beginner(_MenuController):
        """Only AUto until UP is held at IdLE, then the full loop."""

        def __init__(self):
            super().__init__(["IdLE"], ignore_presses=set())
            self.unlocked = False

        def press(self, key, hold_ms=SHORT_PRESS_MS):
            self.presses.append((key, hold_ms))
            if hold_ms >= LONG_MS:
                if key is Key.ENTER:
                    self.options, self.index = ["IdLE"], 0
                elif key is Key.UP:
                    self.unlocked = True
            elif key is Key.ENTER and self.options == ["IdLE"]:
                self.options = ["Pro", "LoAd", "0Pt", "AUto"] if self.unlocked else ["AUto"]
                self.index = 0
            elif key is Key.ENTER:  # select the mode: on to its first screen
                self.options, self.index = ["   1."], 0
            elif key is Key.UP:
                self.index = (self.index + 1) % len(self.options)

        def hold_long(self, key):
            self.press(key, LONG_MS)

    fake = Beginner()
    sequencer = Sequencer(bridge=fake, vision=fake)
    sequencer.select_mode(seq.Mode.LOAD)
    assert (Key.UP, LONG_MS) in fake.presses  # had to unlock
    assert sequencer.level is ProgrammingLevel.ADVANCED

    fake.presses.clear()
    sequencer.select_mode(seq.Mode.LOAD)
    assert (Key.UP, LONG_MS) not in fake.presses  # already Advanced: no unlock


class _ScreenController:
    """Bridge + vision fake: ENTER moves through ``screens`` in order, except
    on the press numbers in ``ignore`` (as if the membrane missed them)."""

    def __init__(self, screens, ignore=()):
        self.screens, self.ignore, self.i, self.presses = screens, set(ignore), 0, []

    def press(self, key, hold_ms=SHORT_PRESS_MS):
        self.presses.append(key)
        if key is Key.ENTER and len(self.presses) not in self.ignore:
            self.i = min(self.i + 1, len(self.screens) - 1)

    def ping(self):
        return True

    def read(self):
        from kiln.vision import build_controller_state

        return build_controller_state(self.screens[self.i])

    def wait_for(self, predicate, timeout_s, poll_interval_s=0.0):
        state = self.read()
        return state if predicate(state) else None


def test_enter_is_pressed_again_only_while_the_screen_has_not_changed(monkeypatch):
    import kiln.sequencer as seq
    from kiln.protocol import DisplayMessage

    monkeypatch.setattr(seq, "MENU_CHANGE_TIMEOUT_S", 0.0)
    fake = _ScreenController(["1dLE.", "Pro ."], ignore={1})
    sequencer = Sequencer(bridge=fake, vision=fake)
    state = sequencer._enter_from(DisplayMessage.IDLE, accept=lambda s: s.message is not None and s.message is not DisplayMessage.IDLE)
    assert state.message is DisplayMessage.PRO
    assert fake.presses == [Key.ENTER, Key.ENTER]


def test_start_is_never_pressed_again_once_firing(monkeypatch):
    import pytest

    import kiln.sequencer as seq
    from kiln.sequencer import SequenceError

    monkeypatch.setattr(seq, "DEFAULT_TIMEOUT_S", 0.0)
    # rEdI -> first ENTER missed -> second ENTER starts it: two presses.
    fake = _ScreenController(["rEd1.", "  25."], ignore={1})
    sequencer = Sequencer(bridge=fake, vision=fake)
    sequencer._start_from_ready()
    assert fake.presses == [Key.ENTER, Key.ENTER]

    # Something other than rEdI or a temperature after ENTER (here StOP):
    # no second press, report it instead.
    fake = _ScreenController(["rEd1.", "5t0P."])
    sequencer = Sequencer(bridge=fake, vision=fake)
    with pytest.raises(SequenceError):
        sequencer._start_from_ready()
    assert fake.presses == [Key.ENTER]


def test_firing_is_refused_when_the_controller_unit_differs():
    import pytest

    from kiln.sequencer import SequenceError

    # App in °C, controller in °F (no °C dot): don't program anything.
    sequencer, bridge = make_sequencer(["1dLE"])
    sequencer.controller_celsius = True
    with pytest.raises(SequenceError, match="controller is in °F but the app is set to °C"):
        sequencer.check_controller_unit()
    assert bridge.presses == []

    sequencer, _ = make_sequencer(["1dLE."])
    sequencer.controller_celsius = True
    sequencer.check_controller_unit()  # matches: no error


class _ValueEditor:
    """One PRO value being edited: a tap moves it by 1; a held press scrolls
    it along the curve recorded on the kiln, on a simulated clock (each
    reading advances it by one camera frame); ``miss`` lists tap numbers
    to ignore. Release takes effect RELEASE_LAG_S later, as on the kiln."""

    FRAME_S = 1 / 30

    def __init__(self, value, scale=1, miss=()):
        self.value, self.scale, self.miss, self.presses, self.taps = value, scale, set(miss), [], 0
        self.now, self.hold = 0.0, None

    def clock(self):
        return self.now

    def press(self, key, hold_ms=SHORT_PRESS_MS):
        self.presses.append((key, hold_ms))
        self.taps += 1
        if self.taps not in self.miss:
            self.value += 1 if key is Key.UP else -1

    def hold_start(self, key, max_ms):
        self.presses.append((key, -max_ms))
        self.hold = (key, self.now, self.value)

    def _held_value(self, at):
        import kiln.sequencer as seq

        key, started, base = self.hold
        sign = 1 if key is Key.UP else -1
        return base + sign * round(_curve_steps(seq.SCROLL_CURVE, at - started))

    def hold_release(self):
        import kiln.sequencer as seq

        self.value = self._held_value(self.now + seq.RELEASE_LAG_S)
        self.hold = None

    def ping(self):
        return True

    def read(self):
        from kiln.vision import build_controller_state

        value = self._held_value(self.now) if self.hold else self.value
        shown = f"{value / self.scale:05.2f}." if self.scale == 100 else f"{value:04d}."
        return build_controller_state(shown)

    def read_fast(self):
        self.now += self.FRAME_S
        return self.read()

    def wait_for(self, predicate, timeout_s, poll_interval_s=0.0):
        state = self.read()
        return state if predicate(state) else None


def _curve_steps(curve, seconds):
    if seconds <= curve[0][0]:
        return 0
    for (t0, n0), (t1, n1) in zip(curve, curve[1:]):
        if seconds <= t1:
            return n0 + (n1 - n0) * (seconds - t0) / (t1 - t0)
    return curve[-1][1] + 300 * (seconds - curve[-1][0])


def _editing(monkeypatch, value, scale=1, miss=()):
    import kiln.sequencer as seq

    fake = _ValueEditor(value, scale, miss)
    monkeypatch.setattr(seq, "TAP_GAP_S", 0.0)
    monkeypatch.setattr(seq, "SCROLL_SETTLE_S", 0.0)
    monkeypatch.setattr(seq, "MENU_CHANGE_TIMEOUT_S", 0.0)
    monkeypatch.setattr(seq.time, "monotonic", fake.clock)
    return Sequencer(bridge=fake, vision=fake), fake


def test_set_value_scrolls_big_changes_then_taps(monkeypatch):
    sequencer, fake = _editing(monkeypatch, 60)
    sequencer._set_value(650)  # e.g. 60 -> 650 °C
    assert fake.value == 650
    holds = [p for p in fake.presses if p[1] < 0]
    taps = [p for p in fake.presses if p[1] > 0]
    assert len(holds) <= 2  # watched holds cover nearly all of it
    assert len(taps) <= 4  # and land within a few steps


def test_set_value_watched_hold_lands_close_on_a_long_scroll(monkeypatch):
    sequencer, fake = _editing(monkeypatch, 900)
    sequencer._set_value(222)  # -678, the longest scroll recorded on the kiln
    assert fake.value == 222
    assert len([p for p in fake.presses if p[1] < 0]) <= 2


def test_set_value_comes_back_down_and_survives_missed_taps(monkeypatch):
    sequencer, fake = _editing(monkeypatch, 433, miss={1, 3})
    sequencer._set_value(425)  # small change: taps only, two of them missed
    assert fake.value == 425
    assert all(p[1] <= 1000 and p[0] is Key.DOWN for p in fake.presses)


def test_set_value_holds_are_hours_and_minutes(monkeypatch):
    sequencer, fake = _editing(monkeypatch, 1200, scale=100)  # showing 12.00
    sequencer._set_value(30, scale=100)  # 0 h 30 min
    assert fake.value == 30


def test_a_camera_failure_is_reported_as_such():
    import pytest

    from kiln.sequencer import SequenceError

    sequencer, bridge = make_sequencer(["?"])  # every read fails
    with pytest.raises(SequenceError, match="can't see the kiln display"):
        sequencer._require_idle("writing a program")
    assert bridge.presses == []


def test_left_in_a_menu_is_returned_to_idle_automatically():
    class InMenu:
        """Showing a menu ("0Pt") until ENTER is held, then IdLE."""

        def __init__(self):
            self.screen, self.presses = "0Pt .", []

        def press(self, key, hold_ms=SHORT_PRESS_MS):
            self.presses.append((key, hold_ms))
            if key is Key.ENTER and hold_ms >= LONG_MS:
                self.screen = "1dLE."

        def hold_long(self, key):
            self.press(key, LONG_MS)

        def ping(self):
            return True

        def read(self):
            from kiln.vision import build_controller_state

            return build_controller_state(self.screen)

        def wait_for(self, predicate, timeout_s, poll_interval_s=0.0):
            state = self.read()
            return state if predicate(state) else None

    fake = InMenu()
    Sequencer(bridge=fake, vision=fake)._require_idle("starting")
    assert fake.presses == [(Key.ENTER, LONG_MS)]
    assert fake.screen == "1dLE."
