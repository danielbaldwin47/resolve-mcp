"""How varied the cutting is, read straight from a list of dicts.

Every case here is hand-written rows in and a dict out — no fake studio, no beats file, no job
poll — because that is what the module is: arithmetic over three columns (``t``, ``seconds``,
``clip``) and a loudness curve. The interesting cases are the ones a whole-report test cannot
reach without building a concert to reach them: a ladder that varies every length and is still
mechanical, a passage held through by one shot, a burst that is not two orphans.
"""

from __future__ import annotations

from typing import Any

from resolve_mcp.analysis import rhythm

QUIET_DBFS, MID_DBFS, LOUD_DBFS = -40.0, -30.0, -20.0
"""Three flat levels, thirty windows each: a curve whose terciles are known by construction."""


def _curve() -> list[tuple[float, float]]:
    """Ninety one-second windows — quiet for thirty, then mid, then loud."""
    return [
        (float(index), QUIET_DBFS if index < 30 else MID_DBFS if index < 60 else LOUD_DBFS)
        for index in range(90)
    ]


def _rows(*shots: tuple[float, float], clips: str = "AB") -> list[dict[str, Any]]:
    """Records from ``(start, length)`` pairs, angles cycling through ``clips``."""
    return [
        {"t": start, "seconds": length, "clip": clips[index % len(clips)]}
        for index, (start, length) in enumerate(shots)
    ]


# --- the shot lengths -------------------------------------------------------------------------


def test_the_bins_are_the_corpus_ones_and_half_open_on_the_upper_edge() -> None:
    read = rhythm.read(_rows((0.0, 1.5), (2.0, 4.0), (6.0, 30.0)))
    assert read["lengths"]["histogram"] == {
        "<2": 1,
        "2-4": 0,
        "4-8": 1,
        "8-15": 0,
        "15-30": 0,
        ">30": 1,
    }


def test_every_bin_is_present_even_when_it_holds_nothing() -> None:
    assert set(rhythm.read(_rows((0.0, 3.0)))["lengths"]["histogram"]) == {
        label for label, _ in rhythm.RHYTHM_BINS
    }


def test_the_spread_ratio_is_none_when_the_shortest_shot_rounds_to_zero() -> None:
    assert rhythm.read(_rows((0.0, 0.0), (0.0, 8.0)))["lengths"]["spread_ratio"] is None


def test_no_shots_reads_as_no_lengths_rather_than_as_zero() -> None:
    read = rhythm.read([])
    assert read["shots"] == 0
    assert read["lengths"]["mean"] is None
    assert read["uniformity"] == {"bin": None, "one_bin": None, "cv": None}
    assert read["reads_metronomic"] is False


# --- alternation ------------------------------------------------------------------------------


def test_a_strict_ab_trade_is_counted_in_cuts() -> None:
    read = rhythm.read(_rows((0.0, 2.0), (2.0, 2.0), (4.0, 2.0), (6.0, 2.0), (8.0, 2.0)))
    assert read["alternation"] == {"cuts": 4, "longest_run": 4, "fraction": 1.0}


def test_a_third_angle_ends_the_run() -> None:
    rows = _rows((0.0, 2.0), (2.0, 2.0), (4.0, 2.0), (6.0, 2.0), (8.0, 2.0), clips="ABABC")
    assert rhythm.read(rows)["alternation"]["longest_run"] == 3


def test_the_same_angle_twice_ends_the_run() -> None:
    rows = _rows((0.0, 2.0), (2.0, 2.0), (4.0, 2.0), (6.0, 2.0), clips="ABBA")
    assert rhythm.read(rows)["alternation"]["longest_run"] == 0


def test_two_shots_that_differ_are_a_cut_rather_than_alternation() -> None:
    read = rhythm.read(_rows((0.0, 2.0), (2.0, 2.0)))
    assert read["alternation"] == {"cuts": 1, "longest_run": 0, "fraction": 0.0}


def test_black_is_an_angle_in_the_trade_rather_than_a_hole_in_it() -> None:
    rows = _rows((0.0, 2.0), (2.0, 2.0), (4.0, 2.0), (6.0, 2.0))
    for index in (1, 3):
        rows[index]["clip"] = None
    assert rhythm.read(rows)["alternation"]["longest_run"] == 3


# --- uniformity and the ramp ------------------------------------------------------------------


def test_one_bin_is_the_fullest_bins_share_of_the_shots() -> None:
    read = rhythm.read(_rows((0.0, 3.0), (3.0, 3.0), (6.0, 3.0), (9.0, 20.0)))
    assert read["uniformity"]["bin"] == "2-4"
    assert read["uniformity"]["one_bin"] == 0.75


def test_a_ladder_that_only_shortens_is_a_ramp() -> None:
    read = rhythm.read(_rows((0.0, 10.0), (10.0, 8.0), (18.0, 7.0), (25.0, 5.0), (30.0, 3.0)))
    assert read["ramp"] == {"cuts": 4, "longest_run": 4, "fraction": 1.0}


