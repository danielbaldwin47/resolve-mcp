"""Audio acquisition, both routes.

The worker tier runs on seconds-long fixture audio — a real WAV written by ``fakes``, not
a stub, because the workers read the header back. The ffmpeg route is exercised twice: once
with the subprocess call substituted, which is where the decisions live (command shape,
refusals, cache behaviour), and once against real ffmpeg when the machine has it, which is
the only thing that proves the command is one ffmpeg accepts.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from resolve_mcp.audio.acquire import (
    _as_export_failure,
    acquire_clip_audio,
    acquire_timeline_audio,
    audio_source,
    mapping_conflict,
)
from resolve_mcp.config import get_config
from resolve_mcp.errors import (
    AudioExportError,
    AudioExtractionError,
    AudioMappingError,
    RenderQueueError,
)
from resolve_mcp.ffmpeg import Completed, Runner
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.resolve.connection import get_connection

from .conftest import Attach
from .fakes import (
    FakeMediaPoolItem,
    FakeProject,
    FakeResolve,
    FakeTimeline,
    FakeTimelineItem,
    ffmpeg_absent,
    ffmpeg_refusing,
    media_pool,
    studio,
    sync_reference,
    with_a_mix,
    write_wav,
)

FIXTURE_SECONDS = 2.0
REFUSAL = "Stream map '0:a:0' matches no streams."


@pytest.fixture
def fixture_audio(tmp_path: Path) -> Path:
    """Two seconds of tone standing in for a concert."""
    return write_wav(tmp_path / "media" / "drums.wav", seconds=FIXTURE_SECONDS)


# --- timeline scope ----------------------------------------------------------------------


def test_the_timeline_mix_comes_off_the_render_queue_as_a_48k_24bit_wav(attach: Attach) -> None:
    resolve = studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94")))
    attach(resolve)

    record = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    assert record.state == "completed"
    assert record.result is not None
    assert Path(record.result["path"]).exists()
    assert record.result["sample_rate"] == 48_000
    assert record.result["bit_depth"] == 24
    assert record.result["scope"] == "timeline"
    assert record.result["content_sha256"]

    project = _project(resolve)
    assert project.render_format == ("wav", "lpcm")
    assert project.render_settings["ExportVideo"] is False
    assert project.render_settings["ExportAudio"] is True
    assert project.render_settings["TargetDir"] == str(get_config().audio_dir)
    assert project.render_queue == []


def test_exporting_another_timeline_puts_the_directors_timeline_back(attach: Attach) -> None:
    open_now = with_a_mix(FakeTimeline("sunset-set v3", "59.94"))
    other = with_a_mix(FakeTimeline("sunset-set v2", "59.94"))
    resolve = studio(timeline=open_now, timelines=[open_now, other])
    attach(resolve)

    wait_for(acquire_timeline_audio(get_connection(), timeline="sunset-set v2")["job_id"])

    assert _project(resolve).timeline_switches == ["sunset-set v2", "sunset-set v3"]


def test_a_rerun_with_an_unchanged_timeline_is_an_instant_cache_hit(attach: Attach) -> None:
    resolve = studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94")))
    attach(resolve)
    first = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    again = acquire_timeline_audio(get_connection())

    assert again["state"] == "completed"
    assert again["cached"] is True
    assert again["result"] == first.result
    assert len(_project(resolve).render_jobs) == 1


def test_refresh_exports_again_even_when_the_timeline_looks_unchanged(attach: Attach) -> None:
    resolve = studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94")))
    attach(resolve)
    wait_for(acquire_timeline_audio(get_connection())["job_id"])

    again = wait_for(acquire_timeline_audio(get_connection(), refresh=True)["job_id"])

    assert again.cached is False
    assert again.state == "completed"
    assert len(_project(resolve).render_jobs) == 2


def test_an_edited_timeline_is_a_different_key_and_exports_again(attach: Attach) -> None:
    """The fingerprint has to move when the cut does, or analysis reads yesterday's mix."""
    timeline = with_a_mix(FakeTimeline("sunset-set v3", "59.94"))
    resolve = studio(timeline=timeline)
    attach(resolve)
    first = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    timeline._end_frame = timeline.GetEndFrame() + 500
    second = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    assert second.cached is False
    assert second.result is not None
    assert first.result is not None
    assert second.result["path"] != first.result["path"]


def test_a_take_swap_that_keeps_the_duration_still_exports_again(attach: Attach) -> None:
    """Bounds and track counts alone would call a recut timeline unchanged."""
    timeline = with_a_mix(sync_reference())
    resolve = studio(timeline=timeline)
    attach(resolve)
    first = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    shots = timeline.GetItemListInTrack("video", 1)
    assert shots is not None
    shots[0]._name = "Cam C.mp4"
    second = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    assert first.state == "completed"
    assert second.cached is False
    assert len(_project(resolve).render_jobs) == 2


