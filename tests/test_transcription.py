"""The transcription job, both scopes.

The model is substituted the way ffmpeg is substituted in acquisition: a ``Transcriber`` is
handed in, so what is under test is everything the server decides — which audio gets
acquired, what the cache key covers, what lands on disk, what comes back inline, and what a
failure downstream of the starter looks like. Whether faster-whisper hears the words right
is not a decision this repo makes, and there is no seam at which it could be asserted.

Fixture audio is two seconds of tone with a written-in gap, so the silence spans in the
document are checked against a gap that is known to be there.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import whisper
from resolve_mcp.analysis.transcribe import transcribe_audio
from resolve_mcp.analysis.transcript import Transcriber, Transcription, Word
from resolve_mcp.audio.ffmpeg import Completed, Runner
from resolve_mcp.config import get_config
from resolve_mcp.errors import (
    AudioExtractionError,
    InvalidRequestError,
    TranscriberUnavailableError,
)
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools import analysis as analysis_tools

from .conftest import Attach
from .fakes import (
    FakeMediaPoolItem,
    FakeProject,
    FakeResolve,
    FakeTimeline,
    media_pool,
    studio,
    write_wav,
)

FIXTURE_SECONDS = 2.0
GAP = (0.6, 1.4)

SPOKEN = (
    ("this", 0.05, 0.25, 0.98),
    ("one", 0.25, 0.45, 0.94),
    ("bridge", 1.45, 1.70, 0.31),
    ("again", 1.70, 1.95, 0.22),
)


@pytest.fixture
def fixture_audio(tmp_path: Path) -> Path:
    """Two seconds of tone with a silent stretch in the middle of it."""
    return write_wav(tmp_path / "media" / "take-1.wav", seconds=FIXTURE_SECONDS, silence=[GAP])


# --- the clip route ----------------------------------------------------------------------


def test_a_clip_is_transcribed_to_a_file_with_gist_stats_returned_inline(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    attach(_studio_holding(fixture_audio))

    record = wait_for(_transcribe(clip="take-1.wav")["job_id"])

    assert record.state == "completed", record.error
    assert record.result is not None
    result = record.result
    assert Path(result["path"]).exists()
    assert result["path"].endswith(".transcript.json")
    assert Path(result["path"]).parent == get_config().analysis_dir
    assert result["language"] == "en"
    assert result["stats"]["word_count"] == len(SPOKEN)
    assert result["stats"]["duration_seconds"] == pytest.approx(FIXTURE_SECONDS, abs=0.01)
    assert result["stats"]["low_confidence_regions"] == 1
    assert result["audio"]["content_sha256"]
    assert "words" not in result


def test_the_document_on_disk_carries_every_word_with_its_timestamps(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    attach(_studio_holding(fixture_audio))

    record = wait_for(_transcribe(clip="take-1.wav")["job_id"])

    assert record.result is not None
    document = _read(Path(record.result["path"]))
    assert [one["word"] for one in document["words"]] == [one[0] for one in SPOKEN]
    assert document["words"][0]["start"] == pytest.approx(0.05)
    assert document["words"][-1]["end"] == pytest.approx(1.95)
    assert document["words"][2]["confidence"] == pytest.approx(0.31)
    assert document["params"]["clip"] == "take-1.wav"
    assert document["audio"]["content_sha256"] == record.result["audio"]["content_sha256"]


def test_the_gap_written_into_the_fixture_comes_back_as_a_silence_span(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    """The spans are RMS-derived, so they find room the words say nothing about."""
    attach(_studio_holding(fixture_audio))

    record = wait_for(_transcribe(clip="take-1.wav")["job_id"])

    assert record.result is not None
    document = _read(Path(record.result["path"]))
    assert len(document["silence"]) == 1
    assert document["silence"][0]["start"] == pytest.approx(GAP[0], abs=0.15)
    assert document["silence"][0]["end"] == pytest.approx(GAP[1], abs=0.15)
    assert record.result["stats"]["silence_seconds"] == pytest.approx(0.8, abs=0.15)


def test_the_unsure_run_is_previewed_inline_so_a_flub_is_visible_without_reading(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    attach(_studio_holding(fixture_audio))

    record = wait_for(_transcribe(clip="take-1.wav")["job_id"])

    assert record.result is not None
    assert record.result["low_confidence_regions"] == [
        {"start": 1.45, "end": 1.95, "duration": 0.5, "words": 2, "text": "bridge again"}
    ]
    assert record.result["low_confidence_truncated"] is False


# --- caching -----------------------------------------------------------------------------


def test_a_rerun_on_unchanged_media_is_an_instant_cache_hit(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    attach(_studio_holding(fixture_audio))
    calls: list[Path] = []
    first = wait_for(_transcribe(clip="take-1.wav", calls=calls)["job_id"])

    again = _transcribe(clip="take-1.wav", calls=calls)

    assert again["state"] == "completed"
    assert again["cached"] is True
    assert again["result"] == first.result
    assert len(calls) == 1


def test_a_different_model_is_a_different_transcript(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    attach(_studio_holding(fixture_audio))
    calls: list[Path] = []
    wait_for(_transcribe(clip="take-1.wav", calls=calls)["job_id"])

    again = _transcribe(clip="take-1.wav", model="small", calls=calls)

    assert again["cached"] is False
    assert wait_for(again["job_id"]).state == "completed"
    assert len(calls) == 2


def test_refresh_transcribes_again_even_when_nothing_moved(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    attach(_studio_holding(fixture_audio))
    calls: list[Path] = []
    wait_for(_transcribe(clip="take-1.wav", calls=calls)["job_id"])

    again = wait_for(_transcribe(clip="take-1.wav", refresh=True, calls=calls)["job_id"])

    assert again.cached is False
    assert again.state == "completed"
    assert len(calls) == 2


def test_the_transcript_is_keyed_on_the_audio_rather_than_on_the_clips_name(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """Re-recorded media under the same name must not answer with the old take's words."""
    source = write_wav(tmp_path / "media" / "take-1.wav", seconds=FIXTURE_SECONDS, silence=[GAP])
    attach(_studio_holding(source))
    calls: list[Path] = []
    first = wait_for(_transcribe(clip="take-1.wav", calls=calls)["job_id"])

    write_wav(source, seconds=FIXTURE_SECONDS + 1.0, silence=[GAP])
    second = wait_for(_transcribe(clip="take-1.wav", calls=calls)["job_id"])

    assert second.cached is False
    assert first.result is not None
    assert second.result is not None
    assert second.result["path"] != first.result["path"]


