"""Camera-based reader for the GM LT's 7-segment display.

This is the piece most dependent on the physical build (camera angle, ROI,
lighting) — see ``docs/hardware.md`` for the calibration procedure. The
pipeline: capture -> roi crop -> sample each digit's segments at calibrated
points -> 7-segment pattern -> character -> classify against
``protocol.DisplayMessage`` / numeric value. Calibrated on Steffi from one
frame of "IdLE." (2026-10-21); more reference frames are still to come.

``sequencer.py`` only depends on the ``KilnVision`` interface, so it can be
developed and unit-tested against ``MockVision`` long before any of that
calibration happens.
"""

from __future__ import annotations

import abc
import logging
import threading
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
# 7-segment glyphs
# ---------------------------------------------------------------------------

# Standard 7-segment layout:
#   _a_
#  f   b
#   _g_
#  e   c
#   _d_
#
# Which segments each character lights. Characters the GM LT draws the same
# way share a pattern (I and 1, O and 0, S and 5), so the display is matched
# against the vocabulary by pattern, never by letter (see classify_display).
# Verified on the real display (Steffi, 2026-10-21/22): 0, 1/I, 2, 3, 5/S,
# 8, A, C, d, E, G, L, o, P, r, the degree sign, and 9, which this display
# draws without the bottom segment. G is drawn exactly like 6. The rest are
# the usual 7-segment shapes; M, N, D, 6 and 7 vary between displays and
# should be checked against reference frames.
GLYPHS: dict[str, str] = {
    " ": "",
    "0": "abcdef", "1": "bc", "2": "abdeg", "3": "abcdg", "4": "bcfg",
    "5": "acdfg", "6": "acdefg", "7": "abc", "8": "abcdefg", "9": "abcfg",
    "A": "abcefg", "b": "cdefg", "C": "adef", "c": "deg", "d": "bcdeg",
    "D": "bcdeg", "E": "adefg", "F": "aefg", "G": "acdefg", "H": "bcefg",
    "h": "cefg", "I": "bc", "L": "def", "M": "abcef", "N": "abcef",
    "n": "ceg", "O": "abcdef", "o": "cdeg", "P": "abefg", "r": "eg",
    "S": "acdfg", "t": "efg", "U": "bcdef", "u": "cde", "-": "g",
    "°": "abfg",
}

SEGMENT_NAMES = "abcdefg"


def _pattern_key(segments: str) -> str:
    return "".join(sorted(segments))


# Pattern -> the character to report for it. Digits win over letters that
# look the same, so numbers parse; classify_display copes with the letters.
_DECODE: dict[str, str] = {}
for _char in "0123456789AbCcdEFGHhLNnoPrtUu-° ":
    _DECODE.setdefault(_pattern_key(GLYPHS[_char]), _char)


def decode_digit(segments: tuple[bool, bool, bool, bool, bool, bool, bool]) -> str:
    """Map a single digit's (a..g) segment-on booleans to a character.

    Returns ``" "`` for a blank digit and ``"?"`` for an unrecognized
    pattern rather than raising, so a bad read degrades to "unclassifiable"
    instead of crashing the poll loop — the sequencer treats "?" the same as
    "no confident reading yet".
    """

    lit = "".join(name for name, on in zip(SEGMENT_NAMES, segments) if on)
    return _DECODE.get(_pattern_key(lit), "?")


def _patterns(text: str) -> Optional[list[str]]:
    """``text`` as one segment pattern per character, ignoring decimal
    points and blanks; None if it has a character with no known glyph.
    Blanks are dropped because the display pads a token from its number
    ("rA 1", "°C 1")."""

    out = []
    for char in text.replace(".", "").replace(" ", ""):
        if char not in GLYPHS:
            return None
        out.append(_pattern_key(GLYPHS[char]))
    return out


