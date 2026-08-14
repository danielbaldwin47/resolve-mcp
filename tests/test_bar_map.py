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


def _noise(count: int, seed: int = 1) -> list[float]:
    """Unstructured accent values — a fixture with a short period is not noise at all.

    The contrast is z-scored, so a tiny but perfectly periodic wobble still scores as strong
    evidence. Real audio that says nothing does not repeat; this does not either.
    """
    values: list[float] = []
    state = seed
    for _ in range(count):
        state = (state * 1_103_515_245 + 12_345) % 2**31
        values.append(state / 2**31)
    return values


def _leaning(count: int, every: int, weight: float, seed: int = 1) -> tuple[float, ...]:
    """Noise with a weak pull towards every n-th beat — evidence too thin to act on."""
    return tuple(
        value + (weight if index % every == 0 else 0.0)
        for index, value in enumerate(_noise(count, seed))
    )


@pytest.mark.parametrize("seed", [1, 7, 42, 99, 2026])
def test_a_fold_the_accents_cannot_settle_assumes_least_rather_than_reading_noise(
    seed: int,
) -> None:
    """At 214 bpm both halving and thirding land in the tapping range, so the winner used to
    be decided by a contrast of 0.01 against 0.05 — noise, reported as a reading, and it
    folded the corpus mix to 107 bpm and its own bass stem to 71 (#180). Several readings
    because one seed agreeing is not the property; every reading with nothing in it folding
    the same way is."""
    grid = _onset_scale()
    times = [float(row["t"]) for row in grid]

    found = bars_module.tactus(times, _leaning(len(grid), every=3, weight=0.03, seed=seed))

    assert found.reason == "tempo"
    assert (found.fold, found.phase) == (2, 0)


def test_noise_alone_does_not_read_as_accent_evidence() -> None:
    """The floor has to move with the span. Over a hundred-odd beats a reading with nothing
    in it scores about 0.18 — above FOLD_MARGIN — so a fixed threshold would call noise a
    fold here while asking far more than it needs to of a seventy-four-minute set."""
    grid = _onset_scale()
    times = [float(row["t"]) for row in grid]
    reading = _noise(len(grid), seed=7)
    # Some candidate always leans this far on a span this short, with nothing behind it.
    loudest = max(
        abs(bars_module._contrast(reading, tuple(range(phase, len(grid), fold))))
        for fold in (2, 3)
        for phase in range(fold)
    )
    assert loudest > bars_module.FOLD_MARGIN

    found = bars_module.tactus(times, reading)

    assert found.reason == "tempo"


def test_the_floor_is_the_larger_of_the_margin_and_the_noise_it_would_beat() -> None:
    short = bars_module._accent_floor(64, 64)
    long_span = bars_module._accent_floor(5_000, 5_000)
    assert short > bars_module.FOLD_MARGIN
    assert long_span == bars_module.FOLD_MARGIN


def test_the_same_grid_folds_the_same_way_whichever_witness_read_it() -> None:
    """Two accent readings that both say nothing must not fold a grid two different ways —
    the disagreement the corpus run showed between the mix and its own bass stem."""
    grid = _onset_scale()
    times = [float(row["t"]) for row in grid]
    one = bars_module.tactus(times, _leaning(len(grid), every=2, weight=0.02, seed=7))
    other = bars_module.tactus(times, _leaning(len(grid), every=3, weight=0.03, seed=99))
    assert (one.fold, one.phase) == (other.fold, other.phase)


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


# --- levelling the reading ---------------------------------------------------------------


def test_a_beat_is_read_against_the_music_around_it() -> None:
    found = bars_module.levelled([0.5] * 8 + [1.0] + [0.5] * 8, window=5)
    assert found[8] == pytest.approx(2.0)
    assert found[0] == pytest.approx(1.0)


def test_silence_levels_to_nothing_rather_than_dividing_by_it() -> None:
    assert bars_module.levelled([0.0] * 9, window=5) == (0.0,) * 9


def test_the_same_accent_reads_the_same_in_a_loud_tune_and_a_quiet_one() -> None:
    quiet = [0.1, 0.05, 0.05, 0.05] * 8
    loud = [1.0, 0.5, 0.5, 0.5] * 8
    together = bars_module.levelled(quiet + loud)
    assert together[0] == pytest.approx(together[len(quiet)], abs=0.01)


