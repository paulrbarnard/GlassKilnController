"""Display decoding against a real camera frame, plus vocabulary matching.

fixtures/idle_roi.png is the roi crop of a 1632x1232 frame of "IdLE." taken
with `cam` on Steffi on 2026-10-21 (Pi Camera V2, 105 mm), with
config/camera_calibration.yaml as measured from that frame. The live_*
fixtures came through RealVision's own capture pipeline (scripts/step.py).
Add a fixture per reference frame as they're captured.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

from kiln.protocol import DisplayMessage
from kiln.vision import (
    GLYPHS,
    DisplayCalibration,
    build_controller_state,
    classify_display,
    decode_digit,
    decode_roi,
)

HERE = Path(__file__).resolve().parent
CAL = DisplayCalibration.load(str(HERE.parent / "config" / "camera_calibration.yaml"))


def _fixture(name: str):
    image = cv2.imread(str(HERE / "fixtures" / name))
    assert image is not None, name
    return image


def test_idle_frame_decodes():
    text = decode_roi(_fixture("idle_roi.png"), CAL)
    assert text == "1dLE."
    assert build_controller_state(text).message is DisplayMessage.IDLE


@pytest.mark.parametrize(
    "fixture, text",
    [
        # Live frames through RealVision's own pipeline, 2026-10-22.
        ("live_idle_roi.png", "1dLE."),
        ("live_18_roi.png", "  18."),
        # This display draws 9 without the bottom segment.
        ("live_19_roi.png", "  19."),
    ],
)
def test_live_frames_decode(fixture, text):
    assert decode_roi(_fixture(fixture), CAL) == text


@pytest.mark.parametrize(
    "fixture, text, message, segment, number",
    [
        # DOWN (Review) from IdLE, 2026-10-22: last program was PRO slot 5.
        ("review_pro_roi.png", "Pro .", DisplayMessage.PRO, None, None),
        ("review_slot5_roi.png", "   5.", None, None, 5),
        # SEG: G is drawn like 6 on this display.
        ("review_seg_roi.png", "5E6 .", DisplayMessage.SEGMENT_COUNT, None, None),
        ("review_ra1_roi.png", "rA 1.", DisplayMessage.RATE_PROMPT, 1, None),
        ("review_0060_roi.png", "0060.", None, None, 60),
        ("review_degc1_roi.png", "°C 1.", DisplayMessage.DEG_C, 1, None),
        ("review_hld1_roi.png", "HLd1.", DisplayMessage.HOLD_PROMPT, 1, None),
        ("review_12_00_roi.png", "12.00.", None, None, 12.0),
        ("review_00_05_roi.png", "00.05.", None, None, 0.05),
    ],
)
def test_review_frames(fixture, text, message, segment, number):
    decoded = decode_roi(_fixture(fixture), CAL)
    assert decoded == text
    state = build_controller_state(decoded)
    assert (state.message, state.segment_number, state.numeric_value) == (message, segment, number)


@pytest.mark.parametrize(
    "fixture, message",
    [
        # Walk through Auto on the kiln, 2026-10-22, revised camera mount
        # (these crops use the refitted roi).
        ("menu_auto_roi.png", DisplayMessage.AUTO),
        ("menu_pro_roi.png", DisplayMessage.PRO),
        ("menu_opt_roi.png", DisplayMessage.OPTIONS),
        ("menu_load_roi.png", DisplayMessage.LOAD),
        ("speed_fast_roi.png", DisplayMessage.SPEED_FAST),
        ("speed_slow_roi.png", DisplayMessage.SPEED_SLOW),
        ("speed_medium_roi.png", DisplayMessage.SPEED_MEDIUM),
        ("process_slump_roi.png", DisplayMessage.PROCESS_SLUMP),
        ("process_tack_roi.png", DisplayMessage.PROCESS_TACK),
        ("process_full_roi.png", DisplayMessage.PROCESS_FULL),
        ("ready_roi.png", DisplayMessage.READY),
        ("stopped_roi.png", DisplayMessage.STOPPED),
    ],
)
def test_menu_frames(fixture, message):
    assert build_controller_state(decode_roi(_fixture(fixture), CAL)).message is message


def test_idle_frame_survives_exposure_change():
    dimmer = (_fixture("idle_roi.png") * 0.6).astype(np.uint8)
    assert decode_roi(dimmer, CAL) == "1dLE."


def test_dark_display_reads_blank():
    x, y, w, h = CAL.roi
    dark = np.full((h, w, 3), 20, dtype=np.uint8)
    text = decode_roi(dark, CAL)
    assert text.strip() == ""
    assert build_controller_state(text).message is None


def test_decode_digit_prefers_digits_for_shared_patterns():
    # I/1, S/5 and O/0 light the same segments; numbers must still parse.
    assert decode_digit((False, True, True, False, False, False, False)) == "1"
    assert decode_digit((True, False, True, True, False, True, True)) == "5"
    assert decode_digit((False,) * 7) == " "


@pytest.mark.parametrize(
    "raw, message, segment",
    [
        ("IdLE", DisplayMessage.IDLE, None),
        ("1dLE", DisplayMessage.IDLE, None),
        ("1dLE.", DisplayMessage.IDLE, None),
        ("SLo", DisplayMessage.SPEED_SLOW, None),
        ("5Lo", DisplayMessage.SPEED_SLOW, None),
        ("rA2", DisplayMessage.RATE_PROMPT, 2),
        ("HLd3", DisplayMessage.HOLD_PROMPT, 3),
        # Mode menu as decoded from the real display, 2026-10-22: capital U
        # in AUto, and O reads as 0.
        ("AUto.", DisplayMessage.AUTO, None),
        ("Pro .", DisplayMessage.PRO, None),
        ("LoAd.", DisplayMessage.LOAD, None),
        ("0Pt .", DisplayMessage.OPTIONS, None),
        # Speeds: M is drawn over two digits as two upside-down Us.
        ("FA5t.", DisplayMessage.SPEED_FAST, None),
        ("5Lo .", DisplayMessage.SPEED_SLOW, None),
        ("NNEd.", DisplayMessage.SPEED_MEDIUM, None),
        ("5t0P.", DisplayMessage.STOPPED, None),
    ],
)
def test_classify_matches_by_segment_pattern(raw, message, segment):
    assert classify_display(raw) == (message, segment)


def test_temperature_is_numeric_not_a_message():
    state = build_controller_state(" 23.")
    assert state.message is None
    assert state.numeric_value == 23.0


def test_every_vocabulary_token_has_glyphs_and_a_unique_pattern():
    seen = {}
    for message in DisplayMessage:
        assert all(c in GLYPHS for c in message.value), message
        key = tuple("".join(sorted(GLYPHS[c])) for c in message.value)
        assert key not in seen, f"{message} looks the same as {seen.get(key)}"
        seen[key] = message


@pytest.mark.parametrize(
    "raw, celsius",
    [
        # The last digit's dot is the GM LT's °C indicator (2026-10-22).
        ("1dLE.", True),
        ("Pro .", True),
        ("12.00.", True),
        ("1dLE", False),
        ("  98", False),
        ("12.00", False),
        ("    ", None),  # kiln off: nothing to tell from
        ("1d?E.", None),
    ],
)
def test_celsius_indicator(raw, celsius):
    assert build_controller_state(raw).shows_celsius is celsius
