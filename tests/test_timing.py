"""Dual time: frames authoritative, seconds and timecode derived once, in tested code."""

from __future__ import annotations

import pytest

from resolve_mcp.errors import InvalidRequestError
from resolve_mcp.timing import dual_time, timecode, to_frames


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


# --- reading a time in ------------------------------------------------------------------


def test_nothing_asked_for_is_nothing_read() -> None:
    assert to_frames(None, 24.0) is None


@pytest.mark.parametrize("value", [96, 96.0, {"frames": 96}, {"frames": "96"}])
def test_a_bare_number_is_frames(value: object) -> None:
    assert to_frames(value, 24.0) == 96


@pytest.mark.parametrize(
    ("snap", "expected"),
    [("floor", 60), ("ceil", 61)],
)
def test_seconds_snap_the_way_the_caller_says(snap: str, expected: int) -> None:
    assert to_frames({"seconds": 2.52, "snap": snap}, 24.0) == expected


def test_seconds_that_land_on_a_frame_snap_to_it_either_way() -> None:
    assert to_frames({"seconds": 2.5, "snap": "floor"}, 24.0) == 60
    assert to_frames({"seconds": 2.5, "snap": "ceil"}, 24.0) == 60


def test_bare_seconds_are_rejected_rather_than_guessed() -> None:
    with pytest.raises(InvalidRequestError) as raised:
        to_frames({"seconds": 2.52}, 24.0)

    assert "snap" in raised.value.fix
    assert raised.value.code == "invalid_request"


def test_a_fractional_frame_count_is_rejected_rather_than_rounded() -> None:
    with pytest.raises(InvalidRequestError) as raised:
        to_frames(96.4, 24.0)

    assert "seconds" in raised.value.fix


def test_seconds_need_an_fps_to_become_frames() -> None:
    with pytest.raises(InvalidRequestError) as raised:
        to_frames({"seconds": 2.5, "snap": "floor"}, None)

    assert "fps" in raised.value.cause


@pytest.mark.parametrize(
    "value",
    [
        {"seconds": 2.5, "snap": "nearest"},
        {"frames": 96, "seconds": 2.5, "snap": "floor"},
        {},
        {"timecode": "01:00:00:00"},
        "01:00:00:00",
        True,
    ],
)
def test_a_time_that_cannot_be_read_without_guessing_is_rejected(value: object) -> None:
    with pytest.raises(InvalidRequestError):
        to_frames(value, 24.0)


def test_the_rejection_names_the_field_it_came_from() -> None:
    with pytest.raises(InvalidRequestError) as raised:
        to_frames({"seconds": 2.52}, 24.0, field="end")

    assert "end" in raised.value.cause