def test_the_export_renders_one_file_for_the_whole_timeline(attach: Attach) -> None:
    """The other render mode writes a file per clip — hundreds of fragments, not the mix."""
    resolve = studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94")))
    attach(resolve)

    wait_for(acquire_timeline_audio(get_connection())["job_id"])

    assert _project(resolve).render_mode == 1


def test_the_mix_still_exports_on_a_build_that_refuses_the_wav_pair(attach: Attach) -> None:
    """Resolve 21.0.3 refuses every ("wav", …) pair, and the mix has to come out anyway.

    The stock ``Audio Only`` preset is the only route to a WAV on that build (#32, live),
    so the worker names it as the fallback. Without this test the worker could stop passing
    it and every other test here would stay green.
    """
    resolve = studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94")))
    project = _project(resolve)
    project.accepts_format = False
    project.render_presets["Audio Only"] = ("wav", "lpcm")
    attach(resolve)

    record = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    assert record.state == "completed", record.error
    assert record.result is not None
    assert Path(record.result["path"]).exists()
    assert project.loaded_presets == ["Audio Only"]
    # The preset chose the format; the caller still chose where it lands.
    assert project.render_settings["TargetDir"] == str(get_config().audio_dir)
    assert project.render_settings["ExportVideo"] is False


def test_a_build_with_no_wav_route_at_all_fails_the_job_naming_both(attach: Attach) -> None:
    """The pair refused and no usable fallback: the reply has to name the preset too,
    or the machine that cannot render audio looks identical to one missing a codec."""
    resolve = studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94")))
    _project(resolve).accepts_format = False  # and no "Audio Only" among the presets
    attach(resolve)

    record = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "audio_export_failed"
    assert "Audio Only" in record.error["cause"]


def test_a_queue_that_refuses_the_job_fails_the_job_not_the_server(attach: Attach) -> None:
    resolve = studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94")))
    _project(resolve).accepts_job = False
    attach(resolve)

    record = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "audio_export_failed"
    assert "audio on it" in record.error["fix"]


def test_a_timeline_with_no_audio_items_is_refused_before_anything_is_queued(
    attach: Attach,
) -> None:
    """#88, live on 21.0.3.7: Resolve queues this render and never runs it.

    The job sits at "Ready for background render" at 0% with ``IsRenderingInProgress()``
    reporting True and no dialog open, and the caller's wait blocks until its own timeout.
    Nothing downstream can recover from that or explain it, so the queue must never see it.
    """
    resolve = studio(timeline=FakeTimeline("sunset-set v3", "59.94", video=[[_shot()]]))
    attach(resolve)

    with pytest.raises(AudioExportError) as raised:
        acquire_timeline_audio(get_connection())

    assert "no audio items" in raised.value.cause
    assert "sunset-set v3" in raised.value.cause
    assert _project(resolve).render_jobs == []


def test_an_analysis_of_a_timeline_with_no_audio_declines_up_front_too(attach: Attach) -> None:
    """``audio_source`` promises the route's refusals before a job starts, not inside one."""
    attach(studio(timeline=FakeTimeline("sunset-set v3", "59.94", video=[[_shot()]])))

    with pytest.raises(AudioExportError):
        audio_source(get_connection())


def test_a_timeline_with_audio_on_a_later_track_is_still_exportable(attach: Attach) -> None:
    """The count is across every audio track: an empty A1 says nothing about the mix."""
    timeline = FakeTimeline(
        "sunset-set v3",
        "59.94",
        video=[[_shot()]],
        audio=[[], [FakeTimelineItem("Board mix.wav", 0, 500)]],
    )
    attach(studio(timeline=timeline))

    record = wait_for(acquire_timeline_audio(get_connection())["job_id"])

    assert record.state == "completed", record.error


def _shot() -> FakeTimelineItem:
    """One video item, so the refusal is about the audio and not about an empty timeline."""
    return FakeTimelineItem("Cam A.mp4", 0, 500)


# --- clip scope --------------------------------------------------------------------------


def test_a_source_clip_is_extracted_straight_off_disk(attach: Attach, fixture_audio: Path) -> None:
    attach(_studio_holding(fixture_audio))
    calls: list[Sequence[str]] = []

    record = wait_for(
        acquire_clip_audio(get_connection(), "drums.wav", runner=_copying(calls))["job_id"]
    )

    assert record.state == "completed"
    assert record.result is not None
    assert record.result["duration_seconds"] == pytest.approx(FIXTURE_SECONDS, abs=0.01)
    assert record.result["scope"] == "clip"
    assert record.result["content_sha256"]
    assert Path(record.result["path"]).parent == get_config().audio_dir
    assert calls[0][0] == "ffmpeg"
    assert "-vn" in calls[0]
    assert "pcm_s24le" in calls[0]


