"""The home screen's kiln temperature (kiln/kiln_temp.py)."""

import pytest

import kiln.kiln_temp as kt
from kiln.kiln_temp import KilnTemperature, is_hot
from kiln.vision import MockVision


@pytest.fixture(autouse=True)
def _short_read_window(monkeypatch):
    monkeypatch.setattr(kt, "READ_WINDOW_S", 0.05)


def make(script, busy=False, firing_temp=None, celsius=True):
    published = []
    reader = KilnTemperature(
        MockVision(script=script),
        publish=published.append,
        celsius=lambda: celsius,
        busy=lambda: busy,
        firing_temp=lambda: firing_temp,
    )
    return reader, published


def test_reads_the_temperature_between_idles():
    reader, published = make(["IdLE", "IdLE", "  31.", "IdLE"])
    assert reader.poll() == 31.0
    status = reader.status()
    assert status["temp"] == 31.0 and status["unit"] == "C" and status["hot"] is False
    assert published == [status]


def test_hot_is_40c_or_104f():
    assert is_hot(40.0, celsius=True) and not is_hot(39.0, celsius=True)
    assert is_hot(104.0, celsius=False) and not is_hot(102.0, celsius=False)
    reader, _ = make(["  612."])
    reader.poll()
    assert reader.status()["hot"] is True


def test_in_fahrenheit():
    reader, _ = make(["  98."], celsius=False)
    reader.poll()
    status = reader.status()
    assert (status["temp"], status["unit"], status["hot"], status["off"]) == (98.0, "F", False, False)


def test_stands_aside_while_the_controller_is_busy():
    # A sequence or firing has the camera: use the firing monitor's reading
    # and don't read the display at all.
    reader, _ = make(["  31."], busy=True, firing_temp=550.0)
    assert reader.poll() == 550.0
    assert reader.status()["hot"] is True
    assert reader._vision._index == 0  # the display wasn't read


def test_a_dark_display_means_the_kiln_is_off():
    reader, published = make([""])
    assert reader.poll() is None
    assert reader.status()["off"] is True and reader.status()["temp"] is None
    assert len(published) == 1


def test_switched_off_hot_keeps_the_last_reading_then_comes_back():
    reader, published = make(["  312.", ""])  # the dark frame then repeats
    reader.poll()
    reader.poll()  # dark: switched off
    status = reader.status()
    assert status["off"] is True and status["temp"] == 312.0 and status["hot"] is True
    reader.poll()  # still off: nothing new to publish
    assert len(published) == 2
    reader._vision.script = ["  290."]
    reader._vision.advance_to(0)
    reader.poll()  # switched back on
    assert reader.status()["off"] is False and reader.status()["temp"] == 290.0


def test_a_lit_display_without_a_temperature_isnt_off():
    reader, published = make(["Pro ."])  # in a menu
    assert reader.poll() is None
    assert reader.status()["off"] is False and published == []


# -- cooling estimate ---------------------------------------------------------

import math  # noqa: E402

from kiln.kiln_temp import cooling_eta_s  # noqa: E402


def newton(t, start=500.0, room=20.0, k=1 / 7200):
    return room + (start - room) * math.exp(-k * t)


def test_cooling_estimate_matches_newtons_law():
    samples = [(t, newton(t)) for t in range(0, 20 * 60 + 1, 15)]
    eta = cooling_eta_s(samples, room_c=20.0)
    truth = math.log((newton(20 * 60) - 20) / (40 - 20)) * 7200  # from the last sample
    assert abs(eta - truth) < 60  # within a minute of ~6.3 h


def test_no_estimate_until_it_has_cooled_for_a_while():
    short = [(t, newton(t)) for t in range(0, 5 * 60, 15)]  # 5 min
    assert cooling_eta_s(short, room_c=20.0) is None
    flat = [(t, 300.0) for t in range(0, 30 * 60, 15)]  # not cooling
    assert cooling_eta_s(flat, room_c=20.0) is None


def test_reader_estimates_after_a_firing_and_starts_over_when_busy():
    clock = [1000.0]
    temps = iter(newton(t) for t in range(0, 3600, 15))
    reader, _ = make(["IdLE"])
    reader._clock = lambda: clock[0]
    reader._room_c = 18.0  # it read the kiln cool before the firing

    reader._busy = lambda: True  # still firing: no estimate
    reader._firing_temp = lambda: 520.0
    reader.poll()
    assert reader.status()["cooling"] is False and reader.status()["cool_by"] is None

    reader._busy = lambda: False  # finished: the kiln cools on its own
    reader._read = lambda: (next(temps), False)
    reader.poll()
    assert reader.status()["cooling"] is True and reader.status()["cool_by"] is None  # estimating
    for _ in range(80):  # 20 min more
        clock[0] += 15
        reader.poll()
    status = reader.status()
    assert status["cool_by"] is not None
    hours_left = (status["cool_by"] - clock[0]) / 3600
    assert 5 < hours_left < 7.5


def test_cool_reading_clears_the_estimate_and_sets_room_temperature():
    reader, _ = make(["  17."])
    reader._cooling = [(0.0, 300.0)]
    reader.poll()
    assert reader._room_c == 17.0 and reader._cooling == []
    assert reader.status()["cooling"] is False
