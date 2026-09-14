from kiln.profiles import (
    GlassFireSelection,
    Profile,
    ProfileKind,
    ProfileLibrary,
)
from kiln.protocol import Process, RampHoldSegment, Speed


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
