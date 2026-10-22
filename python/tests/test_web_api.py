"""Tests for web/api.py's route handlers against a fake ui double — no App
Lab runtime, no browser, no Uno Q. Confirms the *logic* each route runs;
the brick's own path-param/body-binding behavior is confirmed separately
against real hardware (see docs/architecture.md), not re-tested here.
"""

import time

from kiln.bridge import MockBridge
from kiln.profiles import LIBRARY_DIR, ProfileLibrary
from kiln.protocol import MAX_USER_PROFILES
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
    vision = MockVision(script=["IdLE", "AUto", "SLo", "FULL", "rEdI", "ON"])
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


def make_custom(ui, id_="custom"):
    return ui.call(
        "POST",
        "/api/profiles",
        body={
            "id": id_,
            "name": id_,
            "description": "",
            "segments": [{"rate_f_per_hr": 200, "target_temp_f": 1000, "hold_minutes": 10}],
        },
    )


def test_activate_and_deactivate_profile(tmp_path):
    ui, *_ = make_registered(tmp_path)
    make_custom(ui, "custom")

    activated = ui.call("POST", "/api/profiles/{profile_id}/activate", profile_id="custom", body={"slot": 1})
    assert activated["pro_slot"] == 1

    deactivated = ui.call("POST", "/api/profiles/{profile_id}/deactivate", profile_id="custom")
    assert deactivated["pro_slot"] is None


def test_activate_swaps_out_previous_holder(tmp_path):
    ui, *_ = make_registered(tmp_path)
    make_custom(ui, "a")
    make_custom(ui, "b")

    ui.call("POST", "/api/profiles/{profile_id}/activate", profile_id="a", body={"slot": 1})
    ui.call("POST", "/api/profiles/{profile_id}/activate", profile_id="b", body={"slot": 1})

    a = ui.call("GET", "/api/profiles/{profile_id}", profile_id="a")
    b = ui.call("GET", "/api/profiles/{profile_id}", profile_id="b")
    assert a["pro_slot"] is None
    assert b["pro_slot"] == 1


def test_activate_rejects_glassfire_profile(tmp_path):
    ui, *_ = make_registered(tmp_path)
    result = ui.call(
        "POST", "/api/profiles/{profile_id}/activate", profile_id="full_fuse_slow", body={"slot": 1}
    )
    assert "error" in result


def test_set_mgf_override_via_api(tmp_path):
    ui, *_ = make_registered(tmp_path)
    result = ui.call(
        "POST",
        "/api/profiles/{profile_id}/mgf_override",
        profile_id="full_fuse_slow",
        body={"temp_override_f": 1400, "hold_override_minutes": 25},
    )
    assert result["temp_override_f"] == 1400
    assert result["segments"][1]["target_temp_f"] == 1400
    assert result["source"] == "user"

    # Clearing (both null) reverts to the factory default.
    cleared = ui.call(
        "POST",
        "/api/profiles/{profile_id}/mgf_override",
        profile_id="full_fuse_slow",
        body={"temp_override_f": None, "hold_override_minutes": None},
    )
    assert cleared["temp_override_f"] is None
    assert cleared["segments"][1]["target_temp_f"] == 1465


def test_save_profile_rejects_beyond_cap(tmp_path):
    ui, *_ = make_registered(tmp_path)
    for i in range(MAX_USER_PROFILES):
        result = make_custom(ui, f"p{i}")
        assert "error" not in result
    result = make_custom(ui, "one_too_many")
    assert "error" in result
