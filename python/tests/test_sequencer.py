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
    sequencer, bridge = make_sequencer(["IdLE", "AUto"])
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
