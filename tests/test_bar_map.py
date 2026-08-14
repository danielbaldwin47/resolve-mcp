"""The bar map: folding an onset-scale grid to the tactus and finding where the bar starts.

The decisions under test are all arithmetic over a grid and a per-beat accent reading, so
they are checked here against grids written by hand rather than against a beat model. The
one thing that reads real audio — ``accents`` — is checked against fixture audio whose
loud beats are known by construction.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import bars as bars_module
from resolve_mcp.analysis import music
from resolve_mcp.analysis.beats import BeatGrid, Detector, numbered
from resolve_mcp.errors import InvalidRequestError
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.tools import analysis as analysis_tools

from .fakes import write_hits

TEMPO = 107.0
"""A walking tempo — the corpus anchor's, where the tracker reports its double (#180)."""


def _rows(beats: Sequence[float], downbeats: Sequence[float]) -> tuple[dict[str, Any], ...]:
    return numbered(BeatGrid(beats=tuple(beats), downbeats=tuple(downbeats)))


def _times(count: int, step: float, first: float = 0.0) -> tuple[float, ...]:
    return tuple(round(first + index * step, 6) for index in range(count))


def _onset_scale(bars: int = 8, meter: int = 4, tempo: float = TEMPO) -> tuple[dict[str, Any], ...]:
    """The G2 grid: swung eighths tracked as beats, and every one of them called a downbeat.

    This is what the anchor timeline actually produced — 214 "bpm", ``meter: 1`` — and it is
    the grid a bar map has to make something of.
    """
    times = _times(bars * meter * 2, 60.0 / tempo / 2)
    return numbered(BeatGrid(beats=times, downbeats=times))


def _committed(bars: int = 8, meter: int = 4, tempo: float = TEMPO) -> tuple[dict[str, Any], ...]:
    """A grid the model did commit to: the tactus, with a downbeat every ``meter`` beats."""
    times = _times(bars * meter, 60.0 / tempo)
    return numbered(BeatGrid(beats=times, downbeats=times[::meter]))


def _accented(
    grid: Sequence[Mapping[str, Any]],
    every: int,
    fold: int = 2,
    loud: float = 1.0,
    tactus: float = 0.5,
    quiet: float = 0.1,
) -> tuple[float, ...]:
    """An accent reading: loud on the bar line, medium on the tactus, quiet in between."""
    return tuple(
        loud if index % every == 0 else tactus if index % fold == 0 else quiet
        for index in range(len(grid))
    )


# --- folding to the tactus ------------------------------------------------------------


def test_a_grid_already_at_the_tactus_is_not_folded() -> None:
    grid = _committed()
    found = bars_module.tactus([float(row["t"]) for row in grid], [1.0] * len(grid))
    assert found.fold == 1
    assert found.phase == 0


def test_an_onset_scale_grid_folds_to_the_beat_the_accents_are_on() -> None:
    grid = _onset_scale()
    salience = _accented(grid, every=8)
    found = bars_module.tactus([float(row["t"]) for row in grid], salience)
    assert (found.fold, found.phase) == (2, 0)
    assert found.beats[:3] == (0, 2, 4)


def test_a_backbeat_never_folds_a_grid_that_is_already_a_plausible_tactus() -> None:
    """Two and four are the loudest beats in most of this music, and folding onto them
    would report a half-note pulse for every rock tune in the corpus."""
    grid = _committed(tempo=120.0)
    backbeat = tuple(1.0 if index % 4 in (1, 3) else 0.2 for index in range(len(grid)))
    found = bars_module.tactus([float(row["t"]) for row in grid], backbeat)
    assert found.fold == 1


def test_a_grid_too_fast_to_be_a_tactus_folds_on_tempo_when_the_accents_are_flat() -> None:
    grid = _onset_scale()
    found = bars_module.tactus([float(row["t"]) for row in grid], [0.4] * len(grid))
    assert found.fold == 2
    assert found.reason == "tempo"


def test_folding_says_which_evidence_it_used() -> None:
    grid = _onset_scale()
    found = bars_module.tactus([float(row["t"]) for row in grid], _accented(grid, every=8))
    assert found.reason == "accent"


# --- finding the bar line -------------------------------------------------------------


