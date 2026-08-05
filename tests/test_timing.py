"""Dual time: frames authoritative, seconds and timecode derived once, in tested code."""

from __future__ import annotations

import pytest

from resolve_mcp.timing import (
    IN_POINT,
    OUT_POINT,
    Snap,
    dual_time,
    duration_frames,
    frames_from_seconds,
    ranges_overlap,
    timecode,
)


@pytest.mark.parametrize(
    ("frames", "fps", "expected"),
    [
        (0, 24.0, "00:00:00:00"),
        (23, 24.0, "00:00:00:23"),
        (24, 24.0, "00:00:01:00"),
        (90, 59.94, "00:00:01:30"),
        (60 * 60 * 24, 24.0, "01:00:00:00"),
    ],
)
def test_timecode_counts_frames_at_the_nearest_whole_rate(
    frames: int, fps: float, expected: str
) -> None:
    assert timecode(frames, fps) == expected


def test_dual_carries_frames_seconds_timecode_and_fps() -> None:
    assert dual_time(90, 59.94) == {
        "frames": 90,
        "seconds": 1.502,
        "timecode": "00:00:01:30",
        "fps": 59.94,
    }


def test_dual_without_an_fps_still_reports_frames() -> None:
    assert dual_time(90, None) == {"frames": 90, "seconds": None, "timecode": None, "fps": None}


def test_dual_of_nothing_is_nothing() -> None:
    assert dual_time(None, 24.0) is None


# --- snapping ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "fps", "snap", "expected"),
    [
        (1.0, 24.0, IN_POINT, 24),
        (1.0, 24.0, OUT_POINT, 24),
        (1.5, 24.0, IN_POINT, 36),
        (1.01, 24.0, IN_POINT, 24),
        (1.01, 24.0, OUT_POINT, 25),
        (0.0, 59.94, IN_POINT, 0),
        (10.0, 59.94, IN_POINT, 599),
        (10.0, 59.94, OUT_POINT, 600),
    ],
)
def test_seconds_snap_the_way_the_caller_asked(
    seconds: float, fps: float, snap: Snap, expected: int
) -> None:
    assert frames_from_seconds(seconds, fps, snap) == expected


def test_a_boundary_second_does_not_drift_up_on_a_ceil() -> None:
    """0.1 * 3 is not 0.3 in binary; a moment on a frame must not gain one."""
    assert frames_from_seconds(0.1 + 0.1 + 0.1, 10.0, OUT_POINT) == 3


def test_snapping_needs_a_real_frame_rate() -> None:
    with pytest.raises(ValueError, match="fps must be positive"):
        frames_from_seconds(1.0, 0.0, IN_POINT)


# --- half-open range math ---------------------------------------------------------------


def test_duration_is_out_minus_in() -> None:
    assert duration_frames(14032, 14210) == 178


def test_a_range_that_names_no_frames_has_no_duration() -> None:
    assert duration_frames(100, 100) == 0


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        pytest.param((0, 100), (100, 200), False, id="touching-at-a-boundary"),
        pytest.param((0, 100), (99, 200), True, id="one-frame-of-overlap"),
        pytest.param((0, 100), (0, 100), True, id="identical"),
        pytest.param((0, 100), (20, 40), True, id="contained"),
        pytest.param((100, 200), (0, 50), False, id="apart"),
    ],
)
def test_half_open_ranges_overlap_only_when_they_share_a_frame(
    a: tuple[int, int], b: tuple[int, int], overlap: bool
) -> None:
    assert ranges_overlap(*a, *b) is overlap
    assert ranges_overlap(*b, *a) is overlap
