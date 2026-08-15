"""Image quality: whether the picture a camera got is worth cutting to.

The same two tiers ``test_occlusion.py`` keeps apart, for the same reason. ``picture`` is pure
— grey bytes in, readings out — so it is exercised on frames this file composes, with focus,
exposure, clipping and movement dialled in one at a time. The job around it — the range, the
sampled decode, the windows, the cache — runs through the job runner with the subprocess
substituted, the seam every ffmpeg route is tested at.

The discriminations that matter here are the ones a single number would get wrong: a pan is
not a wobble, a cut is not a wobble, a black hold is not a wobble, and a busy frame is not a
sharp one. Each has a test, because each is a shot the measurement would otherwise veto for
the wrong reason.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from resolve_mcp.errors import InvalidRequestError, QualityScanError
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools import video as video_tools
from resolve_mcp.video import picture
from resolve_mcp.video.quality import (
    DEFAULT_MAX_CLIPPED,
    DEFAULT_MIN_SHARPNESS,
    DEFAULT_MIN_STABILITY,
    INLINE_WINDOWS,
    analyze_quality,
)

from .conftest import Attach
from .fakes import (
    FakeMediaPoolItem,
    FakeResolve,
    ffmpeg_absent,
    ffmpeg_refusing,
    ffmpeg_sampling,
    ffmpeg_writing_nothing,
    media_pool,
    picture_frame,
    studio,
)

CLIP_FPS = 25.0
CLIP_FRAMES = 600
REFUSAL = "[mp4 @ 0] moov atom not found\nInvalid data found processing input"

SHARP = picture_frame()
SOFT = picture_frame(blur=5)
"""A take in focus and the same take missed by a few pixels — the whole sharpness question."""


@pytest.fixture
def fixture_video(tmp_path: Path) -> Path:
    """Stands in for an angle on disk: the scan route only ever stats it."""
    target = tmp_path / "media" / "20260617_D_FX6_0004.MP4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not really an mp4, but a file with a size and an mtime")
    return target


# --- the arithmetic --------------------------------------------------------------------------


def test_a_soft_take_reads_softer_than_a_sharp_one() -> None:
    """The measurement the ticket exists for: a focus miss, by number rather than by eye."""
    sharp, soft = _looked(SHARP), _looked(SOFT)

    assert sharp.sharpness > soft.sharpness
    assert sharp.sharpness >= DEFAULT_MIN_SHARPNESS
    assert soft.sharpness < DEFAULT_MIN_SHARPNESS


def test_sharpness_is_not_just_how_busy_the_frame_is() -> None:
    """A low-contrast frame in focus beats a high-contrast one that is out of it.

    Gradient energy alone would rank these the other way round, which is the failure mode
    that makes a plain sharpness metric useless on concert footage: a crowd shot is busy and
    a lit soloist against black is not.
    """
    quiet = _looked(picture_frame(swing=0.06))
    loud_but_soft = _looked(picture_frame(blur=5, swing=0.40))

    assert quiet.sharpness > loud_but_soft.sharpness


def test_exposure_reads_the_level_the_take_was_shot_at() -> None:
    dark = _looked(picture_frame(level=0.12))
    bright = _looked(picture_frame(level=0.80))

    assert dark.exposure == pytest.approx(0.12, abs=0.03)
    assert bright.exposure == pytest.approx(0.80, abs=0.03)


def test_a_blown_out_frame_reports_the_fraction_that_is_gone() -> None:
    burned = _looked(picture_frame(clipped=0.25))

    assert burned.clipped == pytest.approx(0.25, abs=0.02)
    assert burned.clipped > DEFAULT_MAX_CLIPPED


def test_an_ordinary_take_clips_nothing() -> None:
    assert _looked(SHARP).clipped == 0.0


def test_a_locked_off_camera_is_as_stable_as_a_shot_gets() -> None:
    scan = _scored([SHARP] * 4)

    assert [one.stability for one in scan.readings[1:]] == [1.0, 1.0, 1.0]
    assert scan.readings[0].stability is None


def test_a_steady_pan_is_a_shot_not_a_wobble() -> None:
    """The distinction the whole metric turns on: movement is not instability."""
    panning = [picture_frame(shift=step * 3) for step in range(6)]

    scan = _scored(panning)

    assert all(one.stability == 1.0 for one in scan.readings[1:])


def test_handheld_wobble_is_what_the_score_is_for() -> None:
    wobbling = [picture_frame(shift=6 if step % 2 else 0) for step in range(6)]

    scan = _scored(wobbling)

    scores = [one.stability for one in scan.readings[1:]]
    assert all(one is not None and one < DEFAULT_MIN_STABILITY for one in scores)


def test_a_cut_is_not_a_wobble() -> None:
    """Two frames of different pictures are unmeasurable, not unstable.

    A rendered cut is full of these. Scoring the first frame after every cut as maximally
    shaky would veto the whole edit, and it would be wrong about every one of them.
    """
    different = picture_frame(seed=99)

    scan = _scored([SHARP, SHARP, different, different])

    assert scan.readings[2].stability is None
    assert scan.readings[2].discontinuity is True
    assert scan.readings[3].stability == 1.0


def test_a_black_hold_has_no_stability_to_report() -> None:
    black = bytes(picture.GRID_WIDTH * picture.GRID_HEIGHT)

    scan = _scored([black, black, black])

    assert [one.stability for one in scan.readings] == [None, None, None]
    assert all(one.discontinuity for one in scan.readings[1:])


def test_a_partial_frame_fails_the_read_rather_than_being_dropped() -> None:
    with pytest.raises(QualityScanError) as raised:
        picture.read_grid(SHARP + SHARP[:100])

    assert raised.value.detail["remainder"] == 100


def test_a_summary_takes_the_worst_of_what_vetoes_and_the_middle_of_what_describes() -> None:
    readings = _scored([SHARP, SHARP, picture_frame(clipped=0.3), SHARP]).readings

    summary = picture.summarize(list(readings))

    assert summary["samples"] == 4
    assert summary["clipped"] == pytest.approx(0.3, abs=0.02)
    assert summary["exposure"] == pytest.approx(_looked(SHARP).exposure, abs=0.05)


def test_a_stretch_with_nothing_in_it_says_so() -> None:
    assert picture.summarize([]) == {"samples": 0}


def test_the_floors_name_what_a_sample_missed() -> None:
    floors = picture.Floors(sharpness=0.5, clipped=0.02, stability=0.6)
    soft_and_blown = _scored([picture_frame(blur=5, clipped=0.2)] * 2).readings[1]

    assert picture.failures(soft_and_blown, floors) == ("soft", "clipped")


def test_an_unmeasurable_stability_is_not_a_failure() -> None:
    """A black frame is many things; a shaky camera is not one of them."""
    floors = picture.Floors(sharpness=0.0, clipped=1.0, stability=0.9)
    black = _scored([bytes(picture.GRID_WIDTH * picture.GRID_HEIGHT)] * 2).readings[1]

    assert picture.failures(black, floors) == ()


# --- the job ---------------------------------------------------------------------------------


def test_the_scan_reports_a_window_where_the_picture_goes_soft(
    attach: Attach, fixture_video: Path
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], _sequence(4, 4, 4))["job_id"])

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["samples"] == 12
    assert record.result["windows"] == 1
    window = record.result["worst_windows"][0]
    assert window["reasons"] == ["soft"]


def test_a_scan_of_footage_that_holds_up_reports_no_window(
    attach: Attach, fixture_video: Path
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], [SHARP] * 8)["job_id"])

    assert record.result is not None
    assert record.result["windows"] == 0
    assert record.result["unusable_samples"] == 0
    assert record.result["quality"]["sharpness"] >= DEFAULT_MIN_SHARPNESS


def test_the_curve_goes_to_disk_with_a_time_on_every_sample(
    attach: Attach, fixture_video: Path
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], _sequence(2, 2, 2))["job_id"])

    assert record.result is not None
    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    assert len(catalog["samples"]) == 6
    assert catalog["samples"][0]["t"] == 0.0
    assert catalog["samples"][1]["t"] == pytest.approx(1.0 / catalog["sample_fps"], abs=0.05)
    assert catalog["grid"] == {"width": picture.GRID_WIDTH, "height": picture.GRID_HEIGHT}
    assert catalog["floors"]["min_sharpness"] == DEFAULT_MIN_SHARPNESS


def test_the_scan_asks_ffmpeg_for_the_grid_the_arithmetic_reads(
    attach: Attach, fixture_video: Path
) -> None:
    calls: list[Sequence[str]] = []
    attach(_studio_holding(fixture_video))

    wait_for(_scan(calls, [SHARP] * 4)["job_id"])

    sampled = [one for one in calls if "-f" in one]
    assert f"scale={picture.GRID_WIDTH}:{picture.GRID_HEIGHT}" in " ".join(sampled[0])


def test_a_second_scan_of_the_same_range_is_the_same_job(
    attach: Attach, fixture_video: Path
) -> None:
    calls: list[Sequence[str]] = []
    attach(_studio_holding(fixture_video))

    first = wait_for(_scan(calls, [SHARP] * 4)["job_id"])
    second = wait_for(_scan(calls, [SHARP] * 4)["job_id"])

    assert first.result is not None
    assert second.result is not None
    assert second.result["path"] == first.result["path"]
    assert len([one for one in calls if "-f" in one]) == 1


def test_a_range_outside_the_clip_is_refused_before_the_job_exists(
    attach: Attach, fixture_video: Path
) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(InvalidRequestError):
        _scan([], [SHARP], start=0, end=CLIP_FRAMES * 4)


def test_a_sampling_rate_this_scan_does_not_run_at_is_refused(
    attach: Attach, fixture_video: Path
) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(InvalidRequestError) as raised:
        _scan([], [SHARP], sample_fps=90.0)

    assert raised.value.detail["requested"] == 90.0


def test_a_floor_switched_off_is_a_rule_nobody_is_enforcing(
    attach: Attach, fixture_video: Path
) -> None:
    """A style rule that cares only about clipping sets the other floors to zero.

    The severity a window is ranked by is a distance from each floor, and a floor of zero has
    no distance from it — the arithmetic would divide by it and end the scan on its first
    sample rather than answering the question that was asked.
    """
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], _sequence(2, 2, 2), min_sharpness=0.0, min_stability=0.0)["job_id"])

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["windows"] == 0
    assert record.result["unusable_samples"] == 0


def test_a_decode_that_wrote_nothing_is_a_failure_not_a_clean_scan(
    attach: Attach, fixture_video: Path
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(
        analyze_quality(
            get_connection(), fixture_video.name, runner=ffmpeg_writing_nothing([])
        )["job_id"]
    )

    assert record.state == "failed"


def test_ffmpeg_refusing_the_clip_comes_back_as_a_scan_failure(
    attach: Attach, fixture_video: Path
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(
        analyze_quality(get_connection(), fixture_video.name, runner=ffmpeg_refusing(REFUSAL))[
            "job_id"
        ]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "quality_scan_failed"


def test_a_machine_with_no_ffmpeg_says_so(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(
        analyze_quality(get_connection(), fixture_video.name, runner=ffmpeg_absent)["job_id"]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "ffmpeg_unavailable"


def test_only_the_worst_windows_come_back_inline(attach: Attach, fixture_video: Path) -> None:
    alternating: list[bytes] = []
    for _ in range(INLINE_WINDOWS + 3):
        alternating += [SHARP, SHARP, SOFT, SOFT]
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], alternating)["job_id"])

    assert record.result is not None
    assert record.result["windows"] > INLINE_WINDOWS
    assert len(record.result["worst_windows"]) == INLINE_WINDOWS


# --- the tool --------------------------------------------------------------------------------


def test_the_tool_shapes_a_refusal_rather_than_raising(
    attach: Attach, fixture_video: Path
) -> None:
    attach(_studio_holding(fixture_video))

    envelope = video_tools.analyze_quality(fixture_video.name, sample_fps=90.0)

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"


def test_the_tool_is_registered_with_the_other_video_routes() -> None:
    assert video_tools.analyze_quality in video_tools.TOOLS


# --- against real ffmpeg ---------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_real_ffmpeg_samples_a_clip_that_goes_out_of_focus(attach: Attach, tmp_path: Path) -> None:
    """The assertion no stand-in can make: ffmpeg accepts the command and the grid reshapes."""
    source = _render_softening_video(tmp_path / "media" / "focus.mp4")
    attach(_studio_holding(source, {"FPS": "10", "Start": "0", "End": "59", "Frames": "60"}))

    envelope = video_tools.analyze_quality(source.name)
    record = wait_for(envelope["job_id"])

    assert envelope["ok"] is True, envelope.get("error")
    assert record.state == "completed", record.error
    assert record.result is not None
    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    scores = [one["sharpness"] for one in catalog["samples"]]
    assert len(scores) >= 5
    assert scores[0] > scores[-1]


# --- helpers ---------------------------------------------------------------------------------


def _looked(frame: bytes) -> picture.Frame:
    grid = picture.read_grid(frame)
    return picture.look(np.asarray(grid[0], dtype=np.float64) / 255.0)


def _scored(frames: list[bytes]) -> picture.Scan:
    """Score composed frames the way the worker does: through the grid reader."""
    return picture.measure(picture.read_grid(b"".join(frames)))


def _sequence(before: int, soft: int, after: int) -> list[bytes]:
    """Sharp footage, a stretch the focus slips on, then sharp again."""
    return [SHARP] * before + [SOFT] * soft + [SHARP] * after


def _scan(calls: list[Sequence[str]], frames: list[bytes], **kwargs: Any) -> dict[str, Any]:
    return analyze_quality(
        get_connection(),
        "20260617_D_FX6_0004.MP4",
        runner=ffmpeg_sampling(calls, frames),
        **kwargs,
    )


def _studio_holding(source: Path, properties: dict[str, str] | None = None) -> FakeResolve:
    clip = FakeMediaPoolItem(
        source.name,
        file_path=str(source),
        properties={
            "Type": "Video",
            "FPS": str(CLIP_FPS),
            "Start": "0",
            "End": str(CLIP_FRAMES - 1),
            "Frames": str(CLIP_FRAMES),
            **(properties or {}),
        },
    )
    return studio(pool=media_pool({"": [clip]}))


def _render_softening_video(target: Path) -> Path:
    """Three seconds of detailed picture, then three of the same picture thrown out of focus."""
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
            "testsrc2=size=320x240:rate=10:duration=3",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=10:duration=3",
            "-filter_complex",
            "[1:v]boxblur=6:2[soft];[0:v][soft]concat=n=2:v=1:a=0[v]",
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
