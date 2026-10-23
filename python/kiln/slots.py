"""Which app profile is in each of the kiln's 5 PRO slots, and how often
each profile has been fired: the kiln's slots act as a cache for the
app's (up to 12) custom profiles.

When a custom profile is started and isn't already in a slot, it's
written into the slot chosen by ``choose_slot``:

1. a slot whose contents the app doesn't know or doesn't recognise
   (never read, or a program entered by hand at the kiln);
2. a slot holding a profile that has since been deleted or edited;
3. the slot holding the least-fired profile (least recently fired first
   on a tie).

Saved as JSON in python/state/ (kept out of git and deploys, like
settings.json), so it survives restarts.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from kiln.programs import same_segments
from kiln.protocol import MAX_PRO_SLOTS, RampHoldSegment

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "state" / "kiln_slots.json"


def _segments_to_list(segments) -> list[dict]:
    return [
        {"rate_f_per_hr": s.rate_f_per_hr, "target_temp_f": s.target_temp_f, "hold_minutes": s.hold_minutes}
        for s in segments
    ]


def _segments_from_list(items) -> list[RampHoldSegment]:
    return [
        RampHoldSegment(
            rate_f_per_hr=float(i["rate_f_per_hr"]),
            target_temp_f=float(i["target_temp_f"]),
            hold_minutes=float(i["hold_minutes"]),
        )
        for i in items
    ]


@dataclass
class SlotRecord:
    profile_id: str
    segments: list  # RampHoldSegment, as written to / read from the kiln
    updated_at: float


@dataclass
class KilnSlots:
    path: Path = DEFAULT_PATH
    # slot -> what it holds; None (or absent) means unknown/unrecognised.
    slots: dict = field(default_factory=dict)
    # profile id -> {"count": firings started, "last_used": epoch s}
    usage: dict = field(default_factory=dict)

    # -- persistence ------------------------------------------------------

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "KilnSlots":
        store = cls(path=Path(path))
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return store
        except (OSError, ValueError) as exc:
            log.warning("unreadable kiln slot map at %s (%s); starting empty", path, exc)
            return store
        for slot, rec in (data.get("slots") or {}).items():
            if rec:
                store.slots[int(slot)] = SlotRecord(
                    profile_id=rec["profile_id"],
                    segments=_segments_from_list(rec["segments"]),
                    updated_at=float(rec.get("updated_at", 0)),
                )
        store.usage = {k: dict(v) for k, v in (data.get("usage") or {}).items()}
        return store

    def save(self) -> None:
        data = {
            "slots": {
                str(slot): {
                    "profile_id": rec.profile_id,
                    "segments": _segments_to_list(rec.segments),
                    "updated_at": rec.updated_at,
                }
                for slot, rec in sorted(self.slots.items())
                if rec is not None
            },
            "usage": self.usage,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    # -- queries ----------------------------------------------------------

    def slot_for(self, profile) -> Optional[int]:
        """The slot holding this exact version of ``profile``, if any."""

        for slot in sorted(self.slots):
            rec = self.slots[slot]
            if rec and rec.profile_id == profile.id and same_segments(rec.segments, profile.segments or []):
                return slot
        return None

    def uses(self, profile_id: str) -> int:
        return int(self.usage.get(profile_id, {}).get("count", 0))

    def choose_slot(self, library) -> int:
        """The slot to overwrite for a profile that isn't on the kiln."""

        def rank(slot: int):
            rec = self.slots.get(slot)
            if rec is None:
                return (0, 0, 0.0)
            try:
                profile = library.get(rec.profile_id)
            except KeyError:
                return (1, 0, 0.0)  # deleted from the app
            if not same_segments(rec.segments, profile.segments or []):
                return (1, 0, 0.0)  # edited since it was written
            use = self.usage.get(rec.profile_id, {})
            return (2, int(use.get("count", 0)), float(use.get("last_used", 0.0)))

        return min(range(1, MAX_PRO_SLOTS + 1), key=lambda s: (rank(s), s))

    def describe(self, slot: int, library) -> str:
        """What's in ``slot``, for "replacing ..." messages."""

        rec = self.slots.get(slot)
        if rec is None:
            return "an unrecognised program"
        try:
            name = library.get(rec.profile_id).name
        except KeyError:
            return "a deleted profile"
        n = self.uses(rec.profile_id)
        return f"“{name}”, fired {n} time{'' if n == 1 else 's'}"

    # -- changes --------------------------------------------------------------

    def assign(self, slot: int, profile_id: str, segments) -> None:
        self.slots[slot] = SlotRecord(profile_id=profile_id, segments=list(segments), updated_at=time.time())
        self.save()

    def set_unknown(self, slot: int) -> None:
        self.slots.pop(slot, None)
        self.save()

    def record_kiln_read(self, slot: int, segments, library) -> Optional[str]:
        """After reading ``slot`` off the kiln: map it to the app profile
        with exactly those segments (preferring the one already recorded
        there), or mark it unrecognised. Returns the matched profile id."""

        current = self.slots.get(slot)
        candidates = [
            p for p in library.all()
            if p.kind.value == "ramp_hold" and p.segments and same_segments(p.segments, segments)
        ]
        if current and any(p.id == current.profile_id for p in candidates):
            match = current.profile_id
        else:
            match = candidates[0].id if candidates else None
        if match:
            self.assign(slot, match, segments)
        else:
            self.set_unknown(slot)
        return match

    def record_use(self, profile_id: str) -> None:
        use = self.usage.setdefault(profile_id, {"count": 0, "last_used": 0.0})
        use["count"] = int(use.get("count", 0)) + 1
        use["last_used"] = time.time()
        self.save()
