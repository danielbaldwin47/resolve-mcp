"""Turning two lists of times into bars — the part of beat detection that is not a model.

ADR 0002 puts the model behind a seam precisely so that everything downstream of it is
ordinary code under test. This is that code: bar numbering, downbeat matching, and the
tempo and meter stats the gist reports. A jazz set is not all in four and does not always
start on the one, so neither assumption is allowed to go unchecked.
"""

from __future__ import annotations

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
