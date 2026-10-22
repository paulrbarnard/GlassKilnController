"""Firing progress and STOP: the monitor's phases, and the API's stop
aborting a programming sequence before confirming the controller stopped."""

import time

import kiln.firing as firing
import kiln.sequencer as seq
from kiln.bridge import MockBridge
from kiln.firing import FiringMonitor
from kiln.profiles import ProfileLibrary
from kiln.protocol import Key, LONG_PRESS_SECONDS
from kiln.sequencer import Sequencer
from kiln.vision import KilnVision, build_controller_state
from web.api import register_routes

from tests.test_web_api import LIBRARY_DIR, FakeUI

LONG_MS = int(LONG_PRESS_SECONDS * 1000)


class ScriptedVision(KilnVision):
    """Returns each scripted display text in turn, then repeats the last."""

    def __init__(self, script):
        self.script, self.i = list(script), 0

    def read(self):
        text = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return build_controller_state(text)


def _wait(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def _monitor(script, monkeypatch):
    monkeypatch.setattr(firing, "TRACE_EVERY_S", 0.0)
    published = []
    monitor = FiringMonitor(ScriptedVision(script), published.append, poll_s=0.01)
    monitor.begin("p", "Profile")
    monitor.firing_started()
    return monitor, published


def test_monitor_tracks_temperature_until_complete(monkeypatch):
    monitor, published = _monitor(["  25.", "  30.", "CPLt"], monkeypatch)
    assert _wait(lambda: monitor.status()["phase"] == "complete")
    status = monitor.status()
    assert status["temp"] == 30.0
    assert [round(f) for _, f in status["trace"]] == [77, 86]  # °C 25, 30 as °F
    assert status["ended_at"] is not None
    assert not monitor.active
    phases = [p["phase"] for p in published]
    assert phases[0] == "programming" and "firing" in phases and phases[-1] == "complete"


def test_monitor_reports_controller_errors(monkeypatch):
    monitor, _ = _monitor(["  500.", "Err1"], monkeypatch)
    assert _wait(lambda: monitor.status()["phase"] == "error")
    assert "Err1" in monitor.status()["message"]


def test_monitor_notices_a_stop_at_the_kiln(monkeypatch):
    monitor, _ = _monitor(["  500.", "1dLE."], monkeypatch)
    assert _wait(lambda: monitor.status()["phase"] == "ended")


class IdleController:
    """Always shows IdLE, so programming stalls at the mode menu: the UP
    presses never change anything, as if they weren't registering."""

    def __init__(self):
        self.bridge = MockBridge(press_delay_s=0.0)
        self.vision = ScriptedVision(["1dLE."])


def test_stop_aborts_programming_then_confirms(tmp_path, monkeypatch):
    monkeypatch.setattr(seq, "MENU_CHANGE_TIMEOUT_S", 0.5)
    kiln = IdleController()
    sequencer = Sequencer(bridge=kiln.bridge, vision=kiln.vision)
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    ui = FakeUI()
    register_routes(ui, sequencer, library, settings_path=tmp_path / "s.json")

    assert ui.call("POST", "/api/run/{profile_id}", profile_id="full_fuse_slow")["ok"]
    assert _wait(lambda: any(k is Key.UP for k, _ in kiln.bridge.presses))  # programming
    assert ui.call("POST", "/api/stop")["ok"]
    assert _wait(lambda: ui.call("GET", "/api/firing")["phase"] == "stopped", 10.0)

    # The stop's own hold of ENTER comes after the last programming press.
    presses = kiln.bridge.presses
    last_up = max(i for i, (k, _) in enumerate(presses) if k is Key.UP)
    assert presses[-1] == (Key.ENTER, LONG_MS) and len(presses) - 1 > last_up
    assert ("firing", ui.call("GET", "/api/firing")) in ui.messages


def test_run_is_refused_while_a_firing_is_active(tmp_path):
    kiln = IdleController()
    sequencer = Sequencer(bridge=kiln.bridge, vision=kiln.vision)
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    ui = FakeUI()
    register_routes(ui, sequencer, library, settings_path=tmp_path / "s.json")
    assert ui.call("POST", "/api/run/{profile_id}", profile_id="full_fuse_slow")["ok"]
    second = ui.call("POST", "/api/run/{profile_id}", profile_id="full_fuse_slow")
    assert "already in progress" in second["error"]
    ui.call("POST", "/api/stop")
    assert _wait(lambda: not ui.call("GET", "/api/firing")["phase"] in ("programming", "stopping"), 15.0)
