"""Drum-fill candidates: the rule layer, the transcription seam, the job, the cache (#39).

The rule layer is where the decisions are, so most of this file is plain functions over
hits and a beat grid — no audio, no job, no threads. The transcriber is checked separately
on fixture audio, and the job on top of both, because that is where the caching lives.

The stem fixture writes into a ``drums`` subdirectory of a separation directory, because
that is where the second pass actually puts them: the job reports the parent, and a fixture
with a flatter layout than the real one would let a directory the agent cannot use pass.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import drums, fills, music
from resolve_mcp.analysis.beats import BeatGrid, Detector, numbered
from resolve_mcp.analysis.drums import Hit
from resolve_mcp.audio.stems import DRUM_PASS, DRUM_STEMS, MIX_PASS
from resolve_mcp.config import get_config
from resolve_mcp.errors import AnalysisFailedError, InvalidRequestError
from resolve_mcp.jobs.runner import JobOutput, wait_for
from resolve_mcp.jobs.store import JobRecord
from resolve_mcp.tools import analysis as analysis_tools

from .fakes import write_clicks, write_hits


@pytest.fixture
def master(tmp_path: Path) -> Path:
    """The mix the stems were separated from — what the beat grid is read off."""
    return write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)


@pytest.fixture
def separation(tmp_path: Path) -> Path:
    """A separation directory as ``separate_stems`` leaves it: two passes, one parent."""
    directory = tmp_path / "stems" / "concert-abc123def456"
    write_hits(directory / MIX_PASS / "concert_(Drums)_model.wav", times=(), seconds=4.0)
    for label in DRUM_STEMS:
        write_hits(
            directory / DRUM_PASS / f"concert_({label.title()})_model.wav",
            times=(),
            seconds=4.0,
        )
    return directory


@pytest.fixture
def stems(separation: Path) -> dict[str, Path]:
    """The drums mapping, the shape the separation job reports alongside the directory."""
    return {
        label: separation / DRUM_PASS / f"concert_({label.title()})_model.wav"
        for label in DRUM_STEMS
    }


# --- a grid and some playing to hang the rules on -----------------------------------


def _grid(bars: int = 8, tempo: float = 120.0, meter: int = 4) -> tuple[dict[str, Any], ...]:
    """``numbered`` rows for a steady grid — the same shape the beats half writes."""
    step = 60.0 / tempo
    beats = tuple(round(index * step, 6) for index in range(bars * meter))
    downbeats = tuple(beats[index] for index in range(0, len(beats), meter))
    return numbered(BeatGrid(beats=beats, downbeats=downbeats))


def _comping(rows: Sequence[Mapping[str, Any]]) -> list[Hit]:
    """Ordinary playing: a kick on every beat and nothing else — the baseline to depart from."""
    return [Hit(seconds=row["t"], stem="kick", strength=0.4) for row in rows]


def _burst(
    rows: Sequence[Mapping[str, Any]],
    beat: int,
    count: int = 6,
    stem: str = "toms",
) -> list[Hit]:
    """``count`` hits inside one beat — a fill's worth of activity in one place."""
    step = _step(rows)
    start = rows[beat - 1]["t"]
    return [
        Hit(seconds=start + index * step / count, stem=stem, strength=0.8)
        for index in range(count)
    ]


def _step(rows: Sequence[Mapping[str, Any]]) -> float:
    return float(rows[1]["t"]) - float(rows[0]["t"])


def _timekeeper(
    rows: Sequence[Mapping[str, Any]],
    stem: str,
    count: int = 2,
) -> list[Hit]:
    """A stem played the same way on every beat — the thing a fill has to stand out from."""
    return [hit for beat in range(1, len(rows) + 1) for hit in _burst(rows, beat, count, stem)]


# --- the rule layer -----------------------------------------------------------------


def test_steady_comping_is_not_a_fill() -> None:
    rows = _grid()

    found = fills.candidates(_comping(rows), rows)

    assert found.candidates == ()
    assert found.considered == 0


def test_a_tom_burst_before_a_downbeat_is_a_candidate_aligned_to_the_grid() -> None:
    rows = _grid()
    # Bar 4 is beats 13-16; filling its last two beats resolves onto bar 5's downbeat.
    hits = _comping(rows) + _burst(rows, 15) + _burst(rows, 16)

    found = fills.candidates(hits, rows)

    assert len(found.candidates) == 1
    one = found.candidates[0]
    assert one.start == pytest.approx(rows[14]["t"])
    assert one.end == pytest.approx(rows[16]["t"])
    assert (one.beat, one.bar, one.in_bar, one.beats) == (15, 4, 3, 2)
    assert one.resolves_into_bar == 5
    assert one.counts["toms"] == 12


