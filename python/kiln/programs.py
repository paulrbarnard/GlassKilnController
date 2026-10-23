"""Ramp & Hold programs as stored in the GM LT's PRO slots (1-5).

Read with LoAd > slot > ENTER (rEdI) > DOWN (Review): the controller then
shows each screen for about half a second and returns to rEdI. Verified on
the kiln (2026-10-22), slot 5 reviewed as:

    Pro, 5, SEG, 2,
    rA 1, 0060, °C 1, 0121, HLd1, 12.00,
    rA 2, 0031, °C 2, 0060, HLd2, 00.05

i.e. the slot, the segment count, then per segment the rate (degrees per
hour), the target temperature and the hold (Hours.Minutes), all in the
controller's own unit. ``parse_review`` turns that list of screens into a
``KilnProgram``, with segments converted to °F like every other profile.
"""

from __future__ import annotations

from dataclasses import dataclass

from kiln.protocol import DisplayMessage, RampHoldSegment
from kiln.vision import build_controller_state


class ReviewParseError(ValueError):
    """The recorded screens don't form a complete program review (most
    likely a screen was missed); read it again."""


@dataclass(frozen=True)
class KilnProgram:
    slot: int
    segments: tuple[RampHoldSegment, ...]
    celsius: bool  # the unit the controller showed it in

    def to_dict(self) -> dict:
        return {
            "slot": self.slot,
            "unit": "C" if self.celsius else "F",
            "segments": [
                {
                    "rate_f_per_hr": s.rate_f_per_hr,
                    "target_temp_f": s.target_temp_f,
                    "hold_minutes": s.hold_minutes,
                }
                for s in self.segments
            ],
        }


def hold_minutes_from_display(value: float) -> int:
    """Hours.Minutes as shown ("12.00" -> 720, "00.05" -> 5). The minutes
    part isn't normalised: up to 99 hours and 99 minutes (p.17)."""

    hours = int(value)
    minutes = round((value - hours) * 100)
    return hours * 60 + minutes


def parse_review(screens: list[str]) -> KilnProgram:
    states = [build_controller_state(text) for text in screens]
    pos = 0

    def take(description: str, check) -> object:
        nonlocal pos
        if pos >= len(states):
            raise ReviewParseError(f"review ended before {description}")
        state = states[pos]
        if not check(state):
            raise ReviewParseError(f"expected {description}, saw {state.raw_text!r}")
        pos += 1
        return state

    def number(description: str) -> float:
        state = take(description, lambda s: s.message is None and s.numeric_value is not None)
        return state.numeric_value

    take("Pro", lambda s: s.message is DisplayMessage.PRO)
    slot = int(number("the slot number"))
    take("SEG", lambda s: s.message is DisplayMessage.SEGMENT_COUNT)
    count = int(number("the segment count"))
    if not 1 <= count <= 8:
        raise ReviewParseError(f"segment count {count} is outside 1-8")

    segments = []
    celsius = None
    for i in range(1, count + 1):
        take(f"rA {i}", lambda s: s.message is DisplayMessage.RATE_PROMPT and s.segment_number == i)
        rate = number(f"segment {i}'s rate")
        label = take(
            f"°C/°F {i}",
            lambda s: s.message in (DisplayMessage.DEG_C, DisplayMessage.DEG_F) and s.segment_number == i,
        )
        segment_celsius = label.message is DisplayMessage.DEG_C
        if celsius is None:
            celsius = segment_celsius
        elif celsius != segment_celsius:
            raise ReviewParseError("the review switched units part way through")
        temp = number(f"segment {i}'s temperature")
        take(f"HLd {i}", lambda s: s.message is DisplayMessage.HOLD_PROMPT and s.segment_number == i)
        hold = number(f"segment {i}'s hold")

        if celsius:
            rate_f, temp_f = rate * 9.0 / 5.0, temp * 9.0 / 5.0 + 32.0
        else:
            rate_f, temp_f = rate, temp
        segments.append(
            RampHoldSegment(
                rate_f_per_hr=rate_f,
                target_temp_f=temp_f,
                hold_minutes=hold_minutes_from_display(hold),
            )
        )
    if pos != len(states):
        raise ReviewParseError(f"unexpected screens after the last segment: {screens[pos:]}")
    return KilnProgram(slot=slot, segments=tuple(segments), celsius=bool(celsius))


def same_segments(a, b, temp_tol_f: float = 2.0, rate_tol_f: float = 2.0) -> bool:
    """Whether two programs are the same, allowing for the whole-degree
    rounding the controller applies when it's in °C."""

    a, b = list(a), list(b)
    if len(a) != len(b):
        return False
    return all(
        abs(x.target_temp_f - y.target_temp_f) <= temp_tol_f
        and abs(x.rate_f_per_hr - y.rate_f_per_hr) <= rate_tol_f
        and round(x.hold_minutes) == round(y.hold_minutes)
        for x, y in zip(a, b)
    )
