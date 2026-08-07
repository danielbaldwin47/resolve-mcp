"""Turning two lists of times into bars — the part of beat detection that is not a model.

ADR 0002 puts the model behind a seam precisely so that everything downstream of it is
ordinary code under test. This is that code: bar numbering, downbeat matching, and the
tempo and meter stats the gist reports. A jazz set is not all in four and does not always
start on the one, so neither assumption is allowed to go unchecked.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from resolve_mcp.analysis import beats
from resolve_mcp.analysis.beats import BeatGrid
from resolve_mcp.errors import AnalysisDependencyError, AnalysisFailedError


def _steady(count: int, step: float = 0.5, every: int = 4, first: int = 0) -> BeatGrid:
    times = tuple(round(index * step, 6) for index in range(count))
    return BeatGrid(beats=times, downbeats=times[first::every])


def test_bars_are_counted_from_the_downbeats_the_model_gave() -> None:
    records = beats.numbered(_steady(9))

    assert [record["bar"] for record in records] == [1, 1, 1, 1, 2, 2, 2, 2, 3]
    assert [record["in_bar"] for record in records] == [1, 2, 3, 4, 1, 2, 3, 4, 1]


def test_a_tune_in_five_numbers_in_five() -> None:
    records = beats.numbered(_steady(11, every=5))

    assert [record["in_bar"] for record in records] == [1, 2, 3, 4, 5, 1, 2, 3, 4, 5, 1]
    assert beats.gist(_steady(11, every=5), records)["meter"] == 5


def test_a_pickup_before_the_first_downbeat_counts_as_bar_one() -> None:
    """Counting a horn's two pickup notes as bar zero would offset every bar after them."""
    records = beats.numbered(_steady(10, first=2))

    assert [record["bar"] for record in records][:4] == [1, 1, 2, 2]
    assert records[0]["downbeat"] is False
    assert records[2]["downbeat"] is True
    assert records[2]["in_bar"] == 1


def test_a_downbeat_a_hair_off_its_beat_still_marks_that_beat() -> None:
    """The model reports the two separately; float equality would silently drop the mark."""
    grid = BeatGrid(beats=(0.0, 0.5, 1.0, 1.5), downbeats=(0.0, 1.0009))

    records = beats.numbered(grid)

    assert [record["downbeat"] for record in records] == [True, False, True, False]


def test_a_downbeat_nowhere_near_a_beat_marks_nothing() -> None:
    grid = BeatGrid(beats=(0.0, 0.5, 1.0, 1.5), downbeats=(0.0, 1.2))

    assert [record["downbeat"] for record in beats.numbered(grid)] == [True, False, False, False]


def test_the_gist_reports_tempo_from_the_intervals() -> None:
    grid = _steady(9)

    found = beats.gist(grid, beats.numbered(grid))

    assert found["tempo_bpm"] == pytest.approx(120.0)
    assert found["tempo_min_bpm"] == pytest.approx(120.0)
    assert found["count"] == 9
    assert found["downbeat_count"] == 3
    assert found["first_seconds"] == 0.0
    assert found["last_seconds"] == 4.0


def test_the_gist_of_an_empty_grid_says_nothing_rather_than_dividing_by_zero() -> None:
    grid = BeatGrid((), ())

    found = beats.gist(grid, beats.numbered(grid))

    assert found["count"] == 0
    assert found["tempo_bpm"] is None
    assert found["meter"] is None
    assert found["first_seconds"] is None


def test_a_grid_that_arrives_out_of_order_is_sorted_before_it_is_numbered() -> None:
    grid = BeatGrid(beats=(1.0, 0.0, 0.5), downbeats=(0.5,))

    records = beats.numbered(beats.detect(Path("unused.wav"), lambda path: grid))

    assert [record["t"] for record in records] == [0.0, 0.5, 1.0]
    assert [record["downbeat"] for record in records] == [False, True, False]


def test_a_model_that_falls_over_is_an_analysis_failure_naming_the_file() -> None:
    def falls_over(path: Path) -> BeatGrid:
        raise RuntimeError("CUDA out of memory")

    with pytest.raises(AnalysisFailedError) as raised:
        beats.detect(Path("concert.wav"), falls_over)

    assert "concert.wav" in raised.value.cause
    assert "CUDA out of memory" in raised.value.cause