def test_the_bar_line_is_where_the_accents_are() -> None:
    found = bars_module.barring([1.0, 0.5, 0.5, 0.5] * 8)
    assert found is not None
    assert (found.meter, found.phase) == (4, 0)
    assert found.margin > 0


def test_a_bar_line_off_the_first_beat_is_found_by_its_phase() -> None:
    found = bars_module.barring([0.5, 0.5, 1.0, 0.5] * 8)
    assert found is not None
    assert (found.meter, found.phase) == (4, 2)


def test_three_is_found_when_the_accents_are_in_three() -> None:
    found = bars_module.barring([1.0, 0.4, 0.4] * 10)
    assert found is not None
    assert found.meter == 3


def test_a_flat_reading_leaves_no_margin_between_the_candidates() -> None:
    found = bars_module.barring([0.5] * 32)
    assert found is not None
    assert found.contrast == pytest.approx(0.0)
    assert found.margin == pytest.approx(0.0)


def test_too_few_beats_to_bar_at_all_is_no_barring() -> None:
    assert bars_module.barring([1.0, 0.2]) is None


# --- the map ---------------------------------------------------------------------------


def test_a_grid_the_model_committed_to_is_taken_at_its_word() -> None:
    grid = _committed()
    mapped = bars_module.mapped(grid, [0.5] * len(grid))
    assert mapped.source == "model"
    assert mapped.meter == 4
    assert mapped.confidence == pytest.approx(1.0)
    assert [one.t for one in mapped.bars][:2] == [0.0, pytest.approx(60.0 / TEMPO * 4, abs=0.01)]


def test_the_onset_scale_grid_gets_a_bar_map_the_model_would_not_give() -> None:
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, _accented(grid, every=8))
    assert mapped.source == "inferred"
    assert mapped.meter == 4
    assert mapped.tactus.fold == 2
    assert len(mapped.bars) == 8
    assert [one.t for one in mapped.bars] == [
        pytest.approx(index * 4 * 60.0 / TEMPO, abs=0.01) for index in range(8)
    ]


def test_every_bar_carries_its_place_in_the_four_bar_group() -> None:
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, _accented(grid, every=8))
    assert [one.in_group for one in mapped.bars] == [1, 2, 3, 4, 1, 2, 3, 4]


def test_a_bar_knows_which_grid_beat_it_starts_on() -> None:
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, _accented(grid, every=8))
    # The grid is eighths, so bar two starts on its ninth beat — one-based, as numbered.
    assert [one.beat for one in mapped.bars][:3] == [1, 9, 17]


def test_a_reading_with_nothing_in_it_is_refused_rather_than_guessed() -> None:
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, [0.5] * len(grid))
    assert mapped.source == "refused"
    assert mapped.meter is None
    assert mapped.bars == ()
    assert mapped.confidence < bars_module.DEFAULT_MINIMUM_CONFIDENCE


def test_the_refusal_says_what_the_grid_said_instead() -> None:
    """The failure this ticket exists for is a grid quietly reporting ``meter: 1``; a bar
    map that refuses has to carry that reading, not hide it."""
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, [0.5] * len(grid))
    assert mapped.reasons["grid_meter"] == 1
    assert mapped.reasons["grid_bpm"] == pytest.approx(TEMPO * 2, abs=1.0)


def test_a_floor_of_zero_writes_the_map_it_would_otherwise_refuse() -> None:
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, [0.5] * len(grid), minimum_confidence=0.0)
    assert mapped.source == "inferred"
    assert mapped.bars


def test_a_model_grid_that_only_half_commits_falls_through_to_inference() -> None:
    times = _times(64, 60.0 / TEMPO)
    # Downbeats every four for the first half, then every beat — the shape a tracker gives
    # when it loses the form partway through a set.
    downbeats = times[:32:4] + times[32:]
    grid = numbered(BeatGrid(beats=times, downbeats=downbeats))
    mapped = bars_module.mapped(grid, _accented(grid, every=4, fold=1))
    assert mapped.source == "inferred"
    assert mapped.meter == 4


def test_an_empty_grid_is_refused_without_arithmetic() -> None:
    mapped = bars_module.mapped((), ())
    assert mapped.source == "refused"
    assert mapped.meter is None
    assert mapped.bars == ()


def test_a_reading_that_does_not_match_the_grid_is_a_programming_error() -> None:
    grid = _committed()
    with pytest.raises(ValueError):
        bars_module.mapped(grid, [0.5] * (len(grid) - 1))