def classify_display(raw_text: str) -> tuple[Optional[DisplayMessage], Optional[int]]:
    """Match decoded display text against the known vocabulary.

    Matching is by segment pattern, so "1dLE" (what the camera decodes) is
    IdLE. Returns ``(message, segment_number)`` — ``segment_number`` is
    populated when the text is a vocabulary token with a trailing digit
    (``rA2``, ``HLd2``) per View Segment (p.20). Returns ``(None, None)`` if
    ``raw_text`` doesn't match any known token (most commonly because it's
    actually a plain number/temperature, which callers should try to parse
    separately).
    """

    seen = _patterns(raw_text.strip())
    if not seen:
        return None, None
    for message in DisplayMessage:
        if _patterns(message.value) == seen:
            return message, None
    digit_patterns = {_pattern_key(GLYPHS[d]): d for d in "0123456789"}
    for message in DisplayMessage:
        token = _patterns(message.value)
        if not token or len(seen) <= len(token) or seen[: len(token)] != token:
            continue
        rest = seen[len(token):]
        if all(p in digit_patterns for p in rest):
            return message, int("".join(digit_patterns[p] for p in rest))
    return None, None


def parse_numeric(raw_text: str) -> Optional[float]:
    """Best-effort parse of a plain numeric display (temperature, rate,
    hold H.MM, etc). Returns None if ``raw_text`` isn't purely numeric.

    The decimal point after the last digit is lit on every reading on the
    real display ("  19.", "12.00.", "Pro ."), so it's a status light, not
    part of the number, and is dropped.
    """

    text = raw_text.strip().replace("°", "")
    if text.endswith("."):
        text = text[:-1]
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
# Reading a frame
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DisplayCalibration:
    """Where the display sits in the camera frame. Loaded from
    python/config/camera_calibration.yaml; see docs/hardware.md."""

    roi: tuple[int, int, int, int]  # x, y, w, h in frame pixels
    digit_boxes: tuple[tuple[int, int, int, int], ...]  # relative to roi
    # Centre of each segment a..g, and of the decimal point, relative to a
    # digit box (the same for every digit).
    segment_points: tuple[tuple[int, int], ...]
    dp_point: tuple[int, int]
    sample_half: int = 5  # sample a (2n+1)-px square around each point
    min_lit: int = 80  # brightest sample below this: display is off

    @classmethod
    def load(cls, path: str) -> "DisplayCalibration":
        import yaml  # type: ignore

        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        roi = data["roi"]
        points = data["segment_points"]
        return cls(
            roi=(roi["x"], roi["y"], roi["w"], roi["h"]),
            digit_boxes=tuple((b["x"], b["y"], b["w"], b["h"]) for b in data["digit_boxes"]),
            segment_points=tuple(tuple(points[name]) for name in SEGMENT_NAMES),
            dp_point=tuple(points["dp"]),
            sample_half=int(data.get("sample_half", 5)),
            min_lit=int(data.get("min_lit", 80)),
        )


def sample_segments(roi_image, cal: DisplayCalibration) -> list[list[float]]:
    """Mean green-channel level at each segment point (a..g, then dp) of
    each digit. Green separates lit from unlit best: the LEDs are bright
    enough to saturate it, while the dark panel's colour cast differs
    between capture paths (green through ``cam``, blue through the app's
    pipeline) and lands in red or blue."""

    green = roi_image[:, :, 1].astype("float32")
    h = cal.sample_half
    levels = []
    for bx, by, _, _ in cal.digit_boxes:
        digit = []
        for px, py in (*cal.segment_points, cal.dp_point):
            x, y = bx + px, by + py
            digit.append(float(green[y - h : y + h + 1, x - h : x + h + 1].mean()))
        levels.append(digit)
    return levels


def segment_cut(levels: list[list[float]], cal: DisplayCalibration) -> Optional[float]:
    """The level above which a sample counts as lit, or None if nothing is
    bright enough for the display to be on."""

    flat = [v for digit in levels for v in digit]
    lo, hi = min(flat), max(flat)
    if hi < cal.min_lit:
        return None
    # Everything lit about equally (e.g. "8.8.8.8."): no dark reference.
    return (lo + hi) / 2 if hi - lo > cal.min_lit / 2 else lo / 2


