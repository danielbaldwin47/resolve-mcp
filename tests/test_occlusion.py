"""Occlusion scanning: whether something in the near field is standing in front of the stage.

Two tiers of decision live here and they are tested apart. The arithmetic in ``blocking`` is
pure — grey bytes in, scores out — so it is exercised on frames this file composes from
plain bytes, never from the scorer's own idea of a blocker: a fixture that borrowed the
measurement could only ever agree with it. The job around it — the range, the sampled decode,
the windows, the cache — runs through the job runner with the subprocess substituted, which
is the same seam every other ffmpeg route is tested at.

The one thing no stand-in can prove is that ffmpeg accepts the sampling command and that the
raw grey it writes reshapes into the grid the scorer expects, so real ffmpeg renders a clip
whose bottom half goes black halfway through, and the scan has to find it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from resolve_mcp.config import get_config
from resolve_mcp.errors import InvalidRequestError, OcclusionScanError
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools import video as video_tools
from resolve_mcp.video import blocking
from resolve_mcp.video.occlusion import (
    DEFAULT_THRESHOLD,
    INLINE_WINDOWS,
    analyze_occlusion,
)

from .conftest import Attach
from .fakes import (
    FakeMediaPoolItem,
    FakeResolve,
    ffmpeg_absent,
    ffmpeg_refusing,
    ffmpeg_sampling,
    ffmpeg_writing_nothing,
    gray_frame,
    media_pool,
    studio,
)

CLIP_FPS = 25.0
"""Round on purpose: a sample a second is a whole number of frames, so a window's frame
numbers can be read against the samples that produced them."""

CLIP_FRAMES = 600
REFUSAL = "[mp4 @ 0] moov atom not found\nInvalid data found processing input"

HEAD = 0.25
"""Frame fraction of a blocker that is unmistakable — a head and shoulders at the barrier."""

PARTIAL = 0.12
"""Enough to score, not enough to survive a raised threshold."""

CLEAN = gray_frame()
BLOCKED = gray_frame(HEAD)

EVIDENCE = Path(__file__).parent / "data" / "occlusion"
"""The gauntlet's G11 evidence set, frozen as the 128x72 grey the detector actually reads.