def test_a_set_wide_reading_no_longer_swamps_the_bar_level_accent() -> None:
    """The first real run over the Zinc set scored 0.03 on grids the arithmetic reads
    perfectly on a fixture: a quiet tune, an applause gap and a shout chorus put all the
    variance between tunes and none of it inside a bar (#180)."""
    grid = _onset_scale(bars=12)
    quiet = [0.1 if index % 8 == 0 else 0.04 for index in range(32)]
    gap = [0.005] * 32
    shout = [2.0 if index % 8 == 0 else 0.8 for index in range(32)]
    reading = quiet + gap + shout
    mapped = bars_module.mapped(grid, reading)
    assert mapped.meter == 4
    # The same reading either way: levelling is what puts the variance inside the bar
    # instead of between the tunes, and the contrast is how much of it lands there.
    raw = bars_module.barring(reading)
    levelled = bars_module.barring(bars_module.levelled(reading))
    assert raw is not None and levelled is not None
    assert levelled.contrast > raw.contrast


# --- does the span agree with itself ------------------------------------------------------


def test_a_barring_that_holds_throughout_agrees_with_itself() -> None:
    salience = [1.0, 0.4, 0.4, 0.4] * 16
    read = bars_module.barring(salience)
    assert read is not None
    assert bars_module.agreement(salience, read) == pytest.approx(1.0)


def test_a_barring_that_only_holds_for_half_the_span_says_so() -> None:
    """The failure this catches: a span-wide score that is the average of two readings,
    describing neither. It scored respectably on the corpus anchor and meant nothing."""
    steady = [1.0, 0.4, 0.4, 0.4] * 8
    shifted = [0.4, 0.4, 1.0, 0.4] * 8
    read = bars_module.barring(steady + shifted)
    assert read is not None
    held = bars_module.agreement(steady + shifted, read)
    assert held is not None and held < 1.0


def test_a_span_too_short_to_check_against_itself_says_nothing_rather_than_zero() -> None:
    salience = [1.0, 0.4, 0.4, 0.4] * 4
    read = bars_module.barring(salience)
    assert read is not None
    assert bars_module.agreement(salience, read) is None