# --- the timeline route ------------------------------------------------------------------


def test_the_timeline_mix_is_transcribed_through_the_render_queue(attach: Attach) -> None:
    resolve = studio(timeline=FakeTimeline("sunset-set v3", "59.94"))
    attach(resolve)

    record = wait_for(_transcribe()["job_id"])

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["stats"]["word_count"] == len(SPOKEN)
    document = _read(Path(record.result["path"]))
    assert document["params"]["scope"] == "timeline"
    assert document["params"]["timeline"] == "sunset-set v3"
    assert len(_project(resolve).render_jobs) == 1


def test_a_second_transcript_of_one_timeline_does_not_export_the_mix_twice(
    attach: Attach,
) -> None:
    """The acquisition it chains onto has its own cache; a reworded run reuses the WAV."""
    resolve = studio(timeline=FakeTimeline("sunset-set v3", "59.94"))
    attach(resolve)
    wait_for(_transcribe()["job_id"])

    again = wait_for(_transcribe(model="small")["job_id"])

    assert again.state == "completed", again.error
    assert len(_project(resolve).render_jobs) == 1


# --- failures ----------------------------------------------------------------------------


def test_a_clip_with_no_media_says_so_before_a_job_is_ever_started(attach: Attach) -> None:
    attach(_studio_holding(Path("D:/gone/missing.wav")))

    with pytest.raises(AudioExtractionError) as raised:
        _transcribe(clip="missing.wav")

    assert "relink_media" in raised.value.fix


