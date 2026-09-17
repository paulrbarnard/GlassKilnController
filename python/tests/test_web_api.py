"""Tests for web/api.py's route handlers against a fake ui double — no App
Lab runtime, no browser, no Uno Q. Confirms the *logic* each route runs;
the brick's own path-param/body-binding behavior is confirmed separately
against real hardware (see docs/architecture.md), not re-tested here.
"""

import time

from kiln.bridge import MockBridge
from kiln.profiles import LIBRARY_DIR, ProfileLibrary
from kiln.sequencer import Sequencer
from kiln.vision import MockVision
from web.api import register_routes


class FakeUI:
    def __init__(self):
        self.routes = {}
        self.messages = []

    def expose_api(self, method, path, handler):
        self.routes[(method, path)] = handler

    def send_message(self, event, data):
        self.messages.append((event, data))

    def call(self, method, path, **kwargs):
        return self.routes[(method, path)](**kwargs)


def make_registered(tmp_path, script=None):
    bridge = MockBridge(press_delay_s=0.0)
    vision = MockVision(script=script or ["IdLE"])
    sequencer = Sequencer(bridge=bridge, vision=vision)
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    ui = FakeUI()
    register_routes(ui, sequencer, library)
    return ui, sequencer, library, bridge


def test_list_profiles_includes_builtins(tmp_path):
    ui, *_ = make_registered(tmp_path)
    result = ui.call("GET", "/api/profiles")
    ids = {p["id"] for p in result["profiles"]}
    assert "full_fuse_slow" in ids
    assert "casting_factory" in ids


def test_get_profile_unknown_returns_error_not_exception(tmp_path):
    ui, *_ = make_registered(tmp_path)
    result = ui.call("GET", "/api/profiles/{profile_id}", profile_id="does_not_exist")
    assert "error" in result


def test_save_get_delete_user_profile_round_trip(tmp_path):
    ui, _, library, _ = make_registered(tmp_path)
    body = {
        "id": "my_custom",
        "name": "My Custom",
        "description": "test profile",
        "segments": [
            {"rate_f_per_hr": 300, "target_temp_f": 1000, "hold_minutes": 10},
        ],
    }
    saved = ui.call("POST", "/api/profiles", body=body)
    assert saved["id"] == "my_custom"
    assert saved["segments"][0]["target_temp_f"] == 1000

    fetched = ui.call("GET", "/api/profiles/{profile_id}", profile_id="my_custom")
    assert fetched["name"] == "My Custom"
    assert fetched["source"] == "user"

    deleted = ui.call("DELETE", "/api/profiles/{profile_id}", profile_id="my_custom")
    assert deleted == {"ok": True}
    assert ui.call("GET", "/api/profiles/{profile_id}", profile_id="my_custom") == {
        "error": "no such profile: my_custom"
    }


def test_run_profile_unknown_id_returns_error(tmp_path):
    ui, *_ = make_registered(tmp_path)
    result = ui.call("POST", "/api/run/{profile_id}", profile_id="nope")
    assert "error" in result


def test_run_profile_rejects_concurrent_start(tmp_path):
    # press_delay_s=1.0 guarantees the background thread is still mid-flight
    # when the second call below executes on the main thread.
    bridge = MockBridge(press_delay_s=1.0)
    vision = MockVision(script=["IdLE", "Auto", "SLo", "FULL", "rEdI", "ON"])
    sequencer = Sequencer(bridge=bridge, vision=vision)
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    ui = FakeUI()
    register_routes(ui, sequencer, library)

    first = ui.call("POST", "/api/run/{profile_id}", profile_id="full_fuse_slow")
    assert first["ok"] is True
    second = ui.call("POST", "/api/run/{profile_id}", profile_id="full_fuse_slow")
    assert "already in progress" in second["error"]

    # Let the background thread finish so it doesn't outlive the test process.
    time.sleep(1.5)


def test_status_reports_bridge_and_running_state(tmp_path):
    ui, *_ = make_registered(tmp_path)
    result = ui.call("GET", "/api/status")
    assert result == {"bridge_ok": True, "running": False}