# --- what goes to disk ------------------------------------------------------------------


def test_a_row_carries_the_downbeat_time_and_the_bar_length() -> None:
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, _accented(grid, every=8))
    row = bars_module.rows(mapped)[0]
    assert row == {
        "bar": 1,
        "t": 0.0,
        "seconds": pytest.approx(4 * 60.0 / TEMPO, abs=0.01),
        "beats": 4,
        "beat": 1,
        "in_group": 1,
    }


def test_the_last_bar_does_not_invent_a_length_the_grid_cannot_give() -> None:
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, _accented(grid, every=8))
    assert bars_module.rows(mapped)[-1]["seconds"] is None


def test_the_gist_is_scalars_only_so_it_can_ride_home_in_a_job_record() -> None:
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, _accented(grid, every=8))
    summary = bars_module.gist(mapped, bars_module.DEFAULT_MINIMUM_CONFIDENCE, "bass")
    assert not any(isinstance(value, list | dict) for value in summary.values())
    assert summary["meter"] == 4
    assert summary["source"] == "inferred"
    assert summary["count"] == 8
    assert summary["stem"] == "bass"


def test_a_refused_map_still_reports_a_gist() -> None:
    grid = _onset_scale()
    mapped = bars_module.mapped(grid, [0.5] * len(grid))
    summary = bars_module.gist(mapped, bars_module.DEFAULT_MINIMUM_CONFIDENCE, None)
    assert summary["count"] == 0
    assert summary["meter"] is None
    assert summary["source"] == "refused"


# --- reading the accents off audio -------------------------------------------------------


def test_accents_are_loudest_where_the_loud_hits_are(tmp_path: Path) -> None:
    step = 60.0 / TEMPO
    times = _times(16, step)
    loud = [index % 4 == 0 for index in range(16)]
    audio = write_hits(
        tmp_path / "bass.wav",
        times,
        seconds=16 * step + 1.0,
        accents=[1.0 if one else 0.25 for one in loud],
    )
    found = bars_module.accents(audio, times)
    assert len(found) == 16
    assert min(value for value, one in zip(found, loud, strict=True) if one) > max(
        value for value, one in zip(found, loud, strict=True) if not one
    )


def test_a_beat_past_the_end_of_the_audio_reads_as_nothing(tmp_path: Path) -> None:
    audio = write_hits(tmp_path / "bass.wav", [0.1], seconds=1.0)
    found = bars_module.accents(audio, [0.1, 5.0])
    assert found[1] == pytest.approx(0.0)


def test_silence_reads_flat_rather_than_dividing_by_nothing(tmp_path: Path) -> None:
    audio = write_hits(tmp_path / "bass.wav", [], seconds=1.0)
    found = bars_module.accents(audio, [0.1, 0.5])
    assert found == (0.0, 0.0)


# --- the job -----------------------------------------------------------------------------


def _detector(grid: tuple[dict[str, Any], ...]) -> Detector:
    def detect(path: Path) -> BeatGrid:
        return BeatGrid(
            beats=tuple(float(row["t"]) for row in grid),
            downbeats=tuple(float(row["t"]) for row in grid if row["downbeat"]),
        )

    return detect


def _detector_that_must_not_run() -> Detector:
    def detect(path: Path) -> BeatGrid:  # pragma: no cover - the point is that it does not
        raise AssertionError("the beat grid should have come from cache")

    return detect


def _accent_of(salience: Sequence[float]) -> bars_module.Accent:
    def read(path: Path, times: Sequence[float]) -> tuple[float, ...]:
        return tuple(salience[: len(times)])

    return read


def _master(tmp_path: Path, seconds: float) -> Path:
    return write_hits(tmp_path / "media" / "concert.wav", [], seconds=seconds)


def _result(started: Mapping[str, Any]) -> dict[str, Any]:
    record = wait_for(str(started["job_id"]))
    assert record.state == "completed", record.error
    assert record.result is not None
    return record.result


def test_the_job_writes_a_bar_map_and_reports_its_gist(tmp_path: Path) -> None:
    grid = _onset_scale()
    source = _master(tmp_path, seconds=float(grid[-1]["t"]) + 1.0)
    result = _result(
        bars_module.detect_bars(
            source,
            detector=_detector(grid),
            accent=_accent_of(_accented(grid, every=8)),
        )
    )
    assert result["meter"] == 4
    assert result["source"] == "inferred"
    written = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert written["kind"] == "bars"
    assert len(written["bars"]) == 8
    assert written["bars"][0]["t"] == 0.0