def test_an_acquisition_that_fails_in_its_thread_fails_the_transcript_with_its_advice(
    attach: Attach,
) -> None:
    """The agent needs the render queue's fix, not "the audio was not acquired"."""
    resolve = studio(timeline=FakeTimeline("sunset-set v3", "59.94"))
    _project(resolve).accepts_job = False
    attach(resolve)

    record = wait_for(_transcribe()["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "transcription_failed"
    assert "audio on it" in record.error["fix"]
    assert record.error["detail"]["acquisition"]


def test_a_model_that_raises_fails_the_job_rather_than_the_server(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    attach(_studio_holding(fixture_audio))

    def refusing(audio: Path, params: Mapping[str, Any]) -> Transcription:
        raise TranscriberUnavailableError(cause="faster-whisper is not installed.")

    record = wait_for(
        transcribe_audio(
            get_connection(),
            clip="take-1.wav",
            transcriber=refusing,
            runner=_copying(),
        )["job_id"]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "transcriber_unavailable"


def test_asking_for_a_clip_and_a_timeline_at_once_is_refused(attach: Attach) -> None:
    attach(None)

    with pytest.raises(InvalidRequestError) as raised:
        transcribe_audio(get_connection(), clip="take-1.wav", timeline="sunset-set v3")

    assert "clip" in raised.value.fix


def test_a_bin_without_a_clip_is_refused_rather_than_quietly_ignored(attach: Attach) -> None:
    attach(None)

    with pytest.raises(InvalidRequestError):
        transcribe_audio(get_connection(), bin="Angles")


# --- the backend -------------------------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("faster_whisper") is not None,
    reason="faster-whisper is installed, so the missing-backend path cannot be reached",
)
def test_a_machine_without_faster_whisper_says_how_to_get_it(tmp_path: Path) -> None:
    audio = write_wav(tmp_path / "a.wav", seconds=0.2)

    with pytest.raises(TranscriberUnavailableError) as raised:
        whisper.transcribe(audio, {"model": whisper.DEFAULT_MODEL})

    assert "analysis" in raised.value.fix


# --- the tool ----------------------------------------------------------------------------


def test_the_tool_starts_a_job_and_returns_its_record(
    attach: Attach,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timeline route, so the only substituted thing is the model itself."""
    attach(studio(timeline=FakeTimeline("sunset-set v3", "59.94")))
    monkeypatch.setattr(whisper, "transcribe", _saying(SPOKEN, []))

    reply = analysis_tools.transcribe_audio()

    assert reply["ok"] is True
    assert reply["job"]["kind"] == "transcribe_audio"
    assert reply["job"]["params"]["model"] == whisper.DEFAULT_MODEL
    assert wait_for(reply["job"]["job_id"]).state == "completed"


def test_the_tool_reports_a_bad_request_as_a_failure_envelope(attach: Attach) -> None:
    attach(None)

    reply = analysis_tools.transcribe_audio(clip="take-1.wav", timeline="sunset-set v3")

    assert reply["ok"] is False
    assert reply["error"]["code"] == "invalid_request"


# --- helpers -----------------------------------------------------------------------------


def _transcribe(
    clip: str | None = None,
    model: str | None = None,
    refresh: bool = False,
    calls: list[Path] | None = None,
    **rest: Any,
) -> dict[str, Any]:
    """Start a transcription with the model and (for the clip route) ffmpeg substituted."""
    return transcribe_audio(
        get_connection(),
        clip=clip,
        model=model or whisper.DEFAULT_MODEL,
        refresh=refresh,
        transcriber=_saying(SPOKEN, calls if calls is not None else []),
        runner=_copying(),
        **rest,
    )


def _saying(spoken: Sequence[tuple[str, float, float, float]], calls: list[Path]) -> Transcriber:
    def transcriber(audio: Path, params: Mapping[str, Any]) -> Transcription:
        calls.append(audio)
        heard = tuple(
            Word(text=one[0], start=one[1], end=one[2], confidence=one[3]) for one in spoken
        )
        return Transcription(words=heard, language="en")

    return transcriber


def _copying() -> Runner:
    def runner(argv: Sequence[str]) -> Completed:
        shutil.copyfile(argv[argv.index("-i") + 1], argv[-1])
        return Completed(0, "")

    return runner


def _read(path: Path) -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def _project(resolve: FakeResolve) -> FakeProject:
    project = resolve.current_project
    assert project is not None
    return project


def _studio_holding(source: Path) -> FakeResolve:
    clip = FakeMediaPoolItem(
        source.name,
        file_path=str(source),
        properties={"Type": "Audio", "Audio Ch": "2"},
    )
    return studio(pool=media_pool({"": [clip]}))