Six 90 s scans — the three adjudicated Taurus pieces on both angles, a sample a second — cut
from the same media by ``gauntlet/recon/occl_fixture_grids.py``, which re-scores each one
against the catalog the original run left on disk. Real frames rather than drawn ones because
every false positive in the ledgers *is* a large dark bottom-anchored blob: a fixture built by
drawing one could only agree with the detector it was drawn to satisfy.
"""

ADJUDICATED: dict[str, list[tuple[tuple[int, int], str]]] = {
    # gauntlet/recon/occlusion_verdict_r3.json — an audience head over the pianist, then five
    # windows of the same static bed: a black piano lid and a head parked in the far corner.
    "opening-fx6": [
        ((12, 19), blocking.OBSTRUCTION),
        ((43, 50), blocking.SCENE),
        ((53, 54), blocking.SCENE),
        ((56, 61), blocking.SCENE),
        ((67, 90), blocking.SCENE),
    ],
    "opening-a7iv": [((42, 43), blocking.OBSTRUCTION)],
    # gauntlet/recon/occlusion_mid.json — the sax player crossing the near field, and the
    # drummer's own arm twice. The crossing scored 0.416, the drummer 0.472 and 0.469.
    "mid-a7iv": [
        ((64, 65), blocking.OBSTRUCTION),
        ((78, 79), blocking.SCENE),
        ((87, 88), blocking.SCENE),
    ],
    # The mid-take reframe: 42 s of flags with nothing ever in the way.
    "mid-fx6": [((0, 42), blocking.SCENE)],
    # gauntlet/recon/occlusion_ending.json — four flags, all of them the drummer.
    "ending-a7iv": [
        ((5, 6), blocking.SCENE),
        ((8, 9), blocking.SCENE),
        ((16, 17), blocking.SCENE),
        ((40, 41), blocking.SCENE),
    ],
    # Adjudicated clean end to end: nothing flagged, so nothing to class.
    "ending-fx6": [],
}
"""Every window the scan flagged inside an adjudicated piece, as half-open sample indices, and
what the eye said about it. Sample ``i`` is ``i`` seconds into the piece."""


def _adjudicated(kind: str) -> list[tuple[str, tuple[int, int]]]:
    """Every window the gauntlet judged ``kind``, as ``(scan, window)`` pairs to parametrise."""
    return [
        (scan, window)
        for scan, windows in ADJUDICATED.items()
        for window, verdict in windows
        if verdict == kind
    ]


@cache
def _measured(scan: str) -> blocking.Scan:
    """One evidence scan, scored — cached, because a dozen tests read the same six."""
    with np.load(EVIDENCE / f"{scan}.npz") as loaded:
        return blocking.measure(loaded["frames"])


def _peaks(scan: str, window: tuple[int, int]) -> tuple[float, float]:
    """``(novel, hidden)`` at their worst across a window, which is how a window is classed."""
    readings = _measured(scan).readings[window[0] : window[1]]
    return max(one.novel for one in readings), max(one.hidden for one in readings)


@pytest.fixture
def fixture_video(tmp_path: Path) -> Path:
    """Stands in for an angle on disk: the scan route only ever stats it."""
    target = tmp_path / "media" / "20260617_D_A7IV_0006.MP4"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not really an mp4, but a file with a size and an mtime")
    return target


# --- the arithmetic --------------------------------------------------------------------------


def test_a_clear_view_of_the_stage_scores_nothing() -> None:
    scan = _scored(CLEAN, CLEAN, CLEAN)

    assert [one.score for one in scan.readings] == [0.0, 0.0, 0.0]
    assert [one.blobs for one in scan.readings] == [0, 0, 0]


def test_a_head_at_the_bottom_of_frame_is_what_the_scan_is_for() -> None:
    scan = _scored(CLEAN, CLEAN, BLOCKED, BLOCKED)

    blocked = scan.readings[2]
    assert blocked.score >= DEFAULT_THRESHOLD
    assert blocked.blobs == 1
    assert blocked.largest == pytest.approx(HEAD, abs=0.02)
    assert scan.readings[0].score == 0.0


def test_the_ceiling_of_the_room_is_not_an_obstruction() -> None:
    """A black ceiling touches the top edge of every frame shot in a basement club."""
    ceiling = gray_frame(HEAD, anchor="top")

    scan = _scored(CLEAN, CLEAN, ceiling, ceiling)

    assert [one.score for one in scan.readings] == [0.0, 0.0, 0.0, 0.0]


def test_something_in_the_picture_rather_than_in_front_of_it_is_not_an_obstruction() -> None:
    """A dark shape that touches no edge is a speaker stack on stage, not a body at the lens."""
    onstage = gray_frame(HEAD, anchor="float")

    scan = _scored(CLEAN, onstage, onstage)

    assert [one.score for one in scan.readings] == [0.0, 0.0, 0.0]


def test_speckle_is_not_a_body() -> None:
    """Grain and shadowed detail are dark too; a blocker is a region."""
    scan = _scored(CLEAN, gray_frame(0.005), gray_frame(0.005))

    assert [one.score for one in scan.readings] == [0.0, 0.0, 0.0]


def test_a_dark_corner_the_shot_always_has_is_scene_not_obstruction() -> None:
    """A locked-off camera with a dark table in the bottom corner is not blocked all night."""
    corner = gray_frame(0.03)

    scan = _scored(corner, corner, corner, corner)

    assert scan.baseline > 0.0
    assert [one.score for one in scan.readings] == [0.0, 0.0, 0.0, 0.0]


def test_a_range_blocked_end_to_end_still_reads_as_blocked() -> None:
    """The baseline is capped, so 'all of it is blocked' survives as an answer."""
    wall = gray_frame(0.3)

    scan = _scored(wall, wall, wall, wall)

    assert scan.baseline == blocking.BASELINE_CAP
    assert min(one.score for one in scan.readings) >= DEFAULT_THRESHOLD


def test_an_obstruction_that_wipes_in_scores_above_one_that_was_already_there() -> None:
    """The wipe is what ruins a cut: the shot was fine, then a head crossed it."""
    partial = gray_frame(PARTIAL)

    scan = _scored(CLEAN, CLEAN, partial, partial)

    arriving, holding = scan.readings[2], scan.readings[3]
    assert arriving.coverage == holding.coverage
    assert arriving.score > holding.score


def test_a_wipe_can_never_lift_a_clear_frame() -> None:
    """A rise from nothing to nothing is still nothing — the bonus is multiplicative on blocked."""
    scan = _scored(CLEAN, gray_frame(0.005), CLEAN)

    assert max(one.score for one in scan.readings) == 0.0


def test_half_a_frame_is_not_a_sample_and_not_a_verdict_either() -> None:
    """A partial tail is a refusal: reading the frames that survived answers for a range that
    was never decoded, and the answer it gives is that the shot is clear."""
    grid = blocking.GRID_WIDTH * blocking.GRID_HEIGHT

    with pytest.raises(OcclusionScanError) as raised:
        blocking.read_grid(CLEAN + BLOCKED[: grid // 2])

    assert raised.value.detail["remainder"] == grid // 2


def test_a_whole_number_of_frames_reshapes_onto_the_grid() -> None:
    frames = blocking.read_grid(CLEAN + BLOCKED)

    assert frames.shape == (2, blocking.GRID_HEIGHT, blocking.GRID_WIDTH)


# --- the scan --------------------------------------------------------------------------------


def test_a_scan_writes_the_curve_to_disk_and_returns_the_windows(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    record = wait_for(_scan(calls, _run(5, 4, 5))["job_id"])

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["samples"] == 14
    assert record.result["blocked_samples"] == 4
    assert record.result["windows"] == 1
    assert record.result["threshold"] == DEFAULT_THRESHOLD
    assert Path(record.result["path"]).parent == get_config().analysis_dir


def test_a_window_is_the_stretch_to_keep_a_cut_out_of(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """Five clean seconds, four blocked: the window is those four, in the clip's own frames."""
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], _run(5, 4, 5))["job_id"])

    assert record.result is not None
    window = record.result["worst_windows"][0]
    assert window["in"]["frames"] == 125
    assert window["out"]["frames"] == 225
    assert window["duration_seconds"] == pytest.approx(4.0, abs=0.01)
    assert window["samples"] == 4
    assert window["peak_score"] >= DEFAULT_THRESHOLD


