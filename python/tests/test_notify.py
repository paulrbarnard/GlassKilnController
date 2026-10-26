"""Push notifications (kiln/notify.py) and when the app sends them."""

import json

from kiln.notify import Notifier


def make(tmp_path, keys=True):
    path = tmp_path / "notify.json"
    if keys:
        path.write_text(json.dumps({"pushover_token": "tok", "pushover_user": "usr"}))
    sent = []
    return Notifier(path, post=sent.append), sent


def test_sends_with_both_keys(tmp_path):
    notifier, sent = make(tmp_path)
    notifier.send("Kiln safe to open", "39°C at 14:25", wait=True)
    assert sent == [{"token": "tok", "user": "usr", "title": "Kiln safe to open", "message": "39°C at 14:25", "priority": "0"}]
    assert notifier.last["ok"] is True


def test_sends_nothing_without_keys(tmp_path):
    notifier, sent = make(tmp_path, keys=False)
    notifier.send("Kiln safe to open", "39°C", wait=True)
    assert sent == [] and not notifier.configured()


def test_a_failed_push_is_recorded_not_raised(tmp_path):
    def fail(_fields):
        raise RuntimeError("application token is invalid")

    notifier = Notifier(tmp_path / "notify.json", post=fail)
    (tmp_path / "notify.json").write_text(json.dumps({"pushover_token": "t", "pushover_user": "u"}))
    notifier.send("x", "y", wait=True)
    assert notifier.last == {"time": notifier.last["time"], "ok": False, "detail": "application token is invalid"}


def test_safe_to_open_is_pushed_once_when_a_hot_kiln_cools(monkeypatch):
    import kiln.kiln_temp as kt
    from kiln.kiln_temp import KilnTemperature
    from kiln.vision import MockVision

    monkeypatch.setattr(kt, "READ_WINDOW_S", 0.05)
    safe = []
    reader = KilnTemperature(
        MockVision(script=["  17."]), publish=lambda _: None, celsius=lambda: True,
        busy=lambda: False, firing_temp=lambda: None, on_safe=safe.append,
    )
    reader.poll()
    assert safe == []  # already cool when the app started: nothing to say
    for text in ("  45.", "  41.", "  39.", "  38."):
        reader._vision.script, reader._vision._index = [text], 0
        reader.poll()
    assert [s["temp"] for s in safe] == [39.0]


def test_firing_end_pushes(tmp_path, monkeypatch):
    import web.api as api
    from tests.test_web_api import FakeUI
    from kiln.bridge import MockBridge
    from kiln.profiles import LIBRARY_DIR, ProfileLibrary
    from kiln.sequencer import Sequencer
    from kiln.vision import MockVision

    sent = []
    monkeypatch.setattr(api, "Notifier", lambda path: type("N", (), {"send": lambda self, t, m, priority=0: sent.append((t, m, priority))})())
    sequencer = Sequencer(bridge=MockBridge(press_delay_s=0.0), vision=MockVision(script=["IdLE"]))
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    ui = FakeUI()
    api.register_routes(ui, sequencer, library, settings_path=tmp_path / "settings.json")
    # Drive the firing monitor's publish hook (where the push is decided)
    # with the statuses a firing goes through.
    import gc
    from kiln.firing import FiringMonitor

    monitor = next(o for o in gc.get_objects() if isinstance(o, FiringMonitor) and o._publish.__name__ == "publish_firing")
    base = {"profile_name": "Full fuse", "profile_id": "full_fuse_slow", "unit": "C", "fired_at": 1.0, "ended_at": 1793022095.0, "message": ""}
    monitor._publish({**base, "phase": "programming", "temp": None})
    monitor._publish({**base, "phase": "firing", "temp": 21})
    monitor._publish({**base, "phase": "firing", "temp": 300})  # still firing: no second push
    started = [s for s in sent if s[0].startswith("Firing started")]
    assert len(started) == 1 and "at 21°C" in started[0][1] and "Expected to finish about" in started[0][1]
    sent.clear()
    monitor._publish({**base, "phase": "firing", "temp": 790})
    monitor._publish({**base, "phase": "complete", "temp": 790})
    monitor._publish({**base, "phase": "complete", "temp": 780})  # repeated status: no second push
    assert len(sent) == 1
    title, message, priority = sent[0]
    assert title == "Firing complete: Full fuse" and "kiln 790°C" in message and "Not safe to open yet" in message
    monitor._publish({**base, "phase": "programming", "fired_at": None, "temp": None})
    monitor._publish({**base, "phase": "error", "fired_at": None, "temp": None, "message": "couldn't set the value"})
    assert sent[-1][0] == "Kiln programming failed" and sent[-1][2] == 1


def test_profile_hours_counts_ramps_from_the_kiln_and_holds():
    from kiln.protocol import RampHoldSegment
    from web.api import profile_hours

    segments = [RampHoldSegment(rate_f_per_hr=300, target_temp_f=1000, hold_minutes=30),
                RampHoldSegment(rate_f_per_hr=500, target_temp_f=500, hold_minutes=0)]
    assert profile_hours(segments, start_f=100) == 900 / 300 + 0.5 + 500 / 500
