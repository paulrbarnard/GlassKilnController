"""Camera-based reader for the GM LT's 7-segment display.

This is the piece most dependent on the physical build (camera angle, ROI,
lighting) — see ``docs/hardware.md`` for the calibration procedure. What's
here is a real, working *shape*: capture -> per-digit ROI crop -> 7-segment
pattern -> character -> classify against ``protocol.DisplayMessage`` /
numeric value, with the actual segment-thresholding left as a clearly marked
TODO until there's a camera and a controller to calibrate against.

``sequencer.py`` only depends on the ``KilnVision`` interface, so it can be
developed and unit-tested against ``MockVision`` long before any of that
calibration happens.
"""

from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass
from typing import Callable, Optional

from kiln.protocol import DisplayMessage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ControllerState:
    """One reading of the GM LT display at a point in time."""

    raw_text: str
    message: Optional[DisplayMessage]
    # Numeric value when the display is showing a plain number (a
    # temperature, a rate, a hold time) rather than one of the fixed
    # vocabulary tokens.
    numeric_value: Optional[float]
    # Segment number suffix, e.g. "rA2" / "HLd2" while firing (p.20).
    segment_number: Optional[int]
    timestamp: float

    @property
    def is_error(self) -> bool:
        from kiln.protocol import ERROR_MESSAGES

        return self.message in ERROR_MESSAGES


# ---------------------------------------------------------------------------
# 7-segment decoding
# ---------------------------------------------------------------------------

# Standard 7-segment layout:
#   _a_
#  f   b
#   _g_
#  e   c
#   _d_
#
# Segment-on pattern (a,b,c,d,e,f,g) -> character, restricted to the glyphs
# the GM LT actually uses (pp.11-12: digits 0-9 plus A,C,d,E,F,H,L,n,o,P,r,
# S(=5),t,U and the degree/segment marks handled separately).
SEGMENT_PATTERNS: dict[tuple[bool, bool, bool, bool, bool, bool, bool], str] = {
    (True, True, True, True, True, True, False): "0",
    (False, True, True, False, False, False, False): "1",
    (True, True, False, True, True, False, True): "2",
    (True, True, True, True, False, False, True): "3",
    (False, True, True, False, False, True, True): "4",
    (True, False, True, True, False, True, True): "5",  # also used as 'S'
    (True, False, True, True, True, True, True): "6",
    (True, True, True, False, False, False, False): "7",
    (True, True, True, True, True, True, True): "8",
    (True, True, True, True, False, True, True): "9",
    (True, True, True, False, True, True, True): "A",
    (True, False, False, True, True, True, False): "C",
    (False, True, True, True, True, False, True): "d",
    (True, False, False, True, True, True, True): "E",
    (True, False, False, False, True, True, True): "F",
    (False, False, True, False, True, False, True): "H",  # lowercase-ish h
    (False, False, False, True, True, True, False): "L",
    (False, False, True, False, True, False, False): "n",
    (False, False, True, True, True, False, True): "o",
    (True, True, False, False, True, True, True): "P",
    (False, False, False, False, True, False, True): "r",
    (False, False, False, True, True, True, True): "t",
    (False, True, True, True, True, True, False): "U",
}


def decode_digit(segments: tuple[bool, bool, bool, bool, bool, bool, bool]) -> str:
    """Map a single digit's (a..g) segment-on booleans to a character.

    Returns ``"?"`` for an unrecognized pattern rather than raising, so a
    bad read degrades to "unclassifiable" instead of crashing the poll loop
    — the sequencer treats "?" the same as "no confident reading yet".
    """

    return SEGMENT_PATTERNS.get(segments, "?")


def classify_display(raw_text: str) -> tuple[Optional[DisplayMessage], Optional[int]]:
    """Match decoded display text against the known vocabulary.

    Returns ``(message, segment_number)`` — ``segment_number`` is populated
    when the text is a vocabulary token with a trailing digit (``rA2``,
    ``HLd2``) per View Segment (p.20). Returns ``(None, None)`` if
    ``raw_text`` doesn't match any known token (most commonly because it's
    actually a plain number/temperature, which callers should try to parse
    separately).
    """

    text = raw_text.strip()
    for message in DisplayMessage:
        token = message.value
        if text == token:
            return message, None
        if text.startswith(token) and text[len(token):].isdigit():
            return message, int(text[len(token):])
    return None, None