def test_the_same_unchanged_clip_never_runs_ffmpeg_twice(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    attach(_studio_holding(fixture_audio))
    calls: list[Sequence[str]] = []
    wait_for(acquire_clip_audio(get_connection(), "drums.wav", runner=_copying(calls))["job_id"])

    again = acquire_clip_audio(get_connection(), "drums.wav", runner=_copying(calls))

    assert again["cached"] is True
    assert len(calls) == 1


def test_a_clip_whose_media_is_gone_says_so_before_starting_a_job(attach: Attach) -> None:
    attach(_studio_holding(Path("D:/gone/missing.wav")))

    with pytest.raises(AudioExtractionError) as raised:
        acquire_clip_audio(get_connection(), "missing.wav")

    assert "relink_media" in raised.value.fix


def test_a_clip_whose_audio_lives_somewhere_else_is_sent_to_the_timeline_route(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    """Extracting the source file would silently analyse audio that is not in the cut."""
    linked = '{"linked_audio": [{"file_path": "D:/recorder/take-1.wav"}]}'
    attach(_studio_holding(fixture_audio, audio_mapping=linked))

    with pytest.raises(AudioMappingError) as raised:
        acquire_clip_audio(get_connection(), "drums.wav")

    assert "take-1.wav" in raised.value.cause
    assert "scope=timeline" in raised.value.fix


def test_no_ffmpeg_on_the_machine_is_a_named_failure(attach: Attach, fixture_audio: Path) -> None:
    attach(_studio_holding(fixture_audio))

    record = wait_for(
        acquire_clip_audio(get_connection(), "drums.wav", runner=ffmpeg_absent)["job_id"]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "ffmpeg_unavailable"
    assert "RESOLVE_MCP_FFMPEG" in record.error["fix"]


def test_ffmpegs_own_complaint_travels_back_with_the_failure(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    attach(_studio_holding(fixture_audio))

    record = wait_for(
        acquire_clip_audio(get_connection(), "drums.wav", runner=ffmpeg_refusing(REFUSAL))["job_id"]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "audio_extraction_failed"
    assert "Stream map" in record.error["detail"]["stderr"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_real_ffmpeg_accepts_the_command_and_writes_the_wav(
    attach: Attach,
    fixture_audio: Path,
) -> None:
    """The one assertion the substituted runner cannot make: ffmpeg agrees with the argv."""
    attach(_studio_holding(fixture_audio))

    record = wait_for(acquire_clip_audio(get_connection(), "drums.wav")["job_id"])

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["sample_rate"] == 48_000
    assert record.result["bit_depth"] == 24
    assert record.result["duration_seconds"] == pytest.approx(FIXTURE_SECONDS, abs=0.05)


# --- the audio-mapping check ---------------------------------------------------------------


def test_a_queue_failure_keeps_the_advice_the_queue_was_specific_about() -> None:
    """A stalled queue names the dialog that stalled it; that beats the generic audio advice."""
    stalled = RenderQueueError(cause="The render job was still Rendering.", fix="Check Deliver.")
    refused = RenderQueueError(cause="Resolve would not add the job to the render queue.")

    assert _as_export_failure(stalled).fix == "Check Deliver."
    assert "audio on it" in _as_export_failure(refused).fix
    assert _as_export_failure(refused).code == "audio_export_failed"


# --- the audio-mapping check ---------------------------------------------------------------


def test_an_ordinary_embedded_mapping_is_no_conflict() -> None:
    mapping = {"embedded_audio_channels": 2, "track_info": [{"type": "stereo", "channel": [1, 2]}]}

    assert mapping_conflict(mapping, "D:/media/drums.wav") is None
    assert mapping_conflict(None, "D:/media/drums.wav") is None
    assert mapping_conflict({}, "D:/media/drums.wav") is None


def test_the_clips_own_path_written_a_different_way_is_no_conflict() -> None:
    mapping = {"track_info": [{"source": "D:\\media\\drums.wav"}]}

    assert mapping_conflict(mapping, "D:/media/drums.wav") is None


def test_a_non_zero_offset_is_a_conflict() -> None:
    mapping = {"track_info": [{"audio_offset": 1024}]}

    assert mapping_conflict(mapping, "D:/media/drums.wav") is not None
    assert mapping_conflict({"track_info": [{"audio_offset": 0}]}, "D:/media/drums.wav") is None


# --- helpers -------------------------------------------------------------------------------


def _project(resolve: FakeResolve) -> FakeProject:
    project = resolve.current_project
    assert project is not None
    return project


def _studio_holding(source: Path, audio_mapping: str | None = None) -> FakeResolve:
    clip = FakeMediaPoolItem(
        source.name,
        file_path=str(source),
        properties={"Type": "Audio", "Audio Ch": "2"},
        audio_mapping=audio_mapping,
    )
    return studio(pool=media_pool({"": [clip]}))


def _copying(calls: list[Sequence[str]]) -> Runner:
    """Stand in for ffmpeg by copying the input — the transcode is not what is under test."""

    def runner(argv: Sequence[str]) -> Completed:
        calls.append(list(argv))
        shutil.copyfile(argv[argv.index("-i") + 1], argv[-1])
        return Completed(0, "")

    return runner


