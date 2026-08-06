"""Drum-fill candidates: the rule layer, the transcription seam, the job, the cache (#39).

The rule layer is where the decisions are, so most of this file is plain functions over
hits and a beat grid — no audio, no job, no threads. The transcriber is checked separately
on fixture audio, and the job on top of both, because that is where the caching lives.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import drums, fills, music
from resolve_mcp.analysis.beats import BeatGrid, numbered
from resolve_mcp.analysis.drums import Hit
from resolve_mcp.audio.stems import DRUM_STEMS
from resolve_mcp.config import get_config
from resolve_mcp.errors import AnalysisFailedError, InvalidRequestError
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.jobs.store import JobRecord
from resolve_mcp.tools import analysis as analysis_tools

from .fakes import write_clicks, write_hits

# --- a grid and some playing to hang the rules on -----------------------------------


def grid(bars: int = 8, tempo: float = 120.0, meter: int = 4) -> tuple[dict[str, Any], ...]:
    """``numbered`` rows for a steady grid — the same shape the beats half writes."""
    step = 60.0 / tempo
    beats = tuple(round(index * step, 6) for index in range(bars * meter))
    downbeats = tuple(beats[index] for index in range(0, len(beats), meter))
    return numbered(BeatGrid(beats=beats, downbeats=downbeats))


def comping(rows: Sequence[Mapping[str, Any]]) -> list[Hit]:
    """Ordinary playing: a kick on every beat and nothing else — the baseline to depart from."""
    return [Hit(seconds=row["t"], stem="kick", strength=0.4) for row in rows]


def burst(
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


# --- the rule layer -----------------------------------------------------------------


def test_steady_comping_is_not_a_fill() -> None:
    rows = grid()

    found = fills.candidates(comping(rows), rows)

    assert found.candidates == ()
    assert found.considered == 0


def test_a_tom_burst_before_a_downbeat_is_a_candidate_aligned_to_the_grid() -> None:
    rows = grid()
    # Bar 4 is beats 13-16; filling its last two beats resolves onto bar 5's downbeat.
    hits = comping(rows) + burst(rows, 15) + burst(rows, 16)

    found = fills.candidates(hits, rows)

    assert len(found.candidates) == 1
    one = found.candidates[0]
    assert one.start == pytest.approx(rows[14]["t"])
    assert one.end == pytest.approx(rows[16]["t"])
    assert one.resolves_at == pytest.approx(rows[16]["t"])
    assert (one.beat, one.bar, one.in_bar, one.beats) == (15, 4, 3, 2)
    assert one.resolves_into_bar == 5
    assert one.counts["toms"] == 12


def test_confidence_is_highest_when_toms_land_before_a_phrase_boundary() -> None:
    rows = grid()
    # Bar 5 opens the second four-bar phrase; bar 4 opens nothing in particular.
    phrase = fills.candidates(comping(rows) + burst(rows, 15) + burst(rows, 16), rows)
    middle = fills.candidates(comping(rows) + burst(rows, 11) + burst(rows, 12), rows)

    assert phrase.candidates[0].confidence > middle.candidates[0].confidence
    assert phrase.candidates[0].factors["phrase"] == 1.0
    assert middle.candidates[0].factors["phrase"] < 1.0


def test_a_snare_only_burst_scores_below_the_same_burst_on_toms() -> None:
    rows = grid()
    toms = fills.candidates(comping(rows) + burst(rows, 15, stem="toms"), rows)
    snare = fills.candidates(comping(rows) + burst(rows, 15, stem="snare"), rows)

    assert toms.candidates[0].factors["toms"] > snare.candidates[0].factors["toms"]
    assert toms.candidates[0].confidence > snare.candidates[0].confidence


def test_a_run_that_outlasts_a_fill_is_dropped_and_counted() -> None:
    rows = grid()
    busy = [hit for beat in range(9, 9 + fills.MAXIMUM_BEATS + 1) for hit in burst(rows, beat)]

    found = fills.candidates(comping(rows) + busy, rows)

    assert found.candidates == ()
    assert found.dropped == 1


def test_one_quiet_beat_inside_a_fill_does_not_split_it() -> None:
    rows = grid()
    hits = comping(rows) + burst(rows, 13) + burst(rows, 15) + burst(rows, 16)

    found = fills.candidates(hits, rows)

    assert len(found.candidates) == 1
    assert found.candidates[0].beats == 4
    assert found.candidates[0].beat == 13


def test_candidates_below_the_floor_are_left_out_but_still_counted() -> None:
    rows = grid()
    hits = comping(rows) + burst(rows, 11) + burst(rows, 12)

    kept = fills.candidates(hits, rows, minimum_confidence=0.0)
    dropped = fills.candidates(hits, rows, minimum_confidence=1.0)

    assert len(kept.candidates) == 1
    assert dropped.candidates == ()
    assert dropped.considered == 1


def test_a_grid_with_no_beats_yields_no_candidates_rather_than_failing() -> None:
    found = fills.candidates([Hit(seconds=1.0, stem="toms", strength=0.9)], ())

    assert found.candidates == ()
    assert found.baseline == 0.0


def test_rows_carry_the_times_confidence_and_counts_an_agent_reads() -> None:
    rows = grid()
    found = fills.candidates(comping(rows) + burst(rows, 15) + burst(rows, 16), rows)

    record = fills.rows(found)[0]

    assert record["start"] == pytest.approx(rows[14]["t"], abs=1e-3)
    assert record["duration"] == pytest.approx(record["end"] - record["start"], abs=1e-3)
    assert 0.0 <= record["confidence"] <= 1.0
    assert record["toms"] == 12
    assert set(record["factors"]) == {"density", "toms", "resolution", "phrase"}


# --- the transcription seam ---------------------------------------------------------


def test_the_onset_transcriber_finds_each_stem_hit_and_labels_it(tmp_path: Path) -> None:
    stems = {
        "kick": write_hits(tmp_path / "kick.wav", times=(0.5, 1.5), seconds=2.0),
        "snare": write_hits(tmp_path / "snare.wav", times=(1.0,), seconds=2.0),
    }

    hits = drums.transcribe(stems)

    assert [hit.stem for hit in hits] == ["kick", "snare", "kick"]
    assert [round(hit.seconds, 1) for hit in hits] == [0.5, 1.0, 1.5]
    assert all(0.0 < hit.strength <= 1.0 for hit in hits)


def test_a_transcriber_that_falls_over_is_an_analysis_failure(tmp_path: Path) -> None:
    def broken(stems: Mapping[str, Path]) -> tuple[Hit, ...]:
        raise RuntimeError("no model")

    with pytest.raises(AnalysisFailedError) as raised:
        drums.transcribe({"toms": tmp_path / "toms.wav"}, broken)

    assert "no model" in raised.value.cause


# --- the job: what lands on disk, what comes back inline ----------------------------


def stems_on_disk(tmp_path: Path) -> dict[str, Path]:
    """Three drum stems in a directory, the way a separation job leaves them."""
    directory = tmp_path / "stems" / "concert-abc123def456"
    return {
        label: write_hits(directory / f"concert_({label.title()})_model.wav", times=(), seconds=4.0)
        for label in ("kick", "snare", "toms")
    }


def played(rows: Sequence[Mapping[str, Any]]) -> drums.Transcriber:
    hits = tuple(comping(rows) + burst(rows, 15) + burst(rows, 16))

    def transcriber(stems: Mapping[str, Path]) -> tuple[Hit, ...]:
        return hits

    return transcriber


def detector_for(rows: Sequence[Mapping[str, Any]]) -> Any:
    beats = tuple(row["t"] for row in rows)
    downbeats = tuple(row["t"] for row in rows if row["downbeat"])

    def detector(path: Path) -> BeatGrid:
        return BeatGrid(beats=beats, downbeats=downbeats)

    return detector


def _finished(record: JobRecord) -> dict[str, Any]:
    assert record.state == "completed", record.error
    assert record.result is not None
    return record.result


def test_the_job_writes_one_record_per_candidate_and_returns_the_gist(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    rows = grid()

    result = _finished(
        wait_for(
            fills.detect_drum_fills(
                stems_on_disk(tmp_path),
                audio,
                transcriber=played(rows),
                detector=detector_for(rows),
            )["job_id"]
        )
    )

    written = Path(result["path"])
    assert written.parent == get_config().analysis_dir
    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["kind"] == fills.FILLS
    assert len(document[fills.FILLS]) == result["count"] == 1
    assert document[fills.FILLS][0]["confidence"] == pytest.approx(
        result["strongest"]["confidence"]
    )
    assert result["stems"] == ["kick", "snare", "toms"]


def test_the_document_holds_one_candidate_per_line(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    rows = grid()

    result = _finished(
        wait_for(
            fills.detect_drum_fills(
                stems_on_disk(tmp_path),
                audio,
                transcriber=played(rows),
                detector=detector_for(rows),
            )["job_id"]
        )
    )

    lines = Path(result["path"]).read_text(encoding="utf-8").splitlines()
    records = [line for line in lines if line.strip().startswith('{"start"')]
    assert len(records) == 1


def test_the_stems_directory_is_accepted_in_place_of_the_paths(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    rows = grid()
    directory = next(iter(stems_on_disk(tmp_path).values())).parent

    result = _finished(
        wait_for(
            fills.detect_drum_fills(
                directory,
                audio,
                transcriber=played(rows),
                detector=detector_for(rows),
            )["job_id"]
        )
    )

    assert result["stems"] == ["kick", "snare", "toms"]


# --- the cache ----------------------------------------------------------------------


def _must_not_run() -> drums.Transcriber:
    def transcriber(stems: Mapping[str, Path]) -> tuple[Hit, ...]:
        raise AssertionError("the cached result should have answered this")

    return transcriber


def test_asking_twice_answers_from_cache_without_transcribing_again(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    rows = grid()
    stems = stems_on_disk(tmp_path)
    first = _finished(
        wait_for(
            fills.detect_drum_fills(
                stems, audio, transcriber=played(rows), detector=detector_for(rows)
            )["job_id"]
        )
    )

    again = fills.detect_drum_fills(stems, audio, transcriber=_must_not_run())

    assert again["cached"] is True
    assert again["result"] == first


def test_refresh_redoes_the_work_the_cache_would_have_answered(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    rows = grid()
    stems = stems_on_disk(tmp_path)
    wait_for(
        fills.detect_drum_fills(
            stems, audio, transcriber=played(rows), detector=detector_for(rows)
        )["job_id"]
    )

    record = wait_for(
        fills.detect_drum_fills(
            stems,
            audio,
            transcriber=played(rows),
            detector=detector_for(rows),
            refresh=True,
        )["job_id"]
    )

    assert record.cached is False
    assert _finished(record)["count"] == 1


def test_a_different_floor_is_a_different_key(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    rows = grid()
    stems = stems_on_disk(tmp_path)
    wait_for(
        fills.detect_drum_fills(
            stems, audio, transcriber=played(rows), detector=detector_for(rows)
        )["job_id"]
    )

    started = fills.detect_drum_fills(
        stems,
        audio,
        minimum_confidence=0.9,
        transcriber=played(rows),
        detector=detector_for(rows),
    )

    assert started.get("cached") is not True


def test_the_beat_grid_is_the_one_analyze_music_already_paid_for(tmp_path: Path) -> None:
    """The two jobs share the beats cache entry, so the model runs once per master."""
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    rows = grid()
    wait_for(music.analyze_music(audio, energy=False, detector=detector_for(rows))["job_id"])

    def detector_that_must_not_run(path: Path) -> BeatGrid:
        raise AssertionError("beats were already detected for this audio")

    result = _finished(
        wait_for(
            fills.detect_drum_fills(
                stems_on_disk(tmp_path),
                audio,
                transcriber=played(rows),
                detector=detector_that_must_not_run,
            )["job_id"]
        )
    )

    assert result["count"] == 1


# --- what the starter refuses -------------------------------------------------------


def test_a_missing_master_is_refused_before_a_job_exists(tmp_path: Path) -> None:
    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills(stems_on_disk(tmp_path), tmp_path / "nothing.wav")

    assert "nothing.wav" in raised.value.cause


def test_a_stems_directory_holding_no_drum_stems_is_refused(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    empty = tmp_path / "stems" / "nothing"
    write_hits(empty / "concert_(Vocals)_model.wav", times=(), seconds=1.0)

    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills(empty, audio)

    assert raised.value.detail["wanted"] == list(DRUM_STEMS)


def test_a_named_stem_that_is_not_on_disk_is_refused(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)

    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills({"toms": tmp_path / "gone.wav"}, audio)

    assert "toms" in raised.value.cause


@pytest.mark.parametrize("floor", [-0.1, 1.1])
def test_a_confidence_floor_outside_zero_to_one_is_refused(tmp_path: Path, floor: float) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)

    with pytest.raises(InvalidRequestError) as raised:
        fills.detect_drum_fills(stems_on_disk(tmp_path), audio, minimum_confidence=floor)

    assert raised.value.detail["minimum_confidence"] == floor


# --- progress and the tool seam -----------------------------------------------------


def test_progress_only_climbs_and_the_document_is_the_job_artifact(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    rows = grid()
    steps: list[tuple[float, str]] = []

    output = fills.detect(
        audio,
        stems_on_disk(tmp_path),
        {"minimum_confidence": fills.DEFAULT_MINIMUM_CONFIDENCE},
        lambda fraction, step: steps.append((fraction, step)),
        transcriber=played(rows),
        detector=detector_for(rows),
    )

    fractions = [fraction for fraction, _ in steps]
    assert fractions == sorted(fractions)
    assert [step for _, step in steps if "fill" in step]
    assert output.artifacts == (Path(output.result["path"]),)


def test_the_tool_returns_a_job_to_poll_inside_an_ok_envelope(tmp_path: Path) -> None:
    audio = write_clicks(tmp_path / "media" / "concert.wav", seconds=4.0)
    directory = next(iter(stems_on_disk(tmp_path).values())).parent

    envelope = analysis_tools.detect_drum_fills(str(directory), str(audio))

    assert envelope["ok"] is True
    assert "job_id" in envelope["job"]


def test_the_tool_reports_a_bad_request_as_a_structured_error(tmp_path: Path) -> None:
    envelope = analysis_tools.detect_drum_fills(str(tmp_path), str(tmp_path / "nothing.wav"))

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"
