"""GlassMaster LT protocol reference data.

Transcribed from ``docs/controller_protocol.md`` (itself transcribed from the
GM LT operating manual). This module is pure data + enums: no I/O, no
behavior. If a future manual revision changes anything, update the doc and
this file together.

Everything downstream (``bridge``, ``vision``, ``profiles``, ``sequencer``)
is built on the vocabulary defined here so there is exactly one place that
knows what "SLo" or "EdIt" means.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Physical keys
# ---------------------------------------------------------------------------


class Key(Enum):
    """Must match the ``Key`` enum in sketch/sketch.ino (same int values)."""

    ENTER = 0
    UP = 1
    DOWN = 2


# Long-press ("hold") duration for the manual's "hold 4 seconds" actions:
# Start Over, unlock/relock Advanced level (p.8, p.10, p.18). The
# controller acts when it beeps, at about 4s, so hold for 5s to make sure
# the release never lands just before the beep.
LONG_PRESS_SECONDS = 5.0

# Default short-press dwell used for a plain tap. 300 ms is what registered
# reliably on the real membrane buttons (2026-10-22); 150 ms was never
# tested on them.
SHORT_PRESS_MS = 300

# Timed prompt windows in live Edit Mode (p.21-22). The manual gives two
# different figures for how long "EdIt" is shown depending on entry point;
# both are transcribed here rather than assumed to be a typo.
EDIT_PROMPT_WINDOW_SKIP_SECONDS = 2.0  # Skip Segment entry (p.21)
EDIT_PROMPT_WINDOW_ADD_SECONDS = 6.0  # Add Time / Add Temperature entry (p.21-22)
SSEG_PROMPT_WINDOW_SECONDS = 6.0


# ---------------------------------------------------------------------------
# Display vocabulary (7-segment strings, pp.11-12)
# ---------------------------------------------------------------------------


class DisplayMessage(Enum):
    """The fixed set of non-numeric strings the GM LT display can show.

    Values are the literal on-screen text (manual's own capitalization) so
    that ``vision.py`` classification results can be compared/constructed
    directly against these without a second translation table.
    """

    DEG_F = "°F"
    DEG_C = "°C"  # uppercase C on the real display (verified 2026-10-22)
    AUTO = "AUto"  # capital U on the real display (verified 2026-10-22)
    PRO = "Pro"
    LOAD = "LoAd"
    OPTIONS = "OPt"
    CHANGE_SCALE = "CHG°"
    COMPLETE = "CPLt"
    EDIT_PROMPT = "EdIt"
    HOLD_PROMPT = "HLd"
    IDLE = "IdLE"
    MGF = "MGF"
    ON = "ON"
    OFF = "OFF"  # an OPt setting's value (MGF), shown as "0FF"
    POWER_FAILURE = "PF"
    RATE_PROMPT = "rA"
    READY = "rEdI"
    RESET = "rSEt"
    SEGMENT_COUNT = "SEG"  # PRO: number of segments in the program
    SPEED_SLOW = "SLo"
    SPEED_MEDIUM = "MEd"
    SPEED_FAST = "FASt"
    PROCESS_SLUMP = "SLP"
    PROCESS_TACK = "tAC"
    PROCESS_FULL = "FULL"
    SKIP_SEGMENT = "SSEG"
    STOPPED = "StOP"
    ADD_TIME = "tME"
    ADD_TEMP = "tMP"
    ERR_HEAT_FAIL = "Err1"
    ERR_COOL_FAIL = "ErrF"
    ERR_OVERSHOOT = "ErrD"
    THERMOCOUPLE_FAIL = "FAIL"
    THERMOCOUPLE_REVERSED = "tc--"


# Display states that mean "the kiln has already shut itself off; stop and
# surface this to a human, do not attempt to navigate past it" (see
# docs/controller_protocol.md, "Firing/error notes").
ERROR_MESSAGES = frozenset(
    {
        DisplayMessage.ERR_HEAT_FAIL,
        DisplayMessage.ERR_COOL_FAIL,
        DisplayMessage.ERR_OVERSHOOT,
        DisplayMessage.THERMOCOUPLE_FAIL,
        DisplayMessage.THERMOCOUPLE_REVERSED,
    }
)


# ---------------------------------------------------------------------------
# Programming levels & modes (p.8-9)
# ---------------------------------------------------------------------------


class ProgrammingLevel(Enum):
    BEGINNER = "beginner"
    BEGINNER_PLUS_MGF = "beginner_plus_mgf"
    ADVANCED = "advanced"


class Mode(Enum):
    AUTO = DisplayMessage.AUTO.value
    PRO = DisplayMessage.PRO.value
    LOAD = DisplayMessage.LOAD.value
    OPTIONS = DisplayMessage.OPTIONS.value
    # EDIT is not selected from the mode menu — it's only reachable live,
    # while firing, via UP. Included here for completeness of the state
    # space the sequencer reasons about.
    EDIT = DisplayMessage.EDIT_PROMPT.value


# Modes only reachable once ProgrammingLevel.ADVANCED is unlocked (p.9).
ADVANCED_ONLY_MODES = frozenset({Mode.PRO, Mode.LOAD, Mode.OPTIONS})


# ---------------------------------------------------------------------------
# GlassFire (AUTO mode) factory data (p.14, p.32)
# ---------------------------------------------------------------------------


class Speed(Enum):
    SLOW = DisplayMessage.SPEED_SLOW.value
    MEDIUM = DisplayMessage.SPEED_MEDIUM.value
    FAST = DisplayMessage.SPEED_FAST.value


class Process(Enum):
    SLUMP = DisplayMessage.PROCESS_SLUMP.value
    TACK_FUSE = DisplayMessage.PROCESS_TACK.value
    FULL_FUSE = DisplayMessage.PROCESS_FULL.value


@dataclass(frozen=True)
class ProcessTarget:
    """Soak temperature/hold for a Process, independent of speed (p.32)."""

    soak_temp_f: float
    hold_minutes: float


PROCESS_TABLE: dict[Process, ProcessTarget] = {
    Process.FULL_FUSE: ProcessTarget(soak_temp_f=1465, hold_minutes=10),
    Process.TACK_FUSE: ProcessTarget(soak_temp_f=1325, hold_minutes=10),
    Process.SLUMP: ProcessTarget(soak_temp_f=1225, hold_minutes=15),
}


@dataclass(frozen=True)
class GlassFireSegment:
    """One of the 4 fixed segments in every factory GlassFire program.

    ``soak_temp_f``/``hold_minutes`` of ``None`` means "determined by the
    selected Process" (segment 2, the process/soak segment itself) — see
    ``resolve_glassfire_program`` below.
    """

    rate_f_per_hr: float
    soak_temp_f: Optional[float]
    hold_minutes: Optional[float]


# 9999 °F/hr is the manual's own notation for "as fast as possible" (p.32).
AS_FAST_AS_POSSIBLE = 9999.0

GLASSFIRE_RATE_TABLE: dict[Speed, list[GlassFireSegment]] = {
    Speed.SLOW: [
        GlassFireSegment(250, 300, 10),
        GlassFireSegment(400, None, None),  # by process
        GlassFireSegment(AS_FAST_AS_POSSIBLE, 950, 60),
        GlassFireSegment(100, 800, 15),
    ],
    Speed.MEDIUM: [
        GlassFireSegment(350, 300, 10),
        GlassFireSegment(600, None, None),
        GlassFireSegment(AS_FAST_AS_POSSIBLE, 950, 30),
        GlassFireSegment(150, 800, 10),
    ],
    Speed.FAST: [
        GlassFireSegment(500, 300, 5),
        GlassFireSegment(800, None, None),
        GlassFireSegment(AS_FAST_AS_POSSIBLE, 950, 30),
        GlassFireSegment(175, 800, 10),
    ],
}


def resolve_glassfire_program(
    speed: Speed, process: Process
) -> list[GlassFireSegment]:
    """Return the 4 GlassFire segments for (speed, process) with the
    process/soak segment (segment 2) filled in from ``PROCESS_TABLE``.
    """

    target = PROCESS_TABLE[process]
    segments = list(GLASSFIRE_RATE_TABLE[speed])
    process_seg = segments[1]
    segments[1] = GlassFireSegment(
        rate_f_per_hr=process_seg.rate_f_per_hr,
        soak_temp_f=target.soak_temp_f,
        hold_minutes=target.hold_minutes,
    )
    return segments


# ---------------------------------------------------------------------------
# Ramp & Hold (PRO mode) — generic segment + factory casting program (p.33)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RampHoldSegment:
    """One segment of a PRO-mode program: rate, target temp, hold time.

    ``hold_minutes`` is total minutes; the controller itself enters/displays
    hold as Hours.Minutes (p.17) — conversion happens at the sequencer
    boundary, not in this data model.
    """

    rate_f_per_hr: float
    target_temp_f: float
    hold_minutes: float


# PRO mode slot 5 ships from the factory with this casting program (p.33).
# Overwritten by anything the user stores in slot 5; restored by a factory
# RESET (OPTIONS mode, p.22).
FACTORY_CASTING_PROGRAM: list[RampHoldSegment] = [
    RampHoldSegment(rate_f_per_hr=150, target_temp_f=250, hold_minutes=15),
    RampHoldSegment(rate_f_per_hr=250, target_temp_f=1500, hold_minutes=5),
    RampHoldSegment(rate_f_per_hr=1000, target_temp_f=950, hold_minutes=150),
    RampHoldSegment(rate_f_per_hr=30, target_temp_f=750, hold_minutes=20),
]

MAX_PRO_SEGMENTS = 8
MAX_PRO_SLOTS = 5  # physical controller capacity — at most this many profiles "active" at once
FACTORY_CASTING_SLOT = 5

# App-level cap on *saved* custom (Ramp & Hold) profiles — bigger than
# MAX_PRO_SLOTS on purpose: you can keep a library of recipes and swap
# which up-to-5 are actually programmed into the controller's PRO slots at
# any given time. 12 rather than some other number because it fills one
# 4-column grid screen exactly (see assets/index.html screen-custom).
MAX_USER_PROFILES = 12

# Entry limits (pp.16-17) — the controller itself refuses values beyond
# these; the profile editor UI clamps to the same bounds rather than
# letting someone build a profile the physical controller would reject.
MAX_PROCESS_TEMP_F = 1650.0
MAX_HOLD_HOURS = 99
MAX_HOLD_MINUTES_PART = 99
MAX_HOLD_MINUTES_TOTAL = MAX_HOLD_HOURS * 60 + MAX_HOLD_MINUTES_PART


# ---------------------------------------------------------------------------
# Size/speed guidance shown to the user (p.14) — advisory only, not a
# controller input.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SizeLimit:
    thickness_in: float
    max_diameter_in: Optional[float]  # None == "not recommended"


SIZE_SPEED_GUIDE: dict[Speed, list[SizeLimit]] = {
    Speed.FAST: [
        SizeLimit(0.125, 18),
        SizeLimit(0.25, 4),
        SizeLimit(0.375, None),
    ],
    Speed.MEDIUM: [
        SizeLimit(0.125, 20),
        SizeLimit(0.25, 8),
        SizeLimit(0.375, 5),
    ],
    Speed.SLOW: [
        SizeLimit(0.125, 24),
        SizeLimit(0.25, 18),
        SizeLimit(0.375, 12),
        SizeLimit(0.5, 8),
    ],
}


def suggest_speed(thickness_in: float, diameter_in: float) -> Speed:
    """Suggest the fastest speed that still fits ``diameter_in`` at
    ``thickness_in``, per the manual's guidance table (p.14). Falls back to
    Slow if nothing else fits — "more projects are ruined by going too fast
    than too slow" (p.23).
    """

    for speed in (Speed.FAST, Speed.MEDIUM, Speed.SLOW):
        for limit in SIZE_SPEED_GUIDE[speed]:
            if limit.thickness_in >= thickness_in and limit.max_diameter_in:
                if diameter_in <= limit.max_diameter_in:
                    return speed
    return Speed.SLOW


# ---------------------------------------------------------------------------
# Critical temperature ranges (p.24) — reference data for the GlassFire
# program structure (segments 3-4, pp.14-15). Not shown on any diagram —
# the kiln is also used for non-glass annealing with unrelated targets, so
# the UI doesn't assume this range applies to whatever's actually loaded.
# ---------------------------------------------------------------------------

PRE_ANNEALING_TARGET_F = 960.0  # fast cool ends here
ANNEALING_END_F = 800.0  # slow cool (true annealing) ends here