def test_a_structured_failure_from_a_detector_is_passed_through_unchanged() -> None:
    """A missing install must not be relabelled as the model failing on the audio."""

    def missing(path: Path) -> BeatGrid:
        raise AnalysisDependencyError(cause="beat_this is not installed.")

    with pytest.raises(AnalysisDependencyError):
        beats.detect(Path("concert.wav"), missing)


# --- how far the grid may be trusted (#112) ---------------------------------------------------


def _rubato(steady: int = 12, free: Sequence[float] = (0.9, 1.4, 0.7, 1.3)) -> BeatGrid:
    """A grid that keeps time and then stops keeping it — a ballad head after an in-time tune."""
    times = [round(index * 0.5, 6) for index in range(steady)]
    for interval in free:
        times.append(round(times[-1] + interval, 6))
    return BeatGrid(beats=tuple(times), downbeats=tuple(times[::4]))


def test_a_grid_that_keeps_time_in_four_is_trusted_end_to_end() -> None:
    """The gate has to be inert on the timelines whose grids are already sound."""
    found = beats.trust(beats.numbered(_steady(13)))

    assert found.meter == 4
    assert all(found.trusted)
    assert found.reasons == {}


def test_a_bar_position_the_meter_cannot_hold_is_not_trusted() -> None:
    """A bar 6 in a grid whose bars are fours is the grid refuting itself — #112 AC3."""
    grid = BeatGrid(
        beats=tuple(round(index * 0.5, 6) for index in range(17)),
        downbeats=(0.0, 3.0, 5.0, 7.0),
    )

    found = beats.trust(beats.numbered(grid))

    assert found.meter == 4
    # The opening bar runs to six beats, so its fifth and sixth are positions 4/4 cannot hold.
    assert found.trusted[:6] == (True, True, True, True, False, False)
    assert found.trusted[6:10] == (True, True, True, True)
    assert found.reasons == {"bar_position": 2}


def test_beats_either_side_of_an_out_of_time_stretch_are_not_trusted() -> None:
    """Rubato carries perfectly legal bar positions, so only the timing gives it away."""
    found = beats.trust(beats.numbered(_rubato()))

    assert found.trusted[:10] == (True,) * 10
    assert not all(found.trusted[11:])
    assert found.reasons.get("tempo", 0) > 0


def test_a_steady_grid_that_changes_tempo_once_is_not_gated_wholesale() -> None:
    """A set has tunes at different tempos; only the change itself is untrustworthy, not both."""
    slow = [round(index * 0.5, 6) for index in range(12)]
    fast = [round(slow[-1] + (index + 1) * 0.35, 6) for index in range(12)]
    grid = BeatGrid(beats=tuple(slow + fast), downbeats=tuple((slow + fast)[::4]))

    found = beats.trust(beats.numbered(grid))

    assert all(found.trusted[:9])
    assert all(found.trusted[-9:])


def test_a_grid_too_short_to_judge_is_left_alone_rather_than_gated_blind() -> None:
    """Two beats give one interval and no local reference; refusing them all would invent a gate."""
    found = beats.trust(beats.numbered(BeatGrid(beats=(0.0, 0.5), downbeats=(0.0,))))

    assert all(found.trusted)


def test_a_grid_that_calls_every_beat_a_downbeat_describes_no_bar_position_at_all() -> None:
    """The anchor's failure: `meter: 1` over a jazz set, every beat marked as starting a bar.

    Filtering such a grid against its own meter would keep exactly the beats at position one
    and throw the rest away, leaving a histogram that is 100% beat one by construction. That
    is not a gate finding a skew, it is a gate manufacturing one, so the grid is refused whole.
    """
    times = tuple(round(index * 0.5, 6) for index in range(16))
    found = beats.trust(beats.numbered(BeatGrid(beats=times, downbeats=times)))

    assert found.meter == 1
    assert not any(found.trusted)
    assert found.reasons["bar_position"] == 16


def test_an_empty_grid_has_nothing_to_trust_and_no_meter() -> None:
    found = beats.trust(beats.numbered(BeatGrid((), ())))

    assert found.trusted == ()
    assert found.meter is None