def test_the_map_is_cached_on_the_audio_and_the_settings(tmp_path: Path) -> None:
    grid = _onset_scale()
    source = _master(tmp_path, seconds=float(grid[-1]["t"]) + 1.0)
    accent = _accent_of(_accented(grid, every=8))
    first = _result(bars_module.detect_bars(source, detector=_detector(grid), accent=accent))
    again = _result(
        bars_module.detect_bars(source, detector=_detector_that_must_not_run(), accent=accent)
    )
    assert again["path"] == first["path"]


def test_a_different_floor_is_a_different_entry(tmp_path: Path) -> None:
    grid = _onset_scale()
    source = _master(tmp_path, seconds=float(grid[-1]["t"]) + 1.0)
    accent = _accent_of([0.5] * len(grid))
    refused = _result(bars_module.detect_bars(source, detector=_detector(grid), accent=accent))
    written = _result(
        bars_module.detect_bars(
            source,
            detector=_detector(grid),
            accent=accent,
            minimum_confidence=0.0,
        )
    )
    assert refused["source"] == "refused"
    assert written["source"] == "inferred"
    assert written["path"] != refused["path"]


def test_the_beat_grid_is_the_one_analyze_music_already_wrote(tmp_path: Path) -> None:
    """Riding the shared beats half is the whole reason this is a second pass rather than a
    second detector: an hour of concert must not go through the beat model twice."""
    grid = _onset_scale()
    source = _master(tmp_path, seconds=float(grid[-1]["t"]) + 1.0)
    started = music.analyze_music(source, energy=False, detector=_detector(grid))
    assert _result(started)["beats"]["count"] == len(grid)

    result = _result(
        bars_module.detect_bars(
            source,
            detector=_detector_that_must_not_run(),
            accent=_accent_of(_accented(grid, every=8)),
        )
    )
    assert result["meter"] == 4


def test_a_floor_outside_zero_to_one_is_refused_before_a_job_exists(tmp_path: Path) -> None:
    source = _master(tmp_path, seconds=2.0)
    with pytest.raises(InvalidRequestError):
        bars_module.detect_bars(source, minimum_confidence=1.5)


def test_audio_that_is_not_there_is_refused_with_the_fix(tmp_path: Path) -> None:
    with pytest.raises(InvalidRequestError) as caught:
        bars_module.detect_bars(tmp_path / "missing.wav")
    assert "beat grid" in caught.value.payload()["fix"]


def test_a_named_stem_is_read_instead_of_the_master(tmp_path: Path) -> None:
    grid = _onset_scale()
    source = _master(tmp_path, seconds=float(grid[-1]["t"]) + 1.0)
    stems = tmp_path / "stems"
    stems.mkdir()
    bass = write_hits(stems / "bass.wav", [], seconds=float(grid[-1]["t"]) + 1.0)
    seen: list[Path] = []

    def accent(path: Path, times: Sequence[float]) -> tuple[float, ...]:
        seen.append(path)
        return _accented(grid, every=8)[: len(times)]

    result = _result(
        bars_module.detect_bars(
            source,
            stems={"bass": bass},
            detector=_detector(grid),
            accent=accent,
        )
    )
    assert result["stem"] == "bass"
    assert seen == [bass]


def test_a_stem_that_is_not_in_the_separation_is_refused(tmp_path: Path) -> None:
    source = _master(tmp_path, seconds=2.0)
    with pytest.raises(InvalidRequestError) as caught:
        bars_module.detect_bars(source, stems={"drums": tmp_path / "drums.wav"})
    assert caught.value.payload()["detail"]["wanted"] == "bass"


# --- the tool ----------------------------------------------------------------------------


def test_the_tool_is_registered_and_returns_a_job() -> None:
    assert analysis_tools.detect_bars in analysis_tools.TOOLS


def test_the_tool_wraps_a_refusal_in_the_envelope(tmp_path: Path) -> None:
    answered = analysis_tools.detect_bars(str(tmp_path / "missing.wav"))
    assert answered["ok"] is False
    assert answered["error"]["code"] == "invalid_request"
