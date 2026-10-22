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
        (Key.ENTER, LONG_MS),  # goto_idle's long hold
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
        (Key.ENTER, LONG_MS),  # goto_idle
        (Key.ENTER, SHORT_PRESS_MS),  # open mode menu
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
        (Key.ENTER, LONG_MS),
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
    sequencer, bridge = make_sequencer(["PF", "IdLE"])
    sequencer.goto_idle()
    assert bridge.presses == [
        (Key.ENTER, LONG_MS),
        (Key.ENTER, SHORT_PRESS_MS),  # clear PF
    ]


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
    with pytest.raises(SequenceError, match="isn't showing IdLE"):
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
