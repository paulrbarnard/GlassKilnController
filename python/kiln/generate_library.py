"""Regenerate profiles/library/*.json from protocol.py's factory tables.

The 9 GlassFire combinations (3 processes x 3 speeds) and the factory
casting program are derived data, not hand-authored content — this script
is the one place that turns ``protocol.py`` into the JSON files
``ProfileLibrary`` actually loads at runtime. Run it after any change to the
factory tables in ``protocol.py``:

    python -m kiln.generate_library

Do not hand-edit files under profiles/library — edit protocol.py (and
docs/controller_protocol.md alongside it) and regenerate instead.
"""

from __future__ import annotations

from kiln.profiles import LIBRARY_DIR, GlassFireSelection, Profile, ProfileKind, save_profile
from kiln.protocol import FACTORY_CASTING_PROGRAM, FACTORY_CASTING_SLOT, Process, Speed

PROCESS_LABEL = {
    Process.FULL_FUSE: "Full Fuse",
    Process.TACK_FUSE: "Tack Fuse",
    Process.SLUMP: "Slump",
}

PROCESS_DESCRIPTION = {
    Process.FULL_FUSE: (
        "Glass melts completely together to ~1/4\" (6.4mm) thick with "
        "rounded edges and an undistorted shape."
    ),
    Process.TACK_FUSE: (
        "A slightly more aggressive \"fuse to stick\": pieces bond together "
        "without fully rounding the edges."
    ),
    Process.SLUMP: (
        "Glass bends over or into a mold under heat and gravity. Degree of "
        "slump varies by mold — expect to tweak temperature/hold per mold."
    ),
}

SPEED_LABEL = {
    Speed.SLOW: "Slow",
    Speed.MEDIUM: "Medium",
    Speed.FAST: "Fast",
}

SPEED_DESCRIPTION = {
    Speed.SLOW: "For larger/thicker pieces. Gentlest ramp; use when unsure.",
    Speed.MEDIUM: "For moderate-size pieces.",
    Speed.FAST: "For small pieces only (see size guide) — thin, small diameter.",
}


def build_glassfire_profiles() -> list[Profile]:
    profiles = []
    for process in (Process.FULL_FUSE, Process.TACK_FUSE, Process.SLUMP):
        for speed in (Speed.SLOW, Speed.MEDIUM, Speed.FAST):
            profile_id = f"{process.name.lower()}_{speed.name.lower()}"
            profiles.append(
                Profile(
                    id=profile_id,
                    name=f"{PROCESS_LABEL[process]} — {SPEED_LABEL[speed]}",
                    description=(
                        f"{PROCESS_DESCRIPTION[process]} {SPEED_DESCRIPTION[speed]}"
                    ),
                    kind=ProfileKind.GLASSFIRE,
                    source="library",
                    glassfire=GlassFireSelection(speed=speed, process=process),
                )
            )
    return profiles


def build_casting_profile() -> Profile:
    return Profile(
        id="casting_factory",
        name="Casting",
        description=(
            "The Ramp & Hold casting schedule the GM LT ships with (in its PRO "
            "slot 5, which a controller factory reset restores)."
        ),
        kind=ProfileKind.RAMP_HOLD,
        source="library",
        segments=list(FACTORY_CASTING_PROGRAM),
        pro_slot=FACTORY_CASTING_SLOT,
    )


def main() -> None:
    profiles = build_glassfire_profiles() + [build_casting_profile()]
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    for profile in profiles:
        path = save_profile(profile, LIBRARY_DIR)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