def decode_roi(roi_image, cal: DisplayCalibration) -> str:
    """Read the display text from the roi crop of a frame.

    Each segment is on if it's brighter than halfway between the dimmest and
    brightest samples in this frame, so exposure changes don't matter. A
    frame with nothing brighter than ``min_lit`` is a dark display: all
    blanks. Decimal points come out as "." after their digit.
    """

    levels = sample_segments(roi_image, cal)
    cut = segment_cut(levels, cal)
    if cut is None:
        return " " * len(levels)
    text = ""
    for digit in levels:
        segments = tuple(v > cut for v in digit[:7])
        text += decode_digit(segments) + ("." if digit[7] > cut else "")
    return text


def read_frame_text(frame, cal: DisplayCalibration) -> str:
    x, y, w, h = cal.roi
    return decode_roi(frame[y : y + h, x : x + w], cal)


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
    """Reads the physical GM LT display via the CSI camera.

    Capture is libcamera -> GStreamer -> OpenCV at the sensor's 2x2-binned
    1632x1232 mode, the same pipeline the UnoQ-Doorbell app uses on this
    board (a plain ``cv2.VideoCapture(0)`` gets raw Bayer data from camss,
    not a picture). The camera opens on the first read and is released
    after ``release_after_s`` without one, so tools like scripts/focus.sh
    and ``cam`` can use it between runs.
    """

    CAPTURE_SIZE = (1632, 1232)
    # Auto-exposure and white balance take ~5 s to settle after the camera
    # opens (green peak 115 -> 254 on the lit display, measured 2026-10-22);
    # frames before that read the display as dim or off.
    SETTLE_S = 6.0

    def __init__(
        self,
        config_path: str,
        csi_index: int = 0,
        release_after_s: float = 30.0,
    ):
        try:
            import cv2  # type: ignore
        except ImportError as exc:  # pragma: no cover - only off-target
            raise RuntimeError(
                "opencv-python not available — RealVision only runs on the "
                "Uno Q with the camera dependency installed. Use MockVision "
                "for development."
            ) from exc

        self._cv2 = cv2
        self.calibration = DisplayCalibration.load(config_path)
        self._csi_index = csi_index
        self._release_after_s = release_after_s
        self._capture = None
        self._lock = threading.Lock()
        self._idle_timer: Optional[threading.Timer] = None

    def _open(self):
        from arduino.app_peripherals.camera import csi_camss_discovery as camss  # type: ignore

        camss.setup_gstreamer()
        source = camss.gstreamer_source(self._csi_index)
        w, h = self.CAPTURE_SIZE
        pipeline = (
            f"{source} ! video/x-raw,width={w},height={h} ! "
            "videoconvert ! video/x-raw,format=BGR ! appsink drop=true max-buffers=1"
        )
        capture = self._cv2.VideoCapture(pipeline, self._cv2.CAP_GSTREAMER)
        if not capture.isOpened():
            raise RuntimeError("failed to open libcamera pipeline")
        settle_until = time.monotonic() + self.SETTLE_S
        while time.monotonic() < settle_until:
            capture.read()
        log.info("RealVision: camera open (%s)", source)
        return capture

    def read_frame(self):
        """Grab one full BGR frame, or None if the camera failed."""

        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            try:
                if self._capture is None:
                    self._capture = self._open()
                ok, frame = self._capture.read()
            except Exception as exc:  # camera missing, busy, pipeline error
                log.warning("RealVision: camera open failed: %s", exc)
                ok, frame = False, None
            if not ok:
                self._release_locked()
                return None
            self._idle_timer = threading.Timer(self._release_after_s, self.close)
            self._idle_timer.daemon = True
            self._idle_timer.start()
            return frame

    # The GM LT alternates IdLE with the temperature about twice a second,
    # so a frame often catches a change halfway ("1dU6." between IdLE and
    # 15). read() only reports text that two consecutive frames agree on.
    STABLE_TRIES = 6

    def read(self) -> ControllerState:
        previous = None
        for _ in range(self.STABLE_TRIES):
            frame = self.read_frame()
            if frame is None:
                log.warning("RealVision: camera read failed")
                return build_controller_state("?")
            text = read_frame_text(frame, self.calibration)
            if text == previous:
                return build_controller_state(text)
            previous = text
        log.debug("RealVision: no stable reading in %d frames", self.STABLE_TRIES)
        return build_controller_state("?")

    def _release_locked(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
            log.info("RealVision: camera released")

    def close(self) -> None:
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            self._release_locked()


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