def test_confidence_is_highest_when_toms_land_before_a_phrase_boundary() -> None:
    rows = _grid()
    # Bar 5 opens the second four-bar phrase; bar 4 opens nothing in particular.
    phrase = fills.candidates(_comping(rows) + _burst(rows, 15) + _burst(rows, 16), rows)
    middle = fills.candidates(_comping(rows) + _burst(rows, 11) + _burst(rows, 12), rows)

    assert phrase.candidates[0].confidence > middle.candidates[0].confidence
    assert phrase.candidates[0].factors["phrase"] == 1.0
    assert middle.candidates[0].factors["phrase"] < 1.0


def test_a_snare_only_burst_scores_below_the_same_burst_on_toms() -> None:
    rows = _grid()
    toms = fills.candidates(_comping(rows) + _burst(rows, 15, stem="toms"), rows)
    snare = fills.candidates(_comping(rows) + _burst(rows, 15, stem="snare"), rows)

    assert toms.candidates[0].factors["toms"] > snare.candidates[0].factors["toms"]
    assert toms.candidates[0].confidence > snare.candidates[0].confidence


def test_a_run_that_outlasts_a_fill_is_dropped_and_counted() -> None:
    rows = _grid()
    busy = [hit for beat in range(9, 9 + fills.MAXIMUM_BEATS + 1) for hit in _burst(rows, beat)]

    found = fills.candidates(_comping(rows) + busy, rows)

    assert found.candidates == ()
    assert found.dropped == 1


def test_a_fill_is_found_under_constant_tom_comping() -> None:
    """#125: toms on nearly every beat used to nominate the whole tune, which was then dropped.

    The regression test for the ticket. A drummer comping on the low toms all night is playing
    a timekeeper, not filling; the burst on beat 40 is the fill, and it is the only thing here
    that departs from what the kit is otherwise doing.
    """
    rows = _grid(bars=16)
    hits = _comping(rows) + _timekeeper(rows, "toms", count=2) + _burst(rows, 40, count=6)

    found = fills.candidates(hits, rows)

    assert [one.beat for one in found.candidates] == [40]
    assert found.dropped == 0


def test_constant_tom_comping_alone_makes_no_run_to_discard() -> None:
    """The other half of #125: with nothing departing, nothing is nominated in the first place."""
    rows = _grid(bars=16)

    found = fills.candidates(_comping(rows) + _timekeeper(rows, "toms", count=2), rows)

    assert found.candidates == ()
    assert found.considered == 0
    assert found.dropped == 0


def test_a_clip_too_short_for_the_exclusion_zone_still_has_a_baseline() -> None:
    """Twelve beats leaves nothing outside the excluded neighbourhood of a middle beat.

    A baseline of zero there would read steady comping as a departure on every beat, so the
    exclusion is what gets given up on a clip this short, not the baseline.
    """
    rows = _grid(bars=3)

    found = fills.candidates(_comping(rows) + _timekeeper(rows, "toms", count=3), rows)

    assert found.candidates == ()
    assert found.considered == 0
    assert found.dropped == 0


def test_a_sustained_dense_passage_is_still_discarded() -> None:
    """A run past ``MAXIMUM_BEATS`` is a solo or a groove, and dropping it whole stays right."""
    rows = _grid(bars=16)
    sustained = [
        hit
        for beat in range(20, 20 + fills.MAXIMUM_BEATS + 4)
        for hit in _burst(rows, beat, count=6)
    ]

    found = fills.candidates(_comping(rows) + _timekeeper(rows, "toms", count=2) + sustained, rows)

    assert found.candidates == ()
    assert found.dropped == 1


def test_a_fill_under_a_loud_timekeeper_survives_the_density_gate() -> None:
    """A cymbal riding six to the beat inflates both halves of the ratio until it says nothing.

    The local gate is what finds this one: the ride is baseline here and the toms are not.
    """
    rows = _grid(bars=16)
    hits = _comping(rows) + _timekeeper(rows, "ride", count=6) + _burst(rows, 40, count=3)

    found = fills.candidates(hits, rows)

    assert [one.beat for one in found.candidates] == [40]
    assert found.candidates[0].density_ratio < fills.BUSY_MULTIPLE