def parse_numeric(raw_text: str) -> Optional[float]:
    """Best-effort parse of a plain numeric display (temperature, rate,
    hold H.MM, etc). Returns None if ``raw_text`` isn't purely numeric.
    """

    text = raw_text.strip().replace("°", "")
    try:
        return float(text)
    except ValueError:
        return None


def build_controller_state(raw_text: str) -> ControllerState:
    message, segment_number = classify_display(raw_text)
    numeric_value = None if message is not None else parse_numeric(raw_text)
    return ControllerState(
        raw_text=raw_text,
        message=message,
        numeric_value=numeric_value,
        segment_number=segment_number,
        timestamp=time.time(),
    )


# ---------------------------------------------------------------------------
# Vision sources
# ---------------------------------------------------------------------------


class KilnVision(abc.ABC):
    """Abstract camera interface used by ``sequencer.py``."""

    @abc.abstractmethod
    def read(self) -> ControllerState:
        """Capture a frame and return the current best reading."""

    def wait_for(
        self,
        predicate: Callable[[ControllerState], bool],
        timeout_s: float,
        poll_interval_s: float = 0.15,
    ) -> Optional[ControllerState]:
        """Poll ``read()`` until ``predicate`` is true or ``timeout_s``
        elapses. Returns the matching state, or ``None`` on timeout — the
        sequencer treats a timeout as "missed the prompt window, back out
        to IdLE and retry" (see docs/architecture.md).
        """

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            state = self.read()
            if predicate(state):
                return state
            time.sleep(poll_interval_s)
        return None


class RealVision(KilnVision):
    """Reads the physical GM LT display via a camera.

    TODO(hardware): everything below the camera-open call is a placeholder.
    Once the overlay is built, follow docs/hardware.md's calibration
    procedure to fill in:
      - ``roi``: pixel box cropping the 7-segment display out of the frame.
      - ``digit_boxes``: per-digit sub-boxes within ``roi``.
      - the actual segment-on/off thresholding (currently a stub that
        always returns "?" so failures are loud, not silently wrong).
    """

    def __init__(self, camera_index: int = 0, config_path: Optional[str] = None):
        try:
            import cv2  # type: ignore
        except ImportError as exc:  # pragma: no cover - only off-target
            raise RuntimeError(
                "opencv-python not available — RealVision only runs on the "
                "Uno Q with the camera dependency installed. Use MockVision "
                "for development."
            ) from exc

        self._cv2 = cv2
        self._camera_index = camera_index
        self._config_path = config_path
        self._capture = None
        self.roi: Optional[tuple[int, int, int, int]] = None  # x, y, w, h
        self.digit_boxes: list[tuple[int, int, int, int]] = []
        if config_path:
            self._load_calibration(config_path)

    def _load_calibration(self, config_path: str) -> None:
        import yaml  # type: ignore

        with open(config_path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        roi = data.get("roi")
        if roi:
            self.roi = (roi["x"], roi["y"], roi["w"], roi["h"])
        self.digit_boxes = [
            (b["x"], b["y"], b["w"], b["h"]) for b in data.get("digit_boxes", [])
        ]

    def _ensure_capture(self):
        if self._capture is None:
            self._capture = self._cv2.VideoCapture(self._camera_index)
        return self._capture

    def read(self) -> ControllerState:
        capture = self._ensure_capture()
        ok, frame = capture.read()
        if not ok:
            log.warning("RealVision: camera read failed")
            return build_controller_state("?")

        if self.roi:
            x, y, w, h = self.roi
            frame = frame[y : y + h, x : x + w]

        # TODO(hardware): replace with real per-digit segment thresholding
        # against self.digit_boxes. Left unimplemented (rather than guessed)
        # so it fails loudly instead of quietly returning wrong data.
        raw_text = "?"
        return build_controller_state(raw_text)

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


@dataclass
class MockVision(KilnVision):
    """Cycles through a scripted list of raw display strings — lets the
    sequencer be developed/tested against a known state sequence with no
    camera or kiln attached (see python/tests/test_protocol.py).
    """

    script: list[str]
    _index: int = 0

    def read(self) -> ControllerState:
        if not self.script:
            return build_controller_state("IdLE")
        text = self.script[min(self._index, len(self.script) - 1)]
        state = build_controller_state(text)
        if self._index < len(self.script) - 1:
            self._index += 1
        return state

    def advance_to(self, index: int) -> None:
        self._index = index
