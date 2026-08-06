"""The music analysis job: beats and downbeats, energy curves, gist stats, cache.

The beat model is behind an injectable detector (ADR 0002), so what verifies here is every
*decision* the worker makes — what lands on disk, what comes back inline, what a rerun costs
— on fixture audio of a few seconds. Whether beat_this hears the right beat is the model's
business, and no seam in this repo can answer it.
"""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import beats as beats_module
from resolve_mcp.analysis import music
from resolve_mcp.analysis.beats import BeatGrid
from resolve_mcp.config import get_config
from resolve_mcp.jobs import cache, store
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.tools import analysis as analysis_tools

from .fakes import write_clicks, write_wav

FIXTURE_SECONDS = 4.0


@pytest.fixture
def fixture_audio(tmp_path: Path) -> Path:
    return write_clicks(tmp_path / "media" / "concert.wav", seconds=FIXTURE_SECONDS)


def _grid(beats_per_minute: float = 120.0, seconds: float = FIXTURE_SECONDS) -> BeatGrid:
    """A steady four-four grid, standing in for what beat_this would hear."""
    step = 60.0 / beats_per_minute
    times = tuple(round(index * step, 6) for index in range(int(seconds / step)))
    return BeatGrid(beats=times, downbeats=times[::4])


def _detector(grid: BeatGrid | None = None) -> beats_module.Detector:
    def detect(path: Path) -> BeatGrid:
        return grid if grid is not None else _grid()

    return detect


def _finished(started: dict[str, Any]) -> store.JobRecord:
    return wait_for(started["job_id"])


def _result(started: dict[str, Any]) -> dict[str, Any]:
    record = _finished(started)
    assert record.state == "completed", record.error
    assert record.result is not None
    return record.result


# --- what lands on disk ----------------------------------------------------------------


def test_beats_and_downbeats_land_on_disk(fixture_audio: Path) -> None:
    result = _result(music.analyze_music(fixture_audio, energy=False, detector=_detector()))

    written = json.loads(Path(result["beats"]["path"]).read_text(encoding="utf-8"))

    assert [beat["t"] for beat in written["beats"]][:3] == [0.0, 0.5, 1.0]
    assert written["beats"][0] == {"t": 0.0, "beat": 1, "bar": 1, "in_bar": 1, "downbeat": True}
    assert written["beats"][1]["downbeat"] is False
    assert written["beats"][4] == {"t": 2.0, "beat": 5, "bar": 2, "in_bar": 1, "downbeat": True}


def test_the_beats_file_is_readable_in_slices(fixture_audio: Path) -> None:
    """One record per line, so a concert is read with sed, not with the whole file inline."""
    result = _result(music.analyze_music(fixture_audio, energy=False, detector=_detector()))

    lines = Path(result["beats"]["path"]).read_text(encoding="utf-8").splitlines()
    records = [line for line in lines if line.lstrip().startswith('{"t":')]

    assert len(records) == result["beats"]["count"]
    assert json.loads(records[0].rstrip(","))["t"] == 0.0


def test_the_energy_curve_lands_on_disk_with_lufs_rms_and_onsets(fixture_audio: Path) -> None:
    result = _result(
        music.analyze_music(fixture_audio, beats=False, window_seconds=1.0, hop_seconds=0.5)
    )

    written = json.loads(Path(result["energy"]["path"]).read_text(encoding="utf-8"))
    first = written["energy"][0]

    assert set(first) == {"t", "lufs", "rms_dbfs", "onsets_per_second"}
    assert first["t"] == 0.0
    assert written["window_seconds"] == 1.0
    assert len(written["energy"]) == result["energy"]["count"] > 1


def test_the_two_curves_are_separate_files(fixture_audio: Path) -> None:
    """Grepping beats must not mean paging in the energy curve as well."""
    result = _result(music.analyze_music(fixture_audio, detector=_detector()))

    assert result["beats"]["path"] != result["energy"]["path"]
    assert Path(result["beats"]["path"]).parent == get_config().analysis_dir


# --- what comes back inline -----------------------------------------------------------


def test_gist_stats_come_back_inline_and_the_curves_do_not(fixture_audio: Path) -> None:
    result = _result(music.analyze_music(fixture_audio, detector=_detector()))

    assert result["beats"]["count"] == 8
    assert result["beats"]["downbeat_count"] == 2
    assert result["beats"]["tempo_bpm"] == pytest.approx(120.0, abs=0.5)
    assert result["beats"]["meter"] == 4
    assert result["energy"]["integrated_lufs"] < 0.0
    assert result["audio"]["duration_seconds"] == pytest.approx(FIXTURE_SECONDS, abs=0.01)
    assert "beats" not in json.dumps(result["energy"])
    assert not any(isinstance(value, list) for value in result["beats"].values())