def test_the_catalog_carries_every_sample_and_the_run_that_made_it(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], _run(5, 4, 5))["job_id"])

    assert record.result is not None
    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    assert catalog["clip"] == fixture_video.name
    assert catalog["generated_at"]
    assert catalog["grid"] == {"width": blocking.GRID_WIDTH, "height": blocking.GRID_HEIGHT}
    assert len(catalog["samples"]) == 14
    assert catalog["samples"][0]["time"] == {
        "frames": 0,
        "seconds": 0.0,
        "timecode": "00:00:00:00",
        "fps": CLIP_FPS,
    }
    assert catalog["samples"][5]["time"]["frames"] == 125
    assert catalog["samples"][5]["score"] >= DEFAULT_THRESHOLD


def test_a_head_that_bobs_out_of_frame_for_a_beat_leaves_one_window(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    frames = [CLEAN, CLEAN, CLEAN, BLOCKED, CLEAN, BLOCKED, CLEAN, CLEAN, CLEAN]

    record = wait_for(_scan([], frames)["job_id"])

    assert record.result is not None
    assert record.result["blocked_samples"] == 2
    assert record.result["windows"] == 1
    assert record.result["worst_windows"][0]["samples"] == 3


def test_two_clean_seconds_between_blocks_are_two_windows(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    frames = [CLEAN, CLEAN, CLEAN, BLOCKED, CLEAN, CLEAN, BLOCKED, CLEAN, CLEAN]

    record = wait_for(_scan([], frames)["job_id"])

    assert record.result is not None
    assert record.result["windows"] == 2


def test_a_window_never_runs_past_the_range_that_was_scanned(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """The last sample stands for the second it was taken from, but the range is the range."""
    attach(_studio_holding(fixture_video))

    record = wait_for(
        analyze_occlusion(
            get_connection(),
            fixture_video.name,
            start=0,
            end=100,
            runner=ffmpeg_sampling([], [CLEAN, CLEAN, BLOCKED, BLOCKED]),
        )["job_id"]
    )

    assert record.result is not None
    assert record.result["worst_windows"][0]["out"]["frames"] == 100


def test_a_sample_on_the_end_of_the_range_publishes_no_window_at_all(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """Four seconds asked for, five samples back: the ``fps`` filter emits a frame on the
    boundary, and it is dated at the range's own end. It stays in the curve, but the window it
    would make starts where the range stops — nought frames long, and nothing to cut around."""
    attach(_studio_holding(fixture_video))

    record = wait_for(
        analyze_occlusion(
            get_connection(),
            fixture_video.name,
            start=0,
            end=100,
            runner=ffmpeg_sampling([], [CLEAN] * 4 + [BLOCKED]),
        )["job_id"]
    )

    assert record.result is not None
    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    assert catalog["samples"][4]["time"]["frames"] == 100
    assert record.result["blocked_samples"] == 1
    assert record.result["windows"] == 0
    assert all(one["duration_frames"] > 0 for one in catalog["windows"])


def test_raising_the_threshold_leaves_the_partial_blocks_alone(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    frames = [CLEAN, CLEAN, gray_frame(PARTIAL), gray_frame(PARTIAL), CLEAN]

    lenient = wait_for(_scan([], frames)["job_id"])
    strict = wait_for(_scan([], frames, threshold=0.9)["job_id"])

    assert lenient.result is not None
    assert strict.result is not None
    assert lenient.result["windows"] == 1
    assert strict.result["windows"] == 0
    assert strict.result["blocked_samples"] == 0


def test_the_gist_stays_small_however_many_windows_the_scan_found(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """An angle behind the crowd is blocked all night; the file on disk has the rest.

    A minute of media, because thirty samples a second apart need a minute to sit in: the
    stand-in writes whatever frames it is handed, and samples dated past the end of the range
    make no window at all.
    """
    attach(_studio_holding(fixture_video, {"End": "1499", "Frames": "1500"}))
    frames = [BLOCKED, CLEAN, CLEAN] * 10

    record = wait_for(_scan([], frames)["job_id"])

    assert record.result is not None
    assert record.result["windows"] == 10
    assert len(record.result["worst_windows"]) == INLINE_WINDOWS
    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    assert len(catalog["windows"]) == 10
    assert len(catalog["samples"]) == 30


def test_a_clear_angle_is_a_result_not_a_failure(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], [CLEAN] * 6)["job_id"])

    assert record.state == "completed", record.error
    assert record.result is not None
    assert record.result["windows"] == 0
    assert record.result["worst_windows"] == []
    assert record.result["blocked_fraction"] == 0.0
    assert record.result["score"]["max"] == 0.0


def test_the_scan_seeks_to_the_range_and_asks_ffmpeg_for_raw_grey(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """The range is the clip's own frame numbering; ffmpeg counts seconds from zero."""
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    wait_for(
        analyze_occlusion(
            get_connection(),
            fixture_video.name,
            start=250,
            end=500,
            runner=ffmpeg_sampling(calls, [CLEAN] * 10),
        )["job_id"]
    )

    argv = list(calls[0])
    grid = f"{blocking.GRID_WIDTH}:{blocking.GRID_HEIGHT}"
    assert argv[argv.index("-ss") + 1] == "10.000000"
    assert argv[argv.index("-t") + 1] == "10.000000"
    assert argv[argv.index("-vf") + 1] == f"fps=1,scale={grid}"
    assert argv[argv.index("-pix_fmt") + 1] == "gray"
    assert argv[argv.index("-f") + 1] == "rawvideo"


def test_the_raw_grey_is_scratch_and_does_not_survive_the_scan(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """Tens of megabytes of intermediate that nothing reads again is not an artifact."""
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], _run(2, 2, 2))["job_id"])

    assert record.result is not None
    assert list(get_config().analysis_dir.glob("*.gray")) == []
    assert Path(record.result["path"]).exists()


def test_two_scans_of_the_same_range_never_share_a_scratch_file(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """The clip, the range and the key are the same for both, so only the run can tell them
    apart: ffmpeg's ``-y`` would have one truncate the other's grey mid-read, and the scan
    that read the leftovers would come back saying the footage is clear."""
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []

    wait_for(_scan(calls, _run(2, 2, 2))["job_id"])
    wait_for(_scan(calls, _run(2, 2, 2), refresh=True)["job_id"])

    scratch = [Path(call[-1]) for call in calls]
    assert len(scratch) == 2
    assert scratch[0].suffix == ".gray"
    assert scratch[0] != scratch[1]


def test_a_grey_file_cut_short_fails_the_scan_rather_than_reading_clear(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """A decode killed mid-write, or a second scan writing over this one's scratch."""
    attach(_studio_holding(fixture_video))

    record = wait_for(_scan([], [CLEAN, CLEAN, BLOCKED[:1000]])["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "occlusion_scan_failed"
    assert list(get_config().analysis_dir.glob("*.gray")) == []


# --- the cache -------------------------------------------------------------------------------


def test_an_unchanged_angle_and_range_are_never_scanned_twice(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    wait_for(_scan(calls, _run(2, 2, 2))["job_id"])

    again = _scan(calls, _run(2, 2, 2))

    assert again["cached"] is True
    assert len(calls) == 1


def test_a_different_range_is_a_different_scan(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    wait_for(_scan(calls, _run(2, 2, 2))["job_id"])

    again = analyze_occlusion(
        get_connection(),
        fixture_video.name,
        start=250,
        end=500,
        runner=ffmpeg_sampling(calls, _run(2, 2, 2)),
    )
    wait_for(again["job_id"])

    assert again["cached"] is False
    assert len(calls) == 2


def test_a_different_sampling_rate_is_a_different_scan(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))
    calls: list[Sequence[str]] = []
    wait_for(_scan(calls, _run(2, 2, 2))["job_id"])

    again = _scan(calls, _run(2, 2, 2), sample_fps=2.0)
    wait_for(again["job_id"])

    assert again["cached"] is False
    assert "fps=2" in list(calls[1])[list(calls[1]).index("-vf") + 1]


# --- refusals --------------------------------------------------------------------------------


def test_a_sampling_rate_outside_the_range_the_scan_runs_at_is_refused(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(InvalidRequestError) as raised:
        analyze_occlusion(get_connection(), fixture_video.name, sample_fps=30.0)

    assert "samples a second" in raised.value.fix


def test_a_threshold_that_is_not_a_score_is_refused(attach: Attach, fixture_video: Path) -> None:
    attach(_studio_holding(fixture_video))

    with pytest.raises(InvalidRequestError) as raised:
        analyze_occlusion(get_connection(), fixture_video.name, threshold=1.5)

    assert "at most 1.0" in raised.value.fix


def test_a_range_outside_the_media_is_refused_before_a_job_starts(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """ffmpeg seeked past the end exits zero, so an out-of-bounds range must never reach it."""
    attach(_studio_holding(fixture_video))

    with pytest.raises(InvalidRequestError) as raised:
        analyze_occlusion(get_connection(), fixture_video.name, start=900, end=1000)

    assert raised.value.detail["bounds"]["out"]["frames"] == CLIP_FRAMES


def test_a_range_longer_than_a_survey_is_refused(attach: Attach, tmp_path: Path) -> None:
    long_clip = tmp_path / "media" / "wide_locked.mp4"
    long_clip.parent.mkdir(parents=True, exist_ok=True)
    long_clip.write_bytes(b"a long card")
    attach(_studio_holding(long_clip, {"End": "359999", "Frames": "360000"}))

    with pytest.raises(InvalidRequestError) as raised:
        analyze_occlusion(get_connection(), long_clip.name)

    assert "at most" in raised.value.fix


def test_an_angle_whose_media_is_gone_says_so_before_starting_a_job(attach: Attach) -> None:
    attach(_studio_holding(Path("D:/gone/20260617_D_A7IV_0006.MP4")))

    with pytest.raises(OcclusionScanError) as raised:
        analyze_occlusion(get_connection(), "20260617_D_A7IV_0006.MP4")

    assert "relink_media" in raised.value.fix


def test_a_refused_decode_fails_the_job_and_carries_ffmpegs_message(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(
        analyze_occlusion(
            get_connection(),
            fixture_video.name,
            runner=ffmpeg_refusing(REFUSAL),
        )["job_id"]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "occlusion_scan_failed"
    assert "Invalid data found" in record.error["detail"]["stderr"]


def test_a_decode_that_wrote_no_frames_is_a_failure_not_a_clear_angle(
    attach: Attach,
    fixture_video: Path,
) -> None:
    """The dangerous one: a silent no-op would come back saying every second is usable."""
    attach(_studio_holding(fixture_video))

    record = wait_for(
        analyze_occlusion(
            get_connection(),
            fixture_video.name,
            runner=ffmpeg_writing_nothing([]),
        )["job_id"]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "occlusion_scan_failed"
    assert "wrote no frames" in record.error["cause"]


def test_no_ffmpeg_on_the_machine_fails_the_job_by_name(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    record = wait_for(
        analyze_occlusion(get_connection(), fixture_video.name, runner=ffmpeg_absent)["job_id"]
    )

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "ffmpeg_unavailable"


# --- the tool --------------------------------------------------------------------------------


def test_the_tool_shapes_a_refusal_rather_than_raising(
    attach: Attach,
    fixture_video: Path,
) -> None:
    attach(_studio_holding(fixture_video))

    envelope = video_tools.analyze_occlusion(fixture_video.name, sample_fps=30.0)

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"
    assert "context" in envelope


def test_the_tool_is_registered_with_the_other_video_routes() -> None:
    assert video_tools.analyze_occlusion in video_tools.TOOLS


# --- against real ffmpeg ---------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on PATH")
def test_real_ffmpeg_samples_a_clip_whose_lower_half_goes_black(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """The assertion no stand-in can make: ffmpeg accepts the command and the grid reshapes."""
    source = _render_blocked_video(tmp_path / "media" / "wipe.mp4")
    attach(_studio_holding(source, {"FPS": "10", "Start": "0", "End": "59", "Frames": "60"}))

    envelope = video_tools.analyze_occlusion(source.name)
    record = wait_for(envelope["job_id"])

    assert envelope["ok"] is True, envelope.get("error")
    assert record.state == "completed", record.error
    assert record.result is not None
    catalog = json.loads(Path(record.result["path"]).read_text(encoding="utf-8"))
    scores = [one["score"] for one in catalog["samples"]]
    assert len(scores) >= 5
    assert scores[0] == 0.0
    assert scores[-1] >= DEFAULT_THRESHOLD
    assert record.result["windows"] == 1


# --- the discriminator, on the frames that produced the verdicts -----------------------------


def test_a_shape_the_shot_has_all_night_is_scene_however_hard_it_scores() -> None:
    """A wall of near-field mass scores 1.0 and is still not something to keep a cut out of."""
    wall = gray_frame(0.3)

    scan = _scored(wall, wall, wall, wall)

    assert min(one.score for one in scan.readings) >= DEFAULT_THRESHOLD
    assert [one.novel for one in scan.readings] == [0.0, 0.0, 0.0, 0.0]
    assert [one.hidden for one in scan.readings] == [0.0, 0.0, 0.0, 0.0]
    assert _classes(scan) == [blocking.SCENE] * 4


def test_a_body_crossing_a_shot_that_is_usually_clear_is_an_obstruction() -> None:
    crossing = _scored(*([CLEAN] * 8 + [BLOCKED] * 2))

    arriving = crossing.readings[8]
    assert arriving.novel >= blocking.NOVEL_AREA
    assert arriving.hidden >= blocking.HIDDEN_AREA
    assert blocking.verdict(arriving.novel, arriving.hidden) == blocking.OBSTRUCTION
    assert _classes(crossing)[:8] == [blocking.SCENE] * 8


@pytest.mark.parametrize(("scan", "window"), _adjudicated(blocking.OBSTRUCTION))
def test_a_true_blocking_in_the_evidence_set_reads_as_an_obstruction(
    scan: str, window: tuple[int, int]
) -> None:
    """The three the gauntlet confirmed by eye: an audience head, a hat and a player crossing."""
    novel, hidden = _peaks(scan, window)

    assert blocking.verdict(novel, hidden) == blocking.OBSTRUCTION


@pytest.mark.parametrize(("scan", "window"), _adjudicated(blocking.SCENE))
def test_a_false_positive_in_the_evidence_set_reads_as_scene(
    scan: str, window: tuple[int, int]
) -> None:
    """The eleven the gauntlet overrode: a piano lid, a parked head, a drummer, a reframe."""
    novel, hidden = _peaks(scan, window)

    assert blocking.verdict(novel, hidden) == blocking.SCENE


def test_the_mid_take_reframe_is_scene_rather_than_forty_two_seconds_of_veto() -> None:
    """The FX6's own furniture, moved sideways by the lens — nothing was ever in the way.

    Its signature is the opposite of the piano lid's: it holds for tens of seconds and then
    falls to zero within one sample. No class of its own, because neither the settle test nor
    global drift separates it from a true blocking on the one example there is.
    """
    novel, hidden = _peaks("mid-fx6", (0, 42))

    assert blocking.verdict(novel, hidden) == blocking.SCENE


def test_the_score_ranks_a_drummer_above_a_real_blocking_and_the_discriminator_does_not() -> None:
    """Why the class exists at all: 90 seconds of one angle, the score upside down (#189)."""
    readings = _measured("mid-a7iv").readings
    crossing = readings[64]
    drummer = readings[78]

    assert crossing.score < drummer.score
    assert crossing.novel > drummer.novel
    assert crossing.hidden > drummer.hidden


def test_the_ending_the_adjudication_found_clean_flags_nothing_to_class() -> None:
    """The FX6 through the ending: two known corner heads, and not one sample over threshold."""
    readings = _measured("ending-fx6").readings

    assert max(one.score for one in readings) < DEFAULT_THRESHOLD


def test_the_evidence_set_is_every_window_the_three_adjudications_judged() -> None:
    """A transcription guard: the levels are fitted to these windows, so a window quietly
    dropped from the table would widen the gap they were chosen to sit in."""
    assert len(_adjudicated(blocking.OBSTRUCTION)) == 3
    assert len(_adjudicated(blocking.SCENE)) == 11


def test_the_evidence_set_separates_with_room_either_side_of_both_levels() -> None:
    """Both levels sit between the classes rather than on top of one of them."""
    true_novel = [_peaks(*one)[0] for one in _adjudicated(blocking.OBSTRUCTION)]
    true_hidden = [_peaks(*one)[1] for one in _adjudicated(blocking.OBSTRUCTION)]
    false_novel = [_peaks(*one)[0] for one in _adjudicated(blocking.SCENE)]
    false_hidden = [_peaks(*one)[1] for one in _adjudicated(blocking.SCENE)]

    assert min(true_novel) > blocking.NOVEL_AREA > max(false_novel)
    assert min(true_hidden) > blocking.HIDDEN_AREA > max(false_hidden)


def test_a_window_carries_its_class_and_the_readings_behind_it(
    attach: Attach, fixture_video: Path
) -> None:
    attach(_studio_holding(fixture_video))

    record = _completed(_scan([], _run(5, 2, 5)))
    catalog = json.loads(Path(record["path"]).read_text(encoding="utf-8"))

    window = catalog["windows"][0]
    assert window["kind"] == blocking.OBSTRUCTION
    assert window["peak_novel"] >= blocking.NOVEL_AREA
    assert window["peak_hidden"] >= blocking.HIDDEN_AREA
    assert record["obstructions"] == 1
    assert record["unusable_seconds"] == window["duration_seconds"]


def test_the_gist_puts_an_obstruction_ahead_of_a_scene_window_that_scored_higher(
    attach: Attach, fixture_video: Path
) -> None:
    """The inline budget is small and the veto is the part a builder has to honour."""
    attach(_studio_holding(fixture_video))
    bed = gray_frame(0.35)
    crossing = gray_frame(0.15, anchor="side")
    frames = [bed] * 5 + [CLEAN] * 2 + [crossing] * 2 + [CLEAN] * 2 + [bed] * 5

    record = _completed(_scan([], frames))

    kinds = [one["kind"] for one in record["worst_windows"]]
    assert kinds[0] == blocking.OBSTRUCTION
    assert kinds.count(blocking.SCENE) == 2
    assert record["worst_windows"][0]["peak_score"] < record["worst_windows"][1]["peak_score"]
    assert record["obstructions"] == 1


def test_the_result_says_what_the_classes_mean_and_what_the_window_edges_do_not(
    attach: Attach, fixture_video: Path
) -> None:
    """The detector loses a body once it stops moving; a result that implied otherwise would
    have a builder cutting into the second after a crossing (#189)."""
    attach(_studio_holding(fixture_video))

    record = _completed(_scan([], _run(5, 2, 5)))

    assert set(record["discriminator"]["classes"]) == {blocking.OBSTRUCTION, blocking.SCENE}
    assert "stops moving" in record["discriminator"]["bounds"]


# --- helpers ---------------------------------------------------------------------------------


def _completed(envelope: dict[str, Any]) -> dict[str, Any]:
    """The result of a scan that has to have finished for the test to mean anything."""
    record = wait_for(envelope["job_id"])
    assert record.state == "completed", record.error
    assert record.result is not None
    return dict(record.result)


def _classes(scan: blocking.Scan) -> list[str]:
    return [blocking.verdict(one.novel, one.hidden) for one in scan.readings]


def _scored(*frames: bytes) -> blocking.Scan:
    """Score composed frames the way the worker does: through the grid reader."""
    return blocking.measure(blocking.read_grid(b"".join(frames)))


def _run(before: int, blocked: int, after: int) -> list[bytes]:
    """Clean seconds, then a blocker wiping through, then clean again."""
    return [CLEAN] * before + [BLOCKED] * blocked + [CLEAN] * after


def _scan(
    calls: list[Sequence[str]],
    frames: list[bytes],
    **kwargs: Any,
) -> dict[str, Any]:
    return analyze_occlusion(
        get_connection(),
        "20260617_D_A7IV_0006.MP4",
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


def _render_blocked_video(target: Path) -> Path:
    """Three seconds of clear grey, then three with the bottom half of the frame blacked out."""
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
            "color=c=gray:size=320x240:rate=10:duration=3",
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=320x120:rate=10:duration=3",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=320x120:rate=10:duration=3",
            "-filter_complex",
            "[1:v][2:v]vstack=inputs=2[blocked];[0:v][blocked]concat=n=2:v=1:a=0[v]",
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
