"""Display decoding against a real camera frame, plus vocabulary matching.

fixtures/idle_roi.png is the roi crop of a 1632x1232 frame of "IdLE." taken
on Steffi on 2026-10-21 (Pi Camera V2, 105 mm), with
config/camera_calibration.yaml as measured from that frame. Add a fixture
per reference frame as they're captured (scripts/read_display.py --save).
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
