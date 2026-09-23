"""Firing profiles: the human-facing unit of "what do I want the kiln to do".

A ``Profile`` is either:

- a **GlassFire** selection — a (speed, process) pair from the factory
  table, optionally with MGF temperature/hold overrides — or
- a custom **Ramp & Hold** program — an explicit list of
  ``protocol.RampHoldSegment``.

Either way, a ``Profile`` only knows about temperatures/times/rates — it has
no idea how to get the physical controller into that state. That's
``sequencer.py``'s job, using ``Profile.resolved_segments()`` as its input.

Profiles are stored as JSON: built-ins under ``profiles/library`` (generated
by ``generate_library.py`` from ``protocol.py`` — don't hand-edit, regenerate
instead) and user-created ones under ``profiles/user``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Optional

from kiln.protocol import (
    MAX_PRO_SEGMENTS,
    MAX_PRO_SLOTS,
    MAX_USER_PROFILES,
    Process,
    RampHoldSegment,
    Speed,
    resolve_glassfire_program,
)

LIBRARY_DIR = Path(__file__).resolve().parent.parent / "profiles" / "library"
USER_DIR = Path(__file__).resolve().parent.parent / "profiles" / "user"


class ProfileKind(Enum):
    GLASSFIRE = "glassfire"
    RAMP_HOLD = "ramp_hold"


@dataclass(frozen=True)
class GlassFireSelection:
    speed: Speed
    process: Process
    # Modify GlassFire (MGF) overrides — None means "use the factory
    # process target from protocol.PROCESS_TABLE" (p.32).
    temp_override_f: Optional[float] = None
    hold_override_minutes: Optional[float] = None


@dataclass
class Profile:
    id: str
    name: str
    description: str
    kind: ProfileKind
    source: str  # "library" or "user"
    glassfire: Optional[GlassFireSelection] = None
    segments: Optional[list[RampHoldSegment]] = None
    pro_slot: Optional[int] = None  # target PRO-mode slot (1-5), ramp_hold only

    def __post_init__(self) -> None:
        if self.kind is ProfileKind.GLASSFIRE and self.glassfire is None:
            raise ValueError(f"{self.id}: glassfire profile missing selection")
        if self.kind is ProfileKind.RAMP_HOLD:
            if not self.segments:
                raise ValueError(f"{self.id}: ramp_hold profile has no segments")
            if len(self.segments) > MAX_PRO_SEGMENTS:
                raise ValueError(
                    f"{self.id}: {len(self.segments)} segments exceeds "
                    f"controller max of {MAX_PRO_SEGMENTS}"
                )

    def resolved_segments(self) -> list[RampHoldSegment]:
        """The concrete rate/temp/hold segments this profile represents,
        regardless of whether it's a GlassFire selection or a custom
        Ramp & Hold program. This is what diagrams are drawn from and what
        the sequencer ultimately programs into the controller.
        """

        if self.kind is ProfileKind.RAMP_HOLD:
            assert self.segments is not None
            return self.segments

        assert self.glassfire is not None
        gf_segments = resolve_glassfire_program(
            self.glassfire.speed, self.glassfire.process
        )
        out = []
        for i, seg in enumerate(gf_segments):
            temp = seg.soak_temp_f
            hold = seg.hold_minutes
            if i == 1:  # process/soak segment — where MGF overrides apply
                if self.glassfire.temp_override_f is not None:
                    temp = self.glassfire.temp_override_f
                if self.glassfire.hold_override_minutes is not None:
                    hold = self.glassfire.hold_override_minutes
            out.append(
                RampHoldSegment(
                    rate_f_per_hr=seg.rate_f_per_hr,
                    target_temp_f=temp if temp is not None else 0.0,
                    hold_minutes=hold if hold is not None else 0.0,
                )
            )
        return out

    # -- JSON (de)serialization -------------------------------------------

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind.value,
            "source": self.source,
        }
        if self.glassfire is not None:
            d["glassfire"] = {
                "speed": self.glassfire.speed.value,
                "process": self.glassfire.process.value,
                "temp_override_f": self.glassfire.temp_override_f,
                "hold_override_minutes": self.glassfire.hold_override_minutes,
            }
        if self.segments is not None:
            d["segments"] = [asdict(s) for s in self.segments]
        if self.pro_slot is not None:
            d["pro_slot"] = self.pro_slot
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        kind = ProfileKind(d["kind"])
        glassfire = None
        if "glassfire" in d:
            gf = d["glassfire"]
            glassfire = GlassFireSelection(
                speed=Speed(gf["speed"]),
                process=Process(gf["process"]),
                temp_override_f=gf.get("temp_override_f"),
                hold_override_minutes=gf.get("hold_override_minutes"),
            )
        segments = None
        if "segments" in d:
            segments = [RampHoldSegment(**s) for s in d["segments"]]
        return cls(
            id=d["id"],
            name=d["name"],
            description=d.get("description", ""),
            kind=kind,
            source=d.get("source", "user"),
            glassfire=glassfire,
            segments=segments,
            pro_slot=d.get("pro_slot"),
        )


def load_profile(path: Path) -> Profile:
    with open(path, "r", encoding="utf-8") as fh:
        return Profile.from_dict(json.load(fh))


def save_profile(profile: Profile, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{profile.id}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(profile.to_dict(), fh, indent=2)
        fh.write("\n")
    return path


@dataclass
class ProfileLibrary:
    """Loads built-in + user profiles and keeps them queryable by id."""

    library_dir: Path = LIBRARY_DIR
    user_dir: Path = USER_DIR
    _profiles: dict[str, Profile] = field(default_factory=dict, repr=False)

    def load(self) -> "ProfileLibrary":
        self._profiles.clear()
        for directory in (self.library_dir, self.user_dir):
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.json")):
                try:
                    profile = load_profile(path)
                except (ValueError, KeyError) as exc:
                    raise ValueError(f"invalid profile file {path}: {exc}") from exc
                self._profiles[profile.id] = profile
        return self

    def all(self) -> list[Profile]:
        return list(self._profiles.values())

    def get(self, profile_id: str) -> Profile:
        return self._profiles[profile_id]

    def _persist_as_user(self, profile: Profile) -> Path:
        profile.source = "user"
        path = save_profile(profile, self.user_dir)
        self._profiles[profile.id] = profile
        return path

    def user_ramp_hold_count(self) -> int:
        return sum(
            1
            for p in self._profiles.values()
            if p.source == "user" and p.kind is ProfileKind.RAMP_HOLD
        )

    def save_user_profile(self, profile: Profile) -> Path:
        is_new = profile.id not in self._profiles
        if (
            is_new
            and profile.kind is ProfileKind.RAMP_HOLD
            and self.user_ramp_hold_count() >= MAX_USER_PROFILES
        ):
            raise ValueError(
                f"already have {MAX_USER_PROFILES} saved custom profiles "
                "(the most this app keeps) — delete one before adding another"
            )
        return self._persist_as_user(profile)

    def active_slot_holder(self, slot: int) -> Optional[Profile]:
        return next((p for p in self._profiles.values() if p.pro_slot == slot), None)

    def set_active_slot(self, profile_id: str, slot: Optional[int]) -> Profile:
        """Assign (or clear) which physical PRO slot (1-5) a Ramp & Hold
        profile is "active" in. At most one profile holds a given slot —
        assigning it here deactivates whoever held it before (persisted as
        a user shadow if that was a library profile, e.g. the factory
        Casting program in slot 5 — the exact same shadowing
        ``delete_user_profile`` relies on to "un-edit" a profile).

        This only tracks *intent* (which recipe should live in which slot)
        — it does not touch the physical controller. ``Sequencer.
        program_ramp_hold`` still fully re-enters the segment data via
        button presses every time a profile is run, so the controller's
        actual slot contents are never assumed to already be correct from
        a previous activation (see docs/architecture.md "press, then
        verify").
        """

        profile = self._profiles.get(profile_id)
        if profile is None or profile.kind is not ProfileKind.RAMP_HOLD:
            raise ValueError(f"no custom profile named {profile_id!r}")

        if slot is not None:
            if not 1 <= slot <= MAX_PRO_SLOTS:
                raise ValueError(f"slot must be 1-{MAX_PRO_SLOTS}, got {slot}")
            holder = self.active_slot_holder(slot)
            if holder is not None and holder.id != profile_id:
                holder.pro_slot = None
                self._persist_as_user(holder)

        profile.pro_slot = slot
        self._persist_as_user(profile)
        return profile

    def set_mgf_override(
        self,
        profile_id: str,
        temp_override_f: Optional[float],
        hold_override_minutes: Optional[float],
    ) -> Profile:
        """Persistently override a GlassFire profile's process temp/hold —
        the app-level equivalent of the controller's MGF feature (pp.15-17),
        except MGF only ever prompts per-run on the physical device; this
        remembers the choice so it applies every time this profile runs.
        Only the process (segment 2) values are touched — segments 1/3/4
        stay the fixed factory values, matching what MGF itself can do (see
        docs/controller_protocol.md).
        """

        profile = self._profiles.get(profile_id)
        if profile is None or profile.kind is not ProfileKind.GLASSFIRE:
            raise ValueError(f"no GlassFire profile named {profile_id!r}")
        assert profile.glassfire is not None
        profile.glassfire = replace(
            profile.glassfire,
            temp_override_f=temp_override_f,
            hold_override_minutes=hold_override_minutes,
        )
        self._persist_as_user(profile)
        return profile

    def delete_user_profile(self, profile_id: str) -> None:
        profile = self._profiles.get(profile_id)
        if profile is None or profile.source != "user":
            raise ValueError(f"no user profile named {profile_id!r}")
        path = self.user_dir / f"{profile_id}.json"
        path.unlink(missing_ok=True)
        # Reload rather than just dropping the dict entry: if this id also
        # exists under library_dir (i.e. this was an edited factory
        # profile "shadowing" it — see Profile editing in web/api.py), the
        # library version should reappear now that the override is gone,
        # not disappear from the list entirely.
        self.load()