def test_ride_and_crash_are_counted_as_one_cymbal_signal() -> None:
    """A ride turning crashy at a phrase end is one gesture, so it is tallied as one stem."""
    rows = _grid()
    hits = (
        _comping(rows)
        + _burst(rows, 15, count=3, stem="ride")
        + _burst(rows, 16, count=3, stem="crash")
    )

    found = fills.candidates(hits, rows)
    record = fills.rows(found)[0]

    assert found.candidates[0].counts["cymbals"] == 6
    assert record["cymbals"] == 6
    assert "ride" not in record
    assert "crash" not in record


def test_the_tom_share_is_not_diluted_by_the_cymbals() -> None:
    """Carrying the cymbals must not quietly lower the confidence of every tom fill."""
    rows = _grid()
    played = _comping(rows) + _timekeeper(rows, "snare", count=3)
    fill = _burst(rows, 15, count=3) + _burst(rows, 16, count=3)

    without = fills.candidates(played + fill, rows)
    carried = fills.candidates(played + fill + _timekeeper(rows, "ride", count=2), rows)

    assert without.candidates[0].factors["toms"] < 1.0
    assert carried.candidates[0].factors["toms"] == without.candidates[0].factors["toms"]


def test_one_quiet_beat_inside_a_fill_does_not_split_it() -> None:
    rows = _grid()
    hits = _comping(rows) + _burst(rows, 13) + _burst(rows, 15) + _burst(rows, 16)

    found = fills.candidates(hits, rows)

    assert len(found.candidates) == 1
    assert found.candidates[0].beats == 4
    assert found.candidates[0].beat == 13


def test_candidates_below_the_floor_are_left_out_but_still_counted() -> None:
    rows = _grid()
    hits = _comping(rows) + _burst(rows, 11) + _burst(rows, 12)

    kept = fills.candidates(hits, rows, minimum_confidence=0.0)
    dropped = fills.candidates(hits, rows, minimum_confidence=1.0)

    assert len(kept.candidates) == 1
    assert dropped.candidates == ()
    assert dropped.considered == 1


def test_a_grid_with_no_beats_yields_no_candidates_rather_than_failing() -> None:
    found = fills.candidates([Hit(seconds=1.0, stem="toms", strength=0.9)], ())

    assert found.candidates == ()
    assert found.baseline == 0.0


def test_a_kit_with_nothing_to_be_busier_than_yields_no_candidates() -> None:
    rows = _grid()

    found = fills.candidates(_burst(rows, 15), rows)

    assert found.candidates == ()
    assert found.baseline == 0.0


def test_rows_carry_the_times_confidence_and_counts_an_agent_reads() -> None:
    rows = _grid()
    found = fills.candidates(_comping(rows) + _burst(rows, 15) + _burst(rows, 16), rows)

    record = fills.rows(found)[0]

    assert record["start"] == pytest.approx(rows[14]["t"], abs=1e-3)
    assert record["duration"] == pytest.approx(record["end"] - record["start"], abs=1e-3)
    assert 0.0 <= record["confidence"] <= 1.0
    assert record["toms"] == 12
    assert set(record["factors"]) == {"density", "toms", "resolution", "phrase"}


# --- the transcription seam ---------------------------------------------------------


def test_the_onset_transcriber_finds_each_stem_hit_and_labels_it(tmp_path: Path) -> None:
    kit = {
        "kick": write_hits(tmp_path / "kick.wav", times=(0.5, 1.5), seconds=2.0),
        "snare": write_hits(tmp_path / "snare.wav", times=(1.0,), seconds=2.0),
    }

    hits = drums.transcribe(kit)

    assert [hit.stem for hit in hits] == ["kick", "snare", "kick"]
    assert [round(hit.seconds, 1) for hit in hits] == [0.5, 1.0, 1.5]
    assert all(0.0 < hit.strength <= 1.0 for hit in hits)


def test_a_transcriber_that_falls_over_is_an_analysis_failure(tmp_path: Path) -> None:
    def broken(kit: Mapping[str, Path]) -> tuple[Hit, ...]:
        raise RuntimeError("no model")

    with pytest.raises(AnalysisFailedError) as raised:
        drums.transcribe({"toms": tmp_path / "toms.wav"}, broken)

    assert "no model" in raised.value.cause


