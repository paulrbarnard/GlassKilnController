import pytest


@pytest.fixture(autouse=True)
def _private_kiln_slot_map(tmp_path, monkeypatch):
    """Tests never touch the board's real slot map (python/state/)."""

    monkeypatch.setattr("web.api.SLOTS_PATH", tmp_path / "kiln_slots.json")
    monkeypatch.setattr("web.api.ROOM_PATH", tmp_path / "kiln_room.json")
    monkeypatch.setattr("web.api.COOLING_LOG_DIR", tmp_path / "cooling")
    monkeypatch.setattr("web.api.NOTIFY_PATH", tmp_path / "notify.json")  # no keys: nothing sent