def test_a_step_back_up_ends_the_ramp() -> None:
    read = rhythm.read(_rows((0.0, 10.0), (10.0, 8.0), (18.0, 7.0), (25.0, 9.0), (34.0, 3.0)))
    assert read["ramp"]["longest_run"] == 0


def test_equal_neighbours_end_a_ramp_rather_than_continue_it() -> None:
    read = rhythm.read(_rows((0.0, 5.0), (5.0, 5.0), (10.0, 5.0), (15.0, 5.0), (20.0, 5.0)))
    assert read["ramp"]["longest_run"] == 0


# --- the metronome verdict --------------------------------------------------------------------


def test_a_two_camera_trade_on_one_length_reads_metronomic() -> None:
    rows = _rows(*[(float(index * 3), 3.0) for index in range(12)])
    read = rhythm.read(rows)
    assert read["reads_metronomic"] is True


def test_a_ladder_reads_metronomic_even_though_every_length_differs() -> None:
    # P3·R3: strict two-framing, 9.9 s down to 2.9 s without a step back up. The bin and the
    # spread both call this varied; the ramp is what catches it.
    lengths = [9.9, 8.9, 7.9, 6.9, 5.9, 4.9, 3.9, 2.9]
    start = 0.0
    shots = []
    for length in lengths:
        shots.append((start, length))
        start += length
    read = rhythm.read(_rows(*shots))
    assert read["uniformity"]["one_bin"] < rhythm.ONE_BIN_FLOOR
    assert read["reads_metronomic"] is True


def test_a_varied_cut_on_two_cameras_does_not_read_metronomic() -> None:
    read = rhythm.read(_rows((0.0, 2.0), (2.0, 14.0), (16.0, 3.0), (19.0, 25.0), (44.0, 5.0)))
    assert read["reads_metronomic"] is False


def test_the_heuristic_is_carried_in_the_block_with_the_numbers_it_was_drawn_at() -> None:
    read = rhythm.read(_rows((0.0, 3.0)))
    assert read["heuristic"] == rhythm.HEURISTIC
    assert str(rhythm.ALTERNATION_FLOOR) in read["heuristic"]


# --- the gearing ------------------------------------------------------------------------------


def test_no_level_curve_is_no_gearing_rather_than_a_flat_one() -> None:
    assert rhythm.read(_rows((0.0, 3.0), (3.0, 3.0)))["gears"] is None


def test_a_curve_that_does_not_reach_the_cut_is_no_gearing_either() -> None:
    away = [(float(index) + 500.0, LOUD_DBFS) for index in range(30)]
    assert rhythm.read(_rows((0.0, 3.0), (3.0, 3.0)), away)["gears"] is None


def _geared() -> dict[str, Any]:
    """Three holds in the quiet third, three in the mid, thirty one-second shots in the loud."""
    shots = [(0.0, 10.0), (10.0, 10.0), (20.0, 10.0), (30.0, 10.0), (40.0, 10.0), (50.0, 10.0)]
    shots += [(60.0 + index, 1.0) for index in range(30)]
    return rhythm.read(_rows(*shots), _curve())


def test_each_tercile_holds_a_third_of_the_music_and_its_own_rate() -> None:
    gears = _geared()
    assert gears is not None
    terciles = gears["gears"]["terciles"]
    assert [terciles[gear]["seconds"] for gear in (rhythm.QUIET, rhythm.MID, rhythm.LOUD)] == [
        30.0,
        30.0,
        30.0,
    ]
    assert terciles[rhythm.QUIET]["cuts_per_minute"] == 6.0
    assert terciles[rhythm.LOUD]["cuts_per_minute"] == 60.0
    assert terciles[rhythm.LOUD]["level_dbfs"] == LOUD_DBFS


def test_the_rate_ratio_is_the_loud_third_against_the_quiet_one() -> None:
    assert _geared()["gears"]["rate_ratio"] == 10.0


def test_a_cut_that_changes_gear_is_not_one_speed() -> None:
    assert _geared()["gears"]["one_speed"] is False


def test_the_short_shots_are_counted_where_they_sit() -> None:
    gears = _geared()["gears"]
    assert gears["sub2s_count"] == 30
    assert gears["sub2s_in_loud"] == 30
    assert gears["sub2s_loud_fraction"] == 1.0


def test_a_shot_past_the_curve_is_counted_outside_rather_than_in_a_tercile() -> None:
    shots = [(0.0, 10.0), (10.0, 10.0), (20.0, 10.0), (200.0, 4.0)]
    gears = rhythm.read(_rows(*shots), _curve())["gears"]
    assert gears["outside_shots"] == 1
    assert sum(gears["terciles"][gear]["shots"] for gear in gears["terciles"]) == 3