# --- the job: what lands on disk, what comes back inline ----------------------------


def _played(rows: Sequence[Mapping[str, Any]]) -> drums.Transcriber:
    """A transcriber standing in for the kit: comping, and a fill into bar 5."""
    hits = tuple(_comping(rows) + _burst(rows, 15) + _burst(rows, 16))

    def transcriber(kit: Mapping[str, Path]) -> tuple[Hit, ...]:
        return hits

    return transcriber


def _detector_for(rows: Sequence[Mapping[str, Any]]) -> Detector:
    beats = tuple(row["t"] for row in rows)
    downbeats = tuple(row["t"] for row in rows if row["downbeat"])

    def detector(path: Path) -> BeatGrid:
        return BeatGrid(beats=beats, downbeats=downbeats)

    return detector


def _finished(record: JobRecord) -> dict[str, Any]:
    assert record.state == "completed", record.error
    assert record.result is not None
    return record.result


def _ran(
    stems: Mapping[str, Path] | str | Path,
    audio: Path,
    rows: Sequence[Mapping[str, Any]],
    **asked: Any,
) -> dict[str, Any]:
    """Run the job to completion with the grid and the playing injected."""
    started = fills.detect_drum_fills(
        stems,
        audio,
        transcriber=_played(rows),
        detector=_detector_for(rows),
        **asked,
    )
    return _finished(wait_for(started["job_id"]))


def test_the_job_writes_one_record_per_candidate_and_returns_the_gist(
    master: Path,
    stems: dict[str, Path],
) -> None:
    result = _ran(stems, master, _grid())

    written = Path(result["path"])
    assert written.parent == get_config().analysis_dir
    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["kind"] == fills.FILLS
    assert len(document[fills.FILLS]) == result["count"] == 1
    assert document[fills.FILLS][0]["confidence"] == pytest.approx(
        result["strongest"]["confidence"]
    )
    assert result["stems"] == sorted(DRUM_STEMS)


def test_the_document_holds_one_candidate_per_line(
    master: Path,
    stems: dict[str, Path],
) -> None:
    result = _ran(stems, master, _grid())

    lines = Path(result["path"]).read_text(encoding="utf-8").splitlines()
    records = [line for line in lines if line.strip().startswith('{"start"')]
    assert len(records) == 1


def test_the_directory_a_separation_reports_is_accepted(
    master: Path,
    separation: Path,
) -> None:
    """The job hands back the parent of both passes, so the parent is what this takes."""
    result = _ran(separation, master, _grid())

    assert result["stems"] == sorted(DRUM_STEMS)
    assert result["count"] == 1


def test_the_drum_pass_on_its_own_is_accepted_too(master: Path, separation: Path) -> None:
    result = _ran(separation / DRUM_PASS, master, _grid())

    assert result["stems"] == sorted(DRUM_STEMS)


