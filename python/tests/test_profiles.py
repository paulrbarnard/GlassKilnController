import json

from kiln.profiles import (
    LIBRARY_DIR,
    GlassFireSelection,
    Profile,
    ProfileKind,
    ProfileLibrary,
)
from kiln.protocol import MAX_USER_PROFILES, Process, RampHoldSegment, Speed


def make_ramp_hold(id_, pro_slot=None):
    return Profile(
        id=id_,
        name=id_,
        description="",
        kind=ProfileKind.RAMP_HOLD,
        source="user",
        segments=[RampHoldSegment(rate_f_per_hr=200, target_temp_f=1000, hold_minutes=10)],
        pro_slot=pro_slot,
    )


def test_glassfire_profile_round_trips_through_json():
    profile = Profile(
        id="test_full_slow",
        name="Test",
        description="desc",
        kind=ProfileKind.GLASSFIRE,
        source="library",
        glassfire=GlassFireSelection(speed=Speed.SLOW, process=Process.FULL_FUSE),
    )
    rebuilt = Profile.from_dict(profile.to_dict())
    assert rebuilt.glassfire == profile.glassfire
    assert rebuilt.resolved_segments() == profile.resolved_segments()


def test_glassfire_profile_applies_mgf_overrides_only_to_process_segment():
    profile = Profile(
        id="test_override",
        name="Test",
        description="",
        kind=ProfileKind.GLASSFIRE,
        source="user",
        glassfire=GlassFireSelection(
            speed=Speed.MEDIUM,
            process=Process.TACK_FUSE,
            temp_override_f=1350,
            hold_override_minutes=20,
        ),
    )
    segments = profile.resolved_segments()
    assert segments[1].target_temp_f == 1350
    assert segments[1].hold_minutes == 20
    # Non-process segments are unaffected by the override.
    assert segments[0].target_temp_f == 300


def test_ramp_hold_profile_round_trips_and_rejects_too_many_segments():
    segments = [RampHoldSegment(rate_f_per_hr=200, target_temp_f=1000, hold_minutes=10)] * 9
    try:
        Profile(
            id="too_many",
            name="Too Many",
            description="",
            kind=ProfileKind.RAMP_HOLD,
            source="user",
            segments=segments,
        )
        assert False, "expected ValueError for >8 segments"
    except ValueError:
        pass


def test_library_loads_generated_builtin_profiles():
    library = ProfileLibrary().load()
    all_profiles = library.all()
    ids = {p.id for p in all_profiles}
    # 3 processes x 3 speeds + the factory casting program (see
    # generate_library.py), all sourced from protocol.py's factory tables.
    assert "full_fuse_slow" in ids
    assert "slump_fast" in ids
    assert "casting_factory" in ids
    assert len(all_profiles) >= 10
    assert all(p.source == "library" for p in all_profiles)


def test_set_active_slot_swaps_out_previous_holder(tmp_path):
    library = ProfileLibrary(library_dir=tmp_path, user_dir=tmp_path)
    a, b = make_ramp_hold("a"), make_ramp_hold("b")
    library._profiles = {"a": a, "b": b}

    library.set_active_slot("a", 1)
    assert library.get("a").pro_slot == 1

    library.set_active_slot("b", 1)  # same slot — should bump "a" out
    assert library.get("b").pro_slot == 1
    assert library.get("a").pro_slot is None
    # The bump is persisted, not just in-memory.
    assert (tmp_path / "a.json").exists()
    assert Profile.from_dict(json.loads((tmp_path / "a.json").read_text())).pro_slot is None


def test_set_active_slot_shadows_a_library_profile(tmp_path):
    # casting_factory ships active in slot 5 (see generate_library.py) —
    # assigning a different profile to slot 5 must deactivate it via a
    # user-dir shadow, not mutate the library file.
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    assert library.get("casting_factory").pro_slot == 5

    contender = make_ramp_hold("contender")
    library._profiles["contender"] = contender
    library.set_active_slot("contender", 5)

    assert library.get("contender").pro_slot == 5
    assert library.get("casting_factory").pro_slot is None
    assert (tmp_path / "casting_factory.json").exists()  # shadow written to user_dir
    # The library file itself is untouched — still the factory default.
    library_copy = json.loads((LIBRARY_DIR / "casting_factory.json").read_text())
    assert library_copy["pro_slot"] == 5
    shadow_copy = json.loads((tmp_path / "casting_factory.json").read_text())
    assert "pro_slot" not in shadow_copy

    # A fresh load reflects the shadow, not the untouched library default.
    reloaded = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    assert reloaded.get("casting_factory").pro_slot is None
    assert reloaded.get("casting_factory").source == "user"


def test_set_active_slot_rejects_out_of_range_and_non_ramp_hold(tmp_path):
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    try:
        library.set_active_slot("full_fuse_slow", 1)  # GlassFire, not ramp_hold
        assert False, "expected ValueError"
    except ValueError:
        pass
    try:
        library.set_active_slot("casting_factory", 6)  # out of range
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_save_user_profile_enforces_max_saved_custom_profiles(tmp_path):
    library = ProfileLibrary(library_dir=tmp_path, user_dir=tmp_path)
    for i in range(MAX_USER_PROFILES):
        library.save_user_profile(make_ramp_hold(f"p{i}"))
    assert library.custom_count() == MAX_USER_PROFILES

    try:
        library.save_user_profile(make_ramp_hold("one_too_many"))
        assert False, "expected ValueError at the cap"
    except ValueError:
        pass

    # Editing an existing one (same id) is still fine even right at the cap.
    library.save_user_profile(make_ramp_hold("p0", pro_slot=2))
    assert library.get("p0").pro_slot == 2


def test_set_mgf_override_persists_across_reload(tmp_path):
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    library.set_mgf_override("full_fuse_slow", temp_override_f=1400, hold_override_minutes=25)

    reloaded = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    profile = reloaded.get("full_fuse_slow")
    assert profile.source == "user"
    segments = profile.resolved_segments()
    assert segments[1].target_temp_f == 1400
    assert segments[1].hold_minutes == 25
    # Only the process segment changed — the fixed ramp segments didn't.
    assert segments[0].target_temp_f == 300


def test_the_built_in_casting_profile_counts_towards_the_cap(tmp_path):
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    assert library.custom_count() == 1  # Casting is just another custom profile
    for i in range(MAX_USER_PROFILES - 1):
        library.save_user_profile(make_ramp_hold(f"p{i}"))
    try:
        library.save_user_profile(make_ramp_hold("one_too_many"))
        assert False, "expected ValueError at the cap"
    except ValueError as exc:
        assert "12 custom profiles" in str(exc)
    # Editing one that exists is still fine at the cap.
    library.save_user_profile(make_ramp_hold("p0"))


def test_deleting_casting_removes_it_for_good(tmp_path):
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    library.delete_user_profile("casting_factory")
    assert "casting_factory" not in {p.id for p in library.all()}
    reloaded = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    assert "casting_factory" not in {p.id for p in reloaded.all()}
    assert reloaded.custom_count() == 0


def test_deleting_an_edited_casting_also_removes_it(tmp_path):
    library = ProfileLibrary(library_dir=LIBRARY_DIR, user_dir=tmp_path).load()
    library.set_active_slot("casting_factory", None)  # writes a user copy
    library.delete_user_profile("casting_factory")
    assert "casting_factory" not in {p.id for p in library.all()}
