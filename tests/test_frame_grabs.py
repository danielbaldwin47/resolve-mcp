"""Frame grabs: seeing a chosen moment on a chosen angle.

The subprocess call is substituted the way the audio route substitutes it, because that is
where the decisions are: which seconds ffmpeg is seeked to, that the scale filter keeps the
long edge inside the agent's image cap, what a refusal looks like, and that an unchanged
clip is never grabbed twice. The stand-in writes a JPEG header carrying real dimensions —
the worker reads them back off the file, so a stub of zero bytes would prove nothing.

The one thing no stand-in can prove — that ffmpeg accepts this argv and writes a JPEG whose
long edge really is inside the cap — runs against real ffmpeg when the machine has it.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from resolve_mcp.config import get_config
from resolve_mcp.errors import FfmpegUnavailableError, FrameGrabError, InvalidRequestError
from resolve_mcp.ffmpeg import Completed, Runner
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools import video as video_tools
from resolve_mcp.video import jpeg
from resolve_mcp.video.frames import DEFAULT_MAX_EDGE, MAX_TIMES, grab_frames

from .conftest import Attach
from .fakes import (
    FakeMediaPoolItem,
    FakeResolve,
    ffmpeg_absent,
    ffmpeg_refusing,
    hwaccel_probe_reply,
    media_pool,
    studio,
    write_jpeg,
)

CLIP_FPS = 59.94
REFUSAL = "[mp4 @ 0] moov atom not found\nInvalid data found processing input"


@pytest.fixture
def fixture_video(tmp_path: Path) -> Path:
    """Stands in for an angle on disk: the grab route only ever stats it."""
    target = tmp_path / "media" / "C0012.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not really an mp4, but a file with a size and an mtime")
    return target


# --- the grab ------------------------------------------------------------------------------


def test_a_chosen_moment_comes_back_as_a_jpeg_the_agent_can_read(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    reading = grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))

    grabbed = reading["frames"][0]
    assert Path(grabbed["path"]).exists()
    assert Path(grabbed["path"]).parent == get_config().frame_dir
    assert Path(grabbed["path"]).suffix == ".jpg"
    assert grabbed["width"] == 1568
    assert grabbed["height"] == 882
    assert grabbed["bytes"] > 0
    assert calls[0][0] == "ffmpeg"
    assert "-frames:v" in calls[0]


def test_every_grabbed_moment_carries_dual_time(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))

    reading = grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing([]))

    assert reading["frames"][0]["time"] == {
        "frames": 60,
        "seconds": pytest.approx(1.001, abs=0.001),
        "timecode": "00:00:01:00",
        "fps": CLIP_FPS,
    }


def test_a_time_in_seconds_is_taken_when_it_says_which_way_to_snap(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    reading = grab_frames(
        get_connection(),
        "C0012.mp4",
        [{"seconds": 1.5, "snap": "floor"}],
        runner=_drawing(calls),
    )

    assert reading["frames"][0]["time"]["frames"] == 89
    assert _seek(calls[0]) == pytest.approx(89 / CLIP_FPS, abs=0.001)


def test_bare_seconds_are_refused_the_same_way_every_other_time_is(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(InvalidRequestError) as raised:
        grab_frames(get_connection(), "C0012.mp4", [{"seconds": 1.5}], runner=_drawing([]))

    assert "snap" in raised.value.fix


def test_the_seek_is_measured_from_the_start_of_the_file_not_the_clips_timecode(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """Resolve numbers a clip from its own Start; ffmpeg only ever counts from zero."""
    attach(_studio_holding(fixture_video, {"Start": "3600", "End": "3699", "Frames": "100"}))
    calls: list[Sequence[str]] = []

    grab_frames(get_connection(), "C0012.mp4", [3660], runner=_drawing(calls))

    assert _seek(calls[0]) == pytest.approx(60 / CLIP_FPS, abs=0.001)


def test_the_grab_is_scaled_into_the_agents_image_cap(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))

    scale = calls[0][calls[0].index("-vf") + 1]
    assert str(DEFAULT_MAX_EDGE) in scale
    assert "force_original_aspect_ratio=decrease" in scale


def test_a_smaller_cap_can_be_asked_for_but_a_bigger_one_cannot(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    grab_frames(get_connection(), "C0012.mp4", [60], max_edge=640, runner=_drawing(calls))

    assert "640" in calls[0][calls[0].index("-vf") + 1]
    with pytest.raises(InvalidRequestError) as raised:
        grab_frames(get_connection(), "C0012.mp4", [60], max_edge=4096, runner=_drawing([]))
    assert str(DEFAULT_MAX_EDGE) in raised.value.fix


def test_several_moments_come_back_in_one_call_one_file_each(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    reading = grab_frames(get_connection(), "C0012.mp4", [0, 30, 60], runner=_drawing(calls))

    assert [one["time"]["frames"] for one in reading["frames"]] == [0, 30, 60]
    assert len({one["path"] for one in reading["frames"]}) == 3
    assert len(calls) == 3


def test_the_same_moment_asked_for_twice_is_grabbed_once(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    reading = grab_frames(get_connection(), "C0012.mp4", [60, 60], runner=_drawing(calls))

    assert len(reading["frames"]) == 1
    assert len(calls) == 1


# --- the cache -----------------------------------------------------------------------------


def test_an_unchanged_clip_and_the_same_times_never_run_ffmpeg_twice(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    first = grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))

    again = grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))

    assert first["cached"] is False
    assert again["cached"] is True
    assert again["frames"] == first["frames"]
    assert len(calls) == 1


def test_different_times_are_a_different_cache_entry(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))

    again = grab_frames(get_connection(), "C0012.mp4", [90], runner=_drawing(calls))

    assert again["cached"] is False
    assert len(calls) == 2


def test_a_second_call_only_grabs_the_moment_the_first_one_did_not(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """A session narrows in: the next call is usually the last one's times plus one more."""
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    first = grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))

    again = grab_frames(get_connection(), "C0012.mp4", [60, 90], runner=_drawing(calls))

    assert len(calls) == 2
    assert again["cached"] is False
    assert again["frames"][0] == first["frames"][0]


