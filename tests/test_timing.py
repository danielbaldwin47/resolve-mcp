"""Dual time: frames authoritative, seconds and timecode derived once, in tested code."""

from __future__ import annotations

import pytest

from resolve_mcp.timing import dual_time, timecode


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