def test_the_gist_names_where_the_loudest_and_quietest_moments_are(tmp_path: Path) -> None:
    """The first question asked of an energy curve is "where does it lift?"."""
    quiet = write_wav(tmp_path / "quiet.wav", seconds=1.0, amplitude=0.02)
    loud = write_wav(tmp_path / "loud.wav", seconds=1.0, amplitude=0.6)
    joined = _joined(tmp_path / "joined.wav", quiet, loud)

    result = _result(music.analyze_music(joined, beats=False, window_seconds=0.5, hop_seconds=0.5))

    assert result["energy"]["loudest"]["t"] >= 1.0
    assert result["energy"]["quietest"]["t"] < 1.0
    assert result["energy"]["loudest"]["lufs"] > result["energy"]["quietest"]["lufs"] + 15.0


def test_either_half_can_be_skipped(fixture_audio: Path) -> None:
    beats_only = _result(music.analyze_music(fixture_audio, energy=False, detector=_detector()))
    energy_only = _result(music.analyze_music(fixture_audio, beats=False))

    assert "energy" not in beats_only
    assert "beats" not in energy_only


def test_asking_for_neither_is_refused_before_a_job_is_started(fixture_audio: Path) -> None:
    with pytest.raises(Exception) as raised:
        music.analyze_music(fixture_audio, beats=False, energy=False)

    assert getattr(raised.value, "code", None) == "invalid_request"
    assert store.load_all() == []


# --- cache -----------------------------------------------------------------------------


def test_a_rerun_on_unchanged_audio_is_answered_from_cache(fixture_audio: Path) -> None:
    first = _result(music.analyze_music(fixture_audio, detector=_detector()))

    second = music.analyze_music(fixture_audio, detector=_detector_that_must_not_run())

    assert second["cached"] is True
    assert second["state"] == "completed"
    assert second["result"] == first


def test_different_params_are_a_different_cache_entry(fixture_audio: Path) -> None:
    _result(music.analyze_music(fixture_audio, beats=False, hop_seconds=0.5))
    second = music.analyze_music(fixture_audio, beats=False, hop_seconds=0.25)

    assert second["cached"] is False
    assert _result(second)["energy"]["count"] > 0


def test_refresh_redoes_the_work_and_replaces_the_entry(fixture_audio: Path) -> None:
    _result(music.analyze_music(fixture_audio, energy=False, detector=_detector()))

    redone = _result(
        music.analyze_music(
            fixture_audio,
            energy=False,
            refresh=True,
            detector=_detector(_grid(beats_per_minute=60.0)),
        )
    )

    assert redone["beats"]["count"] == 4
    kept = _detector_that_must_not_run()
    again = music.analyze_music(fixture_audio, energy=False, detector=kept)
    assert again["cached"] is True
    assert again["result"] == redone


def test_changing_the_energy_window_does_not_re_run_the_beat_model(fixture_audio: Path) -> None:
    """Beats cost minutes of GPU on a concert; an energy setting must not buy them again."""
    first = _result(music.analyze_music(fixture_audio, hop_seconds=0.5, detector=_detector()))

    second = _result(
        music.analyze_music(
            fixture_audio, hop_seconds=0.25, detector=_detector_that_must_not_run()
        )
    )

    assert second["beats"] == first["beats"]
    assert second["energy"]["count"] != first["energy"]["count"]


def test_running_one_half_then_both_reuses_the_half_already_paid_for(fixture_audio: Path) -> None:
    first = _result(music.analyze_music(fixture_audio, beats=False))

    both = _result(music.analyze_music(fixture_audio, detector=_detector()))

    assert both["energy"] == first["energy"]


def test_audio_the_server_wrote_is_identified_by_its_content(fixture_audio: Path) -> None:
    """A cache directory WAV is hashed, so the same mix under two names is analysed once."""
    acquired = get_config().audio_dir / "mix-abc123.wav"
    acquired.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture_audio, acquired)
    renamed = acquired.with_name("mix-def456.wav")
    shutil.copyfile(fixture_audio, renamed)

    _result(music.analyze_music(acquired, energy=False, detector=_detector()))
    again = music.analyze_music(renamed, energy=False, detector=_detector_that_must_not_run())

    assert again["cached"] is True