def test_the_four_stem_pass_is_not_mistaken_for_the_drum_stems(
    master: Path,
    separation: Path,
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills(separation / MIX_PASS, master)

    assert raised.value.detail["wanted"] == list(DRUM_STEMS)


def test_a_drum_pass_holding_nothing_labelled_falls_back_to_the_directory(
    master: Path,
    tmp_path: Path,
) -> None:
    """The drum pass is looked in first, but an empty one must not end the search."""
    directory = tmp_path / "stems" / "hand-copied"
    write_hits(directory / DRUM_PASS / "unlabelled.wav", times=(), seconds=1.0)
    for label in DRUM_STEMS:
        write_hits(directory / f"concert_({label.title()})_model.wav", times=(), seconds=4.0)

    result = _ran(directory, master, _grid())

    assert result["stems"] == sorted(DRUM_STEMS)


def test_a_kit_missing_a_stem_is_read_rather_than_refused(
    master: Path,
    stems: dict[str, Path],
) -> None:
    """Some stems beat none — the gist names what was read, so a timid score is explainable."""
    result = _ran({"kick": stems["kick"], "snare": stems["snare"]}, master, _grid())

    assert result["stems"] == ["kick", "snare"]


# --- the cache ----------------------------------------------------------------------


def _must_not_run() -> drums.Transcriber:
    def transcriber(kit: Mapping[str, Path]) -> tuple[Hit, ...]:
        raise AssertionError("the cached result should have answered this")

    return transcriber


def test_asking_twice_answers_from_cache_without_transcribing_again(
    master: Path,
    stems: dict[str, Path],
) -> None:
    first = _ran(stems, master, _grid())

    again = fills.detect_drum_fills(stems, master, transcriber=_must_not_run())

    assert again["cached"] is True
    assert again["result"] == first


def test_refresh_redoes_the_work_the_cache_would_have_answered(
    master: Path,
    stems: dict[str, Path],
) -> None:
    rows = _grid()
    _ran(stems, master, rows)

    started = fills.detect_drum_fills(
        stems,
        master,
        transcriber=_played(rows),
        detector=_detector_for(rows),
        refresh=True,
    )
    record = wait_for(started["job_id"])

    assert record.cached is False
    assert _finished(record)["count"] == 1


def test_a_different_floor_is_a_different_key(master: Path, stems: dict[str, Path]) -> None:
    rows = _grid()
    _ran(stems, master, rows)

    started = fills.detect_drum_fills(
        stems,
        master,
        minimum_confidence=0.9,
        transcriber=_played(rows),
        detector=_detector_for(rows),
    )

    assert started.get("cached") is not True


def test_the_beat_grid_is_the_one_analyze_music_already_paid_for(
    master: Path,
    stems: dict[str, Path],
) -> None:
    """The two jobs share the beats cache entry, so the model runs once per master."""
    rows = _grid()
    wait_for(music.analyze_music(master, energy=False, detector=_detector_for(rows))["job_id"])

    def detector_that_must_not_run(path: Path) -> BeatGrid:
        raise AssertionError("beats were already detected for this audio")

    started = fills.detect_drum_fills(
        stems,
        master,
        transcriber=_played(rows),
        detector=detector_that_must_not_run,
    )

    assert _finished(wait_for(started["job_id"]))["count"] == 1


# --- what the starter refuses -------------------------------------------------------


def test_a_missing_master_is_refused_before_a_job_exists(
    tmp_path: Path,
    stems: dict[str, Path],
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills(stems, tmp_path / "nothing.wav")

    assert "nothing.wav" in raised.value.cause


def test_a_stems_directory_holding_no_drum_stems_is_refused(
    tmp_path: Path,
    master: Path,
) -> None:
    empty = tmp_path / "stems" / "nothing"
    write_hits(empty / "concert_(Vocals)_model.wav", times=(), seconds=1.0)

    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills(empty, master)

    assert raised.value.detail["wanted"] == list(DRUM_STEMS)


def test_a_directory_that_is_not_there_is_refused(tmp_path: Path, master: Path) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills(tmp_path / "gone", master)

    assert "gone" in raised.value.cause


def test_a_named_stem_that_is_not_on_disk_is_refused(tmp_path: Path, master: Path) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills({"toms": tmp_path / "gone.wav"}, master)

    assert "toms" in raised.value.cause


@pytest.mark.parametrize("floor", [-0.1, 1.1])
def test_a_confidence_floor_outside_zero_to_one_is_refused(
    master: Path,
    stems: dict[str, Path],
    floor: float,
) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills(stems, master, minimum_confidence=floor)

    assert raised.value.detail["minimum_confidence"] == floor


# --- progress and the tool seam -----------------------------------------------------


def test_progress_only_climbs_and_the_document_is_the_job_artifact(
    master: Path,
    stems: dict[str, Path],
) -> None:
    rows = _grid()
    steps: list[tuple[float, str]] = []

    output: JobOutput = fills.detect(
        master,
        stems,
        {"minimum_confidence": fills.DEFAULT_MINIMUM_CONFIDENCE},
        lambda fraction, step: steps.append((fraction, step)),
        transcriber=_played(rows),
        detector=_detector_for(rows),
    )

    fractions = [fraction for fraction, _ in steps]
    assert fractions == sorted(fractions)
    assert [step for _, step in steps if "fill" in step]
    assert output.artifacts == (Path(output.result["path"]),)


def test_the_tool_returns_a_job_to_poll_inside_an_ok_envelope(
    master: Path,
    separation: Path,
) -> None:
    envelope = analysis_tools.detect_drum_fills(str(separation), str(master))

    assert envelope["ok"] is True
    assert "job_id" in envelope["job"]


def test_the_tool_reports_a_bad_request_as_a_structured_error(tmp_path: Path) -> None:
    envelope = analysis_tools.detect_drum_fills(str(tmp_path), str(tmp_path / "nothing.wav"))

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"