def test_a_grab_whose_jpeg_was_deleted_is_taken_again(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    first = grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))
    Path(first["frames"][0]["path"]).unlink()

    again = grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))

    assert again["cached"] is False
    assert len(calls) == 2


def test_refresh_takes_the_grab_again_and_replaces_the_entry(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))

    again = grab_frames(get_connection(), "C0012.mp4", [60], refresh=True, runner=_drawing(calls))

    assert again["cached"] is False
    assert len(calls) == 2


# --- refusals ------------------------------------------------------------------------------


def test_a_time_outside_the_clips_own_media_is_refused_before_ffmpeg_runs(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    with pytest.raises(InvalidRequestError) as raised:
        grab_frames(get_connection(), "C0012.mp4", [100], runner=_drawing(calls))

    assert raised.value.detail["bounds"]["out"]["frames"] == 100
    assert calls == []


def test_asking_for_nothing_or_for_too_much_is_refused(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(InvalidRequestError) as empty:
        grab_frames(get_connection(), "C0012.mp4", [], runner=_drawing([]))
    with pytest.raises(InvalidRequestError) as flooded:
        grab_frames(get_connection(), "C0012.mp4", list(range(MAX_TIMES + 1)), runner=_drawing([]))

    assert "at least one" in empty.value.fix
    assert str(MAX_TIMES) in flooded.value.fix


def test_an_empty_entry_in_times_is_refused_rather_than_quietly_dropped(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """Dropping it would return fewer frames than were asked for without saying which."""
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    with pytest.raises(InvalidRequestError) as raised:
        grab_frames(get_connection(), "C0012.mp4", [60, None], runner=_drawing(calls))

    assert raised.value.detail["field"] == "times[1]"
    assert calls == []


def test_a_clip_whose_media_is_gone_says_so_before_running_ffmpeg(attach: Attach) -> None:
    attach(_studio_holding(Path("D:/gone/C0012.mp4")))

    with pytest.raises(FrameGrabError) as raised:
        grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing([]))

    assert "relink_media" in raised.value.fix


def test_a_clip_resolve_reports_no_frame_rate_for_cannot_be_seeked(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video, {"FPS": ""}))

    with pytest.raises(InvalidRequestError) as raised:
        grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing([]))

    assert "frame rate" in raised.value.cause