def test_a_span_that_disagrees_with_itself_loses_confidence() -> None:
    grid = _onset_scale(bars=16)
    steady = _accented(grid, every=8)
    # The same reading with the bar line moved two beats halfway through: the tracker that
    # keeps the form for a chorus and then loses it.
    wandering = steady[: len(steady) // 2] + _accented(grid, every=8)[4:][: len(steady) // 2]

    holds = bars_module.mapped(grid, steady)
    wanders = bars_module.mapped(grid, wandering)

    assert wanders.confidence < holds.confidence
    assert holds.reasons["meter_agreement"] == pytest.approx(1.0)
    assert wanders.reasons["meter_agreement"] < 1.0


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


def test_the_floor_gates_the_model_path_too() -> None:
    """Taken at the model's word is about where the barring came from, not about being
    exempt from the question every other map has to answer."""
    times = _times(40, 60.0 / TEMPO)
    # Nine bars of four and one of three: the model held its meter for most of the tune.
    downbeats = times[:36:4] + (times[36],) + (times[39],)
    grid = numbered(BeatGrid(beats=times, downbeats=downbeats))

    lenient = bars_module.mapped(grid, [0.5] * len(grid), minimum_confidence=0.5)
    strict = bars_module.mapped(grid, [0.5] * len(grid), minimum_confidence=0.95)

    assert lenient.source == "model"
    assert 0.5 <= lenient.confidence < 0.95
    assert strict.source == "refused"
    assert strict.meter is None
    assert strict.bars == ()


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


def test_a_model_map_reports_the_grids_own_tempo_and_no_fold_verdict() -> None:
    """The fold never ran on this path, so nothing may read as its verdict — and the tempo
    to report is the grid's own, because the grid's beats *are* the pulse here."""
    grid = _committed()
    mapped = bars_module.mapped(grid, [0.5] * len(grid))
    summary = bars_module.gist(mapped, bars_module.DEFAULT_MINIMUM_CONFIDENCE, None)
    assert summary["fold_reason"] == bars_module.MODEL
    assert summary["tempo_bpm"] == summary["grid_bpm"] == pytest.approx(TEMPO, abs=0.5)


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


def test_a_span_maps_one_tune_out_of_a_set(tmp_path: Path) -> None:
    """One fold, one meter, one phase — so a set of tunes at different tempos with applause
    between them has to be asked about a tune at a time or the answer is wrong before the
    arithmetic starts (#180)."""
    grid = _onset_scale(bars=16)
    source = _master(tmp_path, seconds=float(grid[-1]["t"]) + 1.0)
    half = float(grid[len(grid) // 2]["t"])
    seen: list[int] = []

    def accent(path: Path, times: Sequence[float]) -> tuple[float, ...]:
        seen.append(len(times))
        return _accented(grid, every=8)[: len(times)]

    result = _result(
        bars_module.detect_bars(
            source,
            start_seconds=half,
            detector=_detector(grid),
            accent=accent,
        )
    )
    assert seen == [len(grid) // 2]
    assert result["count"] == 8
    written = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert written["start_seconds"] == half
    # Bars are numbered within the span; the grid beat each one starts on is not.
    assert written["bars"][0]["bar"] == 1
    assert written["bars"][0]["beat"] == len(grid) // 2 + 1


def test_a_span_is_part_of_the_cache_identity(tmp_path: Path) -> None:
    grid = _onset_scale()
    source = _master(tmp_path, seconds=float(grid[-1]["t"]) + 1.0)
    accent = _accent_of(_accented(grid, every=8))
    whole = _result(bars_module.detect_bars(source, detector=_detector(grid), accent=accent))
    part = _result(
        bars_module.detect_bars(
            source,
            start_seconds=1.0,
            detector=_detector(grid),
            accent=accent,
        )
    )
    assert part["path"] != whole["path"]


def test_a_span_that_ends_before_it_starts_is_refused(tmp_path: Path) -> None:
    source = _master(tmp_path, seconds=2.0)
    with pytest.raises(InvalidRequestError):
        bars_module.detect_bars(source, start_seconds=5.0, end_seconds=1.0)


def test_a_span_starting_before_the_audio_is_refused(tmp_path: Path) -> None:
    source = _master(tmp_path, seconds=2.0)
    with pytest.raises(InvalidRequestError):
        bars_module.detect_bars(source, start_seconds=-1.0)


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
    assert "master mix is read" in caught.value.payload()["fix"]


def test_a_separation_directory_is_read_the_way_the_phrase_job_reads_one(tmp_path: Path) -> None:
    """The stems arrive as a directory far more often than as named paths — it is what
    separate_stems reports — so the directory branch is the one an agent actually takes."""
    grid = _onset_scale()
    source = _master(tmp_path, seconds=float(grid[-1]["t"]) + 1.0)
    separation = tmp_path / "stems" / "mix"
    separation.mkdir(parents=True)
    bass = write_hits(separation / "concert_(Bass)_htdemucs_ft.wav", [], seconds=1.0)
    seen: list[Path] = []

    def accent(path: Path, times: Sequence[float]) -> tuple[float, ...]:
        seen.append(path)
        return _accented(grid, every=8)[: len(times)]

    result = _result(
        bars_module.detect_bars(
            source,
            stems=tmp_path / "stems",
            detector=_detector(grid),
            accent=accent,
        )
    )
    assert result["stem"] == "bass"
    assert seen == [bass]


def test_a_stem_the_cache_dropped_is_refused_by_path(tmp_path: Path) -> None:
    source = _master(tmp_path, seconds=2.0)
    with pytest.raises(InvalidRequestError) as caught:
        bars_module.detect_bars(source, stems={"bass": tmp_path / "gone.wav"})
    assert "separate_stems again" in caught.value.payload()["fix"]
    assert caught.value.payload()["detail"]["stem"].endswith("gone.wav")


# --- the tool ----------------------------------------------------------------------------


def test_the_tool_is_registered_and_returns_a_job() -> None:
    assert analysis_tools.detect_bars in analysis_tools.TOOLS


def test_the_tool_wraps_a_refusal_in_the_envelope(tmp_path: Path) -> None:
    answered = analysis_tools.detect_bars(str(tmp_path / "missing.wav"))
    assert answered["ok"] is False
    assert answered["error"]["code"] == "invalid_request"
