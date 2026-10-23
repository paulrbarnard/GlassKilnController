"""The kiln's 5 PRO slots as a cache for the app's custom profiles."""

import time

from kiln.bridge import MockBridge
from kiln.profiles import Profile, ProfileKind, ProfileLibrary
from kiln.programs import KilnProgram
from kiln.protocol import RampHoldSegment
from kiln.sequencer import ProgramMismatch, Sequencer
from kiln.slots import KilnSlots
from kiln.vision import MockVision
from web.api import register_routes

from tests.test_web_api import LIBRARY_DIR, FakeUI, _wait_for_threads


def seg(temp_f, rate=300.0, hold=10):
    return RampHoldSegment(rate_f_per_hr=rate, target_temp_f=temp_f, hold_minutes=hold)


def custom(library, pid, temp_f):
    profile = Profile(
        id=pid, name=pid.title(), description="", kind=ProfileKind.RAMP_HOLD,
        source="user", segments=[seg(temp_f)],
    )
    library.save_user_profile(profile)
    return library.get(pid)


def make(tmp_path):
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path / "user").load()
    slots = KilnSlots(path=tmp_path / "slots.json")
    return library, slots


def test_choose_slot_order(tmp_path):
    library, slots = make(tmp_path)
    a, b, c, d = (custom(library, n, t) for n, t in [("a", 1000), ("b", 1100), ("c", 1200), ("d", 1300)])
    for slot, p in zip((1, 2, 3, 4, 5), (a, b, c, d, a)):
        slots.assign(slot, p.id, p.segments)
    slots.usage = {"a": {"count": 5, "last_used": 100}, "b": {"count": 2, "last_used": 300},
                   "c": {"count": 2, "last_used": 200}, "d": {"count": 9, "last_used": 50}}
    assert slots.choose_slot(library) == 3  # c: least used, older than b

    slots.set_unknown(4)
    assert slots.choose_slot(library) == 4  # unrecognised beats everything

    slots.assign(4, "d", d.segments)
    custom(library, "b", 1150)  # b edited since it was written
    assert slots.choose_slot(library) == 2


def test_slot_map_survives_a_restart(tmp_path):
    library, slots = make(tmp_path)
    a = custom(library, "a", 1000)
    slots.assign(2, a.id, a.segments)
    slots.record_use("a")
    again = KilnSlots.load(tmp_path / "slots.json")
    assert again.slot_for(a) == 2 and again.uses("a") == 1


def test_record_kiln_read_matches_app_profiles(tmp_path):
    library, slots = make(tmp_path)
    a = custom(library, "a", 1000)
    assert slots.record_kiln_read(3, [seg(1000)], library) == "a"
    assert slots.slot_for(a) == 3
    assert slots.record_kiln_read(3, [seg(1400)], library) is None  # unrecognised
    assert slots.slot_for(a) is None


class FakeKiln:
    """Sequencer stand-in: a dict of slot -> segments, recording calls."""

    def __init__(self, sequencer, contents=None):
        self.contents, self.calls = dict(contents or {}), []
        sequencer.write_program = self.write
        sequencer.read_program = self.read
        sequencer.start_slot = self.start
        sequencer.check_controller_unit = lambda: None

    def write(self, slot, segments):
        self.calls.append(("write", slot))
        self.contents[slot] = list(segments)

    def read(self, slot):
        self.calls.append(("read", slot))
        return KilnProgram(slot=slot, segments=tuple(self.contents[slot]), celsius=True)

    def start(self, slot, expected):
        self.calls.append(("start", slot))
        from kiln.programs import same_segments

        if not same_segments(expected, self.contents.get(slot, [])):
            raise ProgramMismatch(slot, None)


def run(tmp_path, library, slots, kiln_contents, profile_id):
    sequencer = Sequencer(bridge=MockBridge(press_delay_s=0.0), vision=MockVision(script=["IdLE"]))
    kiln = FakeKiln(sequencer, kiln_contents)
    ui = FakeUI()
    register_routes(ui, sequencer, library, settings_path=tmp_path / "s.json", kiln_slots=slots)
    assert ui.call("POST", "/api/run/{profile_id}", profile_id=profile_id)["ok"] is True
    _wait_for_threads()
    return kiln, ui


def test_profile_already_on_the_kiln_starts_without_writing(tmp_path):
    library, slots = make(tmp_path)
    a = custom(library, "a", 1000)
    slots.assign(2, a.id, a.segments)
    kiln, _ = run(tmp_path, library, slots, {2: a.segments}, "a")
    assert kiln.calls == [("start", 2)]
    assert slots.uses("a") == 1


def test_profile_not_on_the_kiln_replaces_the_least_used(tmp_path):
    library, slots = make(tmp_path)
    profiles = [custom(library, n, 1000 + i * 50) for i, n in enumerate("abcdef")]
    for slot, p in zip(range(1, 6), profiles[:5]):
        slots.assign(slot, p.id, p.segments)
        slots.usage[p.id] = {"count": 10 - slot, "last_used": time.time()}
    # Slot 5 holds "e", fired the fewest times (5): "f" goes there.
    kiln, ui = run(tmp_path, library, slots, {s: p.segments for s, p in zip(range(1, 6), profiles)}, "f")
    assert kiln.calls == [("write", 5), ("read", 5), ("start", 5)]
    assert slots.slot_for(profiles[5]) == 5 and slots.uses("f") == 1
    assert any("replacing “E”, fired 5 times" in d.get("message", "") for e, d in ui.messages if e == "progress")


def test_a_slot_changed_behind_the_apps_back_is_rewritten(tmp_path):
    library, slots = make(tmp_path)
    a = custom(library, "a", 1000)
    slots.assign(2, a.id, a.segments)
    kiln, _ = run(tmp_path, library, slots, {2: [seg(1400)]}, "a")
    assert kiln.calls == [("start", 2), ("write", 2), ("read", 2), ("start", 2)]
    assert slots.slot_for(a) == 2