def test_no_ffmpeg_on_the_machine_is_a_named_failure(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(FfmpegUnavailableError) as raised:
        grab_frames(get_connection(), "C0012.mp4", [60], runner=ffmpeg_absent)

    assert "RESOLVE_MCP_FFMPEG" in raised.value.fix


def test_ffmpegs_own_complaint_travels_back_with_the_failure(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(FrameGrabError) as raised:
        grab_frames(get_connection(), "C0012.mp4", [60], runner=ffmpeg_refusing(REFUSAL))

    assert "Invalid data found" in raised.value.detail["stderr"]


def test_a_grab_that_writes_nothing_is_a_failure_not_a_silent_success(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(FrameGrabError) as raised:
        grab_frames(get_connection(), "C0012.mp4", [60], runner=_pretending)

    assert "wrote nothing" in raised.value.cause


def test_a_failed_grab_leaves_no_cache_entry_behind(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    with pytest.raises(FrameGrabError):
        grab_frames(get_connection(), "C0012.mp4", [60], runner=ffmpeg_refusing(REFUSAL))

    reading = grab_frames(get_connection(), "C0012.mp4", [60], runner=_drawing(calls))

    assert reading["cached"] is False
    assert len(calls) == 1


# --- reading a JPEG back ---------------------------------------------------------------------


def test_the_jpeg_header_gives_up_the_dimensions_the_agent_will_see(tmp_path: Path) -> None:
    written = write_jpeg(tmp_path / "frame.jpg", width=1280, height=720)

    reading = jpeg.describe(written)

    assert reading["width"] == 1280
    assert reading["height"] == 720
    assert reading["bytes"] == written.stat().st_size
    assert reading["path"] == str(written)


def test_a_file_that_is_not_a_jpeg_is_refused_rather_than_guessed_at(tmp_path: Path) -> None:
    impostor = tmp_path / "frame.jpg"
    impostor.write_bytes(b"\x89PNG\r\n\x1a\n and then some")

    with pytest.raises(FrameGrabError):
        jpeg.describe(impostor)


# --- the tool ---------------------------------------------------------------------------------


def test_the_tool_shapes_a_refusal_rather_than_raising(attach: Attach, fixture_video: Path) -> None:
    """The tool layer takes no subprocess seam — the refusal lands before ffmpeg is reached."""
    attach(_studio_holding(fixture_video))

    envelope = video_tools.grab_frames("C0012.mp4", [9999])

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"
    assert "context" in envelope


def test_a_tool_without_the_flag_is_never_told_to_pass_recursive(
    attach: Attach, fixture_video: Path
) -> None:
    """#134: grab_frames resolves a clip by name but takes no recursive, so it is not offered.

    The bins here are the shadowed shape the flag exists for — a media tool would be told
    to pass recursive=false. This one cannot, and a fix naming an argument the tool does
    not take is the #122 defect over again.
    """
    holder = FakeMediaPoolItem(fixture_video.name, file_path=str(fixture_video))
    nested = FakeMediaPoolItem(fixture_video.name, file_path=str(fixture_video))
    attach(studio(pool=media_pool({"Angles": [holder], "Angles/Cam A": [nested]})))

    envelope = video_tools.grab_frames(fixture_video.name, [0], bin="Angles")

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "ambiguous_clip"
    assert 'bin="Angles/Cam A"' in envelope["error"]["fix"]
    assert "recursive" not in envelope["error"]["fix"]


# --- against real ffmpeg -----------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_real_ffmpeg_accepts_the_command_and_writes_a_capped_jpeg(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """The assertions a stand-in cannot make: the argv is one ffmpeg takes, and the cap holds."""
    source = _render_test_video(tmp_path / "media" / "C0012.mp4", width=1920, height=1080)
    attach(_studio_holding(source, {"FPS": "10", "Start": "0", "End": "19", "Frames": "20"}))

    envelope = video_tools.grab_frames("C0012.mp4", [10])

    assert envelope["ok"] is True, envelope.get("error")
    grabbed = envelope["frames"][0]
    assert Path(grabbed["path"]).exists()
    assert max(grabbed["width"], grabbed["height"]) <= DEFAULT_MAX_EDGE
    assert grabbed["width"] / grabbed["height"] == pytest.approx(16 / 9, abs=0.01)


# --- helpers -------------------------------------------------------------------------------------


def _studio_holding(source: Path, properties: dict[str, str] | None = None) -> FakeResolve:
    clip = FakeMediaPoolItem(
        source.name,
        file_path=str(source),
        properties={"Type": "Video", "FPS": str(CLIP_FPS), **(properties or {})},
    )
    return studio(pool=media_pool({"": [clip]}))


def _drawing(calls: list[Sequence[str]], width: int = 1568, height: int = 882) -> Runner:
    """Stand in for ffmpeg by writing the JPEG header it would have written."""

    def runner(argv: Sequence[str]) -> Completed:
        probed = hwaccel_probe_reply(argv)
        if probed is not None:
            return probed
        calls.append(list(argv))
        write_jpeg(Path(argv[-1]), width=width, height=height)
        return Completed(0, "")

    return runner


def _pretending(argv: Sequence[str]) -> Completed:
    """Exit zero and write nothing — the failure mode that would otherwise cache a lie."""
    return hwaccel_probe_reply(argv) or Completed(0, "")


def _seek(argv: Sequence[str]) -> float:
    return float(argv[argv.index("-ss") + 1])


def _render_test_video(target: Path, width: int, height: int) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={width}x{height}:rate=10:duration=2",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target
