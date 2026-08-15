"""Scene-cut detection: where the shots change on a piece of b-roll.

A full-file decode is a job, so this runs through the job runner and asserts on the record
the agent polls. The subprocess is substituted with one that replays the ``showinfo`` lines
ffmpeg prints for selected frames — parsing those, turning them into dual time and keeping
the inline answer small while the whole catalog goes to disk are the decisions here.

Real ffmpeg runs once, on a two-colour clip with exactly one cut in it, when the machine
has ffmpeg: only that proves the filter graph is one ffmpeg accepts and that a cut it finds
lands where the picture actually changed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from resolve_mcp.config import get_config
from resolve_mcp.errors import InvalidRequestError, SceneDetectionError
from resolve_mcp.ffmpeg import Completed, Runner
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools import video as video_tools
from resolve_mcp.video.scenes import (
    DEFAULT_THRESHOLD,
    INLINE_CUTS,
    KIND,
    detect_scene_cuts,
)

from .conftest import Attach
from .fakes import (
    FakeMediaPoolItem,
    FakeResolve,
    ffmpeg_absent,
    ffmpeg_refusing,
    hwaccel_probe_reply,
    media_pool,
    studio,
)

CLIP_FPS = 59.94
REFUSAL = "[mp4 @ 0] moov atom not found\nInvalid data found processing input"
CLIP_SECONDS = 10.0


@pytest.fixture
def fixture_video(tmp_path: Path) -> Path:
    """Stands in for b-roll on disk: the scan route only ever stats it."""
    target = tmp_path / "media" / "broll_pan.mp4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not really an mp4, but a file with a size and an mtime")
    return target


# --- the scan ------------------------------------------------------------------------------


def test_a_scan_writes_every_cut_to_disk_and_returns_a_gist(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    record = wait_for(
        detect_scene_cuts(
            get_connection(),
            "broll_pan.mp4",
            runner=_finding(calls, [1.0, 4.0, 7.5]),
        )["job_id"]
    )

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["cuts"] == 3
    assert record.result["threshold"] == DEFAULT_THRESHOLD
    assert Path(record.result["path"]).parent == get_config().analysis_dir
    assert calls[0][0] == "ffmpeg"
    assert "showinfo" in calls[0][calls[0].index("-filter:v") + 1]


def test_the_catalog_on_disk_carries_dual_time_and_the_run_that_made_it(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    record = wait_for(
        detect_scene_cuts(
            get_connection(),
            "broll_pan.mp4",
            runner=_finding([], [1.0, 4.0, 7.5]),
        )["job_id"]
    )
    assert record.result is not None

    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))

    assert catalog["clip"] == "broll_pan.mp4"
    assert catalog["generated_at"]
    assert catalog["threshold"] == DEFAULT_THRESHOLD
    assert catalog["cuts"][0] == {
        "frames": 59,
        "seconds": pytest.approx(0.984, abs=0.002),
        "timecode": "00:00:00:59",
        "fps": CLIP_FPS,
    }


def test_shots_are_the_spans_between_the_cuts_head_and_tail_included(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    record = wait_for(
        detect_scene_cuts(
            get_connection(),
            "broll_pan.mp4",
            runner=_finding([], [1.0, 4.0, 7.5]),
        )["job_id"]
    )
    assert record.result is not None

    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    shots = catalog["shots"]

    assert len(shots) == 4
    assert shots[0]["in"]["frames"] == 0
    assert shots[0]["duration_seconds"] == pytest.approx(1.0, abs=0.02)
    assert shots[-1]["out"]["frames"] == 600
    assert record.result["shot_seconds"]["min"] == pytest.approx(0.984, abs=0.01)
    assert record.result["shot_seconds"]["max"] == pytest.approx(3.503, abs=0.01)


def test_the_gist_stays_small_however_many_cuts_the_scan_found(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """A minute of fast b-roll has hundreds of cuts; the agent reads the file for the rest."""
    many = [round(0.5 + one * 0.02, 3) for one in range(400)]
    attach(_studio_holding(fixture_video))

    record = wait_for(
        detect_scene_cuts(get_connection(), "broll_pan.mp4", runner=_finding([], many))["job_id"]
    )

    assert record.result is not None
    assert record.result["cuts"] == len(many)
    assert len(record.result["first_cuts"]) == INLINE_CUTS
    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    assert len(catalog["cuts"]) == len(many)


def test_a_clip_with_no_cut_in_it_is_a_result_not_a_failure(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(
        detect_scene_cuts(get_connection(), "broll_pan.mp4", runner=_finding([], []))["job_id"]
    )

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["cuts"] == 0
    assert record.result["first_cuts"] == []
    assert record.result["shot_seconds"]["max"] == pytest.approx(CLIP_SECONDS, abs=0.02)


def test_a_clip_resolve_reports_no_end_for_still_catalogs_the_shots_it_can(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """The tail runs to an out point nothing knows; every shot before it is still real.

    Duration is blanked with End/Frames: a reported Duration now stands in for a missing
    out point (#46), and this test is about the case where nothing at all reports one.
    """
    attach(_studio_holding(fixture_video, {"End": "", "Frames": "", "Duration": ""}))

    record = wait_for(
        detect_scene_cuts(get_connection(), "broll_pan.mp4", runner=_finding([], [1.0, 4.0]))[
            "job_id"
        ]
    )

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["cuts"] == 2
    assert record.result["shots"] == 2
    assert record.result["shot_seconds"]["min"] == pytest.approx(0.984, abs=0.01)
    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    assert catalog["shots"][-1]["out"]["frames"] == 239
    assert catalog["bounds"]["out"] is None, "the missing tail has to be readable as missing"


# --- the cache -----------------------------------------------------------------------------


def test_an_unchanged_clip_is_never_scanned_twice(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    wait_for(
        detect_scene_cuts(get_connection(), "broll_pan.mp4", runner=_finding(calls, [1.0]))[
            "job_id"
        ]
    )

    again = detect_scene_cuts(get_connection(), "broll_pan.mp4", runner=_finding(calls, [1.0]))

    assert again["cached"] is True
    assert len(calls) == 1


def test_a_different_threshold_is_a_different_scan(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    wait_for(
        detect_scene_cuts(get_connection(), "broll_pan.mp4", runner=_finding(calls, [1.0]))[
            "job_id"
        ]
    )

    again = detect_scene_cuts(
        get_connection(),
        "broll_pan.mp4",
        threshold=0.2,
        runner=_finding(calls, [1.0]),
    )
    wait_for(again["job_id"])

    assert again["cached"] is False
    assert len(calls) == 2
    assert "0.2" in calls[1][calls[1].index("-filter:v") + 1]


# --- refusals ------------------------------------------------------------------------------


def test_a_threshold_outside_the_sensitivity_range_is_refused(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(InvalidRequestError) as raised:
        detect_scene_cuts(get_connection(), "broll_pan.mp4", threshold=4.0)

    assert "between" in raised.value.fix


def test_a_clip_whose_media_is_gone_says_so_before_starting_a_job(attach: Attach) -> None:
    attach(_studio_holding(Path("D:/gone/broll_pan.mp4")))

    with pytest.raises(SceneDetectionError) as raised:
        detect_scene_cuts(get_connection(), "broll_pan.mp4")

    assert "relink_media" in raised.value.fix


def test_a_refused_scan_fails_the_job_and_carries_ffmpegs_message(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(
        detect_scene_cuts(get_connection(), "broll_pan.mp4", runner=ffmpeg_refusing(REFUSAL))[
            "job_id"
        ]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "scene_detection_failed"
    assert "Invalid data found" in record.error["detail"]["stderr"]


def test_no_ffmpeg_on_the_machine_fails_the_job_by_name(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(
        detect_scene_cuts(get_connection(), "broll_pan.mp4", runner=ffmpeg_absent)["job_id"]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "ffmpeg_unavailable"


# --- the tool ---------------------------------------------------------------------------------


def test_the_tool_shapes_a_refusal_rather_than_raising(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))

    envelope = video_tools.detect_scene_cuts("broll_pan.mp4", threshold=4.0)

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"
    assert "context" in envelope


def test_the_tool_hands_the_job_back_in_the_one_envelope_shape(
    attach: Attach, fixture_video: Path
) -> None:
    """The same shape the analysis and render starters reply with (#219) — record under job."""
    attach(_studio_holding(fixture_video))

    envelope = video_tools.detect_scene_cuts("broll_pan.mp4")

    assert envelope["ok"] is True, envelope.get("error")
    assert envelope["job"]["kind"] == KIND
    assert envelope["job"]["job_id"].startswith(KIND)
    assert "job_id" not in envelope


# --- against real ffmpeg -----------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_real_ffmpeg_finds_the_one_cut_in_a_two_shot_clip(attach: Attach, tmp_path: Path) -> None:
    """The assertion a stand-in cannot make: the filter graph runs and the cut is where it is."""
    source = _render_two_shot_video(tmp_path / "media" / "broll_pan.mp4")
    attach(_studio_holding(source, {"FPS": "10", "Start": "0", "End": "19", "Frames": "20"}))

    envelope = video_tools.detect_scene_cuts("broll_pan.mp4")
    record = wait_for(envelope["job"]["job_id"])

    assert envelope["ok"] is True, envelope.get("error")
    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["cuts"] == 1
    assert record.result["first_cuts"][0]["seconds"] == pytest.approx(1.0, abs=0.15)


# --- helpers -------------------------------------------------------------------------------------


def _studio_holding(source: Path, properties: dict[str, str] | None = None) -> FakeResolve:
    clip = FakeMediaPoolItem(
        source.name,
        file_path=str(source),
        properties={
            "Type": "Video",
            "FPS": str(CLIP_FPS),
            "Start": "0",
            "End": "599",
            "Frames": "600",
            **(properties or {}),
        },
    )
    return studio(pool=media_pool({"": [clip]}))


def _finding(calls: list[Sequence[str]], seconds: Sequence[float]) -> Runner:
    """Replay the showinfo lines ffmpeg prints for the frames the select filter kept."""

    def runner(argv: Sequence[str]) -> Completed:
        probed = hwaccel_probe_reply(argv)
        if probed is not None:
            return probed
        calls.append(list(argv))
        lines = [
            f"[Parsed_showinfo_1 @ 000001] n:{index:4d} pts:{int(one * 1000):8d} "
            f"pts_time:{one} duration_time:0.016 fmt:yuv420p"
            for index, one in enumerate(seconds)
        ]
        return Completed(0, "\n".join(["frame= 600 fps=0.0 q=-0.0", *lines]))

    return runner


def _render_two_shot_video(target: Path) -> Path:
    """One second of red, one second of blue: exactly one place the picture changes."""
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
            "color=c=red:size=320x240:rate=10:duration=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=320x240:rate=10:duration=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    return target
