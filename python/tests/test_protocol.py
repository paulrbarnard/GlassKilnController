from kiln.protocol import (
    Process,
    Speed,
    resolve_glassfire_program,
    suggest_speed,
)


def test_resolve_glassfire_program_fills_in_process_segment():
    segments = resolve_glassfire_program(Speed.SLOW, Process.FULL_FUSE)
    assert len(segments) == 4
    # Segment 2 (index 1) is the process/soak segment: rate stays from the
    # speed table, temp/hold come from PROCESS_TABLE (p.32).
    assert segments[1].rate_f_per_hr == 400
    assert segments[1].soak_temp_f == 1465
    assert segments[1].hold_minutes == 10
    # Other segments are untouched.
    assert segments[0].soak_temp_f == 300
    assert segments[2].soak_temp_f == 950
    assert segments[3].soak_temp_f == 800


def test_resolve_glassfire_program_varies_by_speed_and_process():
    slow_slump = resolve_glassfire_program(Speed.SLOW, Process.SLUMP)
    fast_tack = resolve_glassfire_program(Speed.FAST, Process.TACK_FUSE)
    assert slow_slump[1].soak_temp_f == 1225
    assert fast_tack[1].soak_temp_f == 1325
    assert slow_slump[0].rate_f_per_hr != fast_tack[0].rate_f_per_hr


def test_suggest_speed_prefers_fastest_that_fits():
    # 1/8" thick, 10" diameter fits Fast's 18" limit (p.14).
    assert suggest_speed(thickness_in=0.125, diameter_in=10) == Speed.FAST
    # 1/4" thick, 10" diameter doesn't fit Fast (4" limit) but fits Medium (8"... )
    # actually 10 > 8, so falls to Slow (18" limit).
    assert suggest_speed(thickness_in=0.25, diameter_in=10) == Speed.SLOW
    # 1/2" thick only appears in the Slow table.
    assert suggest_speed(thickness_in=0.5, diameter_in=6) == Speed.SLOW