def test_a_master_the_director_handed_over_is_not_read_end_to_end(
    fixture_audio: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concert master is tens of gigabytes; hashing it would stall the starter (jobs.cache)."""

    def refuse(path: Any) -> str:
        raise AssertionError("source media the server did not write is fingerprinted, not hashed")

    monkeypatch.setattr(cache, "content_hash", refuse)

    assert _result(music.analyze_music(fixture_audio, energy=False, detector=_detector()))


def test_a_deleted_curve_file_is_not_a_cache_hit(fixture_audio: Path) -> None:
    """The cache directory is the user's to tidy; a hit pointing at nothing is a lie."""
    first = _result(music.analyze_music(fixture_audio, energy=False, detector=_detector()))
    Path(first["beats"]["path"]).unlink()

    second = music.analyze_music(fixture_audio, energy=False, detector=_detector())

    assert second["cached"] is False
    assert Path(_result(second)["beats"]["path"]).exists()


# --- failures ---------------------------------------------------------------------------


def test_a_missing_file_is_refused_before_a_job_is_started(tmp_path: Path) -> None:
    with pytest.raises(Exception) as raised:
        music.analyze_music(tmp_path / "nothing.wav")

    assert getattr(raised.value, "code", None) == "invalid_request"


def test_a_file_that_is_not_audio_fails_the_job_with_a_fix(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not audio", encoding="utf-8")

    record = _finished(music.analyze_music(path, beats=False))

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "audio_extraction_failed"


def test_a_missing_beat_model_names_the_install(
    fixture_audio: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        raise ImportError(f"No module named {name!r}")

    monkeypatch.setattr(importlib, "import_module", refuse)

    record = _finished(music.analyze_music(fixture_audio, energy=False))

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "analysis_dependency_missing"
    assert "beat_this" in record.error["fix"]


def test_a_detector_that_raises_is_reported_as_an_analysis_failure(fixture_audio: Path) -> None:
    failing = _failing_detector()
    record = _finished(music.analyze_music(fixture_audio, energy=False, detector=failing))

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "analysis_failed"


def test_a_detector_that_hears_nothing_is_an_empty_grid_not_a_crash(fixture_audio: Path) -> None:
    result = _result(
        music.analyze_music(fixture_audio, energy=False, detector=_detector(BeatGrid((), ())))
    )

    assert result["beats"]["count"] == 0
    assert result["beats"]["tempo_bpm"] is None
    assert Path(result["beats"]["path"]).exists()


# --- progress and the tool seam ----------------------------------------------------------


def test_the_job_reports_progress_while_it_runs(fixture_audio: Path) -> None:
    steps: list[tuple[float, str]] = []

    def watched(fraction: float, step: str) -> None:
        steps.append((fraction, step))

    settings = {"beats": True, "energy": True, "window_seconds": 1.0, "hop_seconds": 0.5}
    output = music.analyze(fixture_audio, settings, watched, detector=_detector())

    fractions = [fraction for fraction, _ in steps]
    assert fractions == sorted(fractions)
    assert [step for _, step in steps if "beat" in step]
    assert set(output.artifacts) == {
        Path(output.result["beats"]["path"]),
        Path(output.result["energy"]["path"]),
    }


def test_analysis_does_not_serialise_behind_a_resolve_job(fixture_audio: Path) -> None:
    """Pure compute must run while a render holds the Resolve lock (#22: jobs never stall)."""
    from resolve_mcp.jobs import runner

    with runner.RESOLVE_LOCK:
        record = _finished(music.analyze_music(fixture_audio, energy=False, detector=_detector()))

    assert record.state == "completed"


def test_the_tool_returns_the_job_record_in_an_envelope(fixture_audio: Path) -> None:
    reply = analysis_tools.analyze_music(str(fixture_audio), beats=False)

    assert reply["ok"] is True
    assert reply["job"]["kind"] == music.KIND
    assert _finished(reply["job"]).state == "completed"


def test_the_tool_reports_a_bad_path_as_a_failure_not_a_crash(tmp_path: Path) -> None:
    reply = analysis_tools.analyze_music(str(tmp_path / "nothing.wav"))

    assert reply["ok"] is False
    assert reply["error"]["code"] == "invalid_request"


def _joined(target: Path, first: Path, second: Path) -> Path:
    import wave

    with wave.open(str(first), "rb") as head, wave.open(str(second), "rb") as tail:
        params = head.getparams()
        frames = head.readframes(head.getnframes()) + tail.readframes(tail.getnframes())
    with wave.open(str(target), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(frames)
    return target


def _failing_detector() -> beats_module.Detector:
    def detect(path: Path) -> BeatGrid:
        raise RuntimeError("the model fell over")

    return detect


def _detector_that_must_not_run() -> beats_module.Detector:
    def detect(path: Path) -> BeatGrid:
        raise AssertionError("this analysis was supposed to be reused, not recomputed")

    return detect
