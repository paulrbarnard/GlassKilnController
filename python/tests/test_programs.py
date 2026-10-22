"""Parsing a PRO slot's Review into a program, using the screens recorded
from the kiln on 2026-10-22 (slots 5 and 4)."""

import pytest

from kiln.programs import ReviewParseError, hold_minutes_from_display, parse_review

SLOT_5 = [
    "Pro .", "   5.", "5E6 .", "   2.",
    "rA 1.", "0060.", "°C 1.", "0121.", "HLd1.", "12.00.",
    "rA 2.", "0031.", "°C 2.", "0060.", "HLd2.", "00.05.",
]
SLOT_4 = ["Pro .", "   4.", "5E6 .", "   1.", "rA 1.", "0300.", "°C 1.", "0650.", "HLd1.", "00.05."]


def test_slot_5_from_the_kiln():
    program = parse_review(SLOT_5)
    assert program.slot == 5 and program.celsius is True
    first, second = program.segments
    assert first.rate_f_per_hr == pytest.approx(108.0)  # 60 °C/hr
    assert first.target_temp_f == pytest.approx(249.8)  # 121 °C
    assert first.hold_minutes == 720  # 12.00
    assert second.rate_f_per_hr == pytest.approx(55.8)  # 31 °C/hr
    assert second.target_temp_f == pytest.approx(140.0)  # 60 °C
    assert second.hold_minutes == 5


def test_slot_4_from_the_kiln():
    program = parse_review(SLOT_4)
    assert program.slot == 4 and len(program.segments) == 1
    assert program.segments[0].target_temp_f == pytest.approx(1202.0)  # 650 °C


def test_fahrenheit_review_has_no_dots_and_no_conversion():
    screens = ["Pro ", "   4", "5E6 ", "   1", "rA 1", "0540", "°F 1", "1202", "HLd1", "00.05"]
    program = parse_review(screens)
    assert program.celsius is False
    assert program.segments[0].rate_f_per_hr == 540 and program.segments[0].target_temp_f == 1202


@pytest.mark.parametrize("missing", [5, 9, 15])
def test_a_missed_screen_is_an_error_not_a_wrong_program(missing):
    screens = SLOT_5[:missing] + SLOT_5[missing + 1 :]
    with pytest.raises(ReviewParseError):
        parse_review(screens)


@pytest.mark.parametrize("shown, minutes", [(12.00, 720), (0.05, 5), (1.30, 90), (99.99, 99 * 60 + 99)])
def test_hold_display(shown, minutes):
    assert hold_minutes_from_display(shown) == minutes