def test_one_speed_is_true_where_the_rate_barely_moves_and_the_lengths_do_not() -> None:
    shots = [(float(index) * 3.0, 3.0) for index in range(30)]
    gears = rhythm.read(_rows(*shots), _curve())["gears"]
    assert gears["rate_ratio"] is not None
    assert gears["rate_ratio"] < rhythm.RATE_RATIO_FLOOR
    assert gears["one_speed"] is True


def test_the_gear_heuristic_is_carried_beside_its_numbers() -> None:
    gears = _geared()["gears"]
    assert gears["heuristic"] == rhythm.GEAR_HEURISTIC
    assert gears["window_seconds"] == rhythm.GEAR_WINDOW_SECONDS


# --- the quiet floor --------------------------------------------------------------------------


def _filler() -> list[tuple[float, float]]:
    """Two-second shots from 30 s to the end of the curve.

    The terciles are taken over the windows the *cut* spans, so a passage test whose shots all
    sit in the first thirty seconds would be splitting a flat thirty-second curve three ways
    rather than reading the quiet third of a ninety-second one. These carry the span out to the
    end of the curve and start after the quiet passage, so they are outside every reading here.
    """
    return [(30.0 + index * 2.0, 2.0) for index in range(30)]


def _floor(*shots: tuple[float, float]) -> dict[str, Any]:
    """The ``quiet_floor`` block for shots read against the known curve."""
    floor: dict[str, Any] = rhythm.read(_rows(*shots, *_filler()), _curve())["gears"][
        "quiet_floor"
    ]
    return floor


def test_the_quiet_passage_is_the_smoothed_bottom_third() -> None:
    floor = _floor((0.0, 10.0), (10.0, 10.0), (20.0, 10.0))
    assert len(floor["runs"]) == 1
    passage = floor["runs"][0]
    assert (passage["from"], passage["to"], passage["seconds"]) == (0.0, 30.0, 30.0)
    assert passage["shots"] == 3
    assert passage["cuts_per_minute"] == 6.0


def test_holds_of_one_length_read_locked() -> None:
    floor = _floor((0.0, 10.0), (10.0, 10.0), (20.0, 10.0))
    assert floor["runs"][0]["cv"] == 0.0
    assert floor["runs"][0]["reads_locked"] is True
    assert floor["reads_locked"] is True


def test_a_lone_flash_between_holds_is_an_orphan_and_does_not_buy_a_spread() -> None:
    floor = _floor((0.0, 10.0), (10.0, 2.0), (12.0, 10.0), (22.0, 10.0))
    passage = floor["runs"][0]
    assert passage["orphans"] == 1
    assert passage["orphan_seconds"] == [2.0]
    assert passage["cv"] > passage["cv_less_orphans"] == 0.0
    assert passage["reads_locked"] is True


def test_two_short_shots_side_by_side_are_a_burst_and_stay_in_the_spread() -> None:
    floor = _floor((0.0, 4.0), (4.0, 4.0), (8.0, 20.0), (28.0, 12.0))
    passage = floor["runs"][0]
    assert passage["orphans"] == 0
    assert passage["cv_less_orphans"] == passage["cv"] > rhythm.FLOOR_CV_FLOOR
    assert passage["reads_locked"] is False
    assert floor["reads_locked"] is False


def test_a_passage_crossed_by_one_hold_reads_locked_on_that_shot() -> None:
    floor = _floor((-10.0, 60.0), (50.0, 4.0), (54.0, 4.0))
    passage = floor["runs"][0]
    assert passage["shots"] == 0
    assert passage["held_through_seconds"] == 60.0
    assert passage["reads_locked"] is True


def test_a_passage_the_film_does_not_cover_is_not_held_through() -> None:
    floor = _floor((-10.0, 5.0), (50.0, 4.0), (54.0, 4.0))
    passage = floor["runs"][0]
    assert passage["shots"] == 0
    assert passage["held_through_seconds"] is None
    assert passage["reads_locked"] is False


def test_a_pocket_too_short_to_sit_in_is_no_passage() -> None:
    # Three ten-second dips rather than one long one: the quiet third is the same size either
    # way, and every run in it is under QUIET_FLOOR_SECONDS.
    dips = {index for start in (10, 40, 70) for index in range(start, start + 10)}
    curve = [
        (float(index), QUIET_DBFS if index in dips else LOUD_DBFS + index * 0.01)
        for index in range(90)
    ]
    rows = _rows(*[(float(index) * 3.0, 3.0) for index in range(30)])
    floor = rhythm.read(rows, curve)["gears"]["quiet_floor"]
    assert floor["runs"] == []
    assert floor["reads_locked"] is False


def test_the_floor_heuristic_is_carried_with_the_smoothing_it_was_read_at() -> None:
    floor = _floor((0.0, 10.0), (10.0, 10.0), (20.0, 10.0))
    assert floor["heuristic"] == rhythm.FLOOR_HEURISTIC
    assert floor["smoothing_windows"] == rhythm.QUIET_SMOOTHING_WINDOWS
