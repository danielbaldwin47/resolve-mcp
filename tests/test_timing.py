"""Dual time: frames authoritative, seconds and timecode derived once, in tested code."""

from __future__ import annotations

import pytest

from resolve_mcp.errors import InvalidRequestError
from resolve_mcp.timing import (
    IN_POINT,
    OUT_POINT,
    Snap,
    dual_time,
    duration_frames,
    frames_from_seconds,
    frames_from_timecode,
    ranges_overlap,
    timecode,
    to_frames,
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


@pytest.mark.parametrize(
    ("value", "fps", "expected"),
    [
        ("00:00:00:00", 24.0, 0),
        ("00:00:01:00", 24.0, 24),
        ("00:00:01:30", 59.94, 90),
        # The Duration an audio-only pool clip reports (#46, live-verified): 23.976
        # counts at the nominal 24, exactly as timecode() writes it.
        ("01:26:38:09", 23.976, 124761),
        # A signed distance reads back signed: timecode() writes negatives, so its
        # mirror parses them.
        ("-00:00:16:40", 59.94, -1000),
    ],
)
def test_frames_from_timecode_mirrors_timecode(value: str, fps: float, expected: int) -> None:
    assert frames_from_timecode(value, fps) == expected
    assert timecode(expected, fps) == value


@pytest.mark.parametrize("value", ["", "300", "audio", "01:26:38", "2.5s"])
def test_a_string_that_is_not_a_timecode_reads_as_none(value: str) -> None:
    """Resolve says "no value" with an empty string — an absence, not an error."""
    assert frames_from_timecode(value, 24.0) is None


def test_drop_frame_timecode_is_refused_not_miscounted() -> None:
    """Resolve writes drop-frame as HH:MM:SS;FF. Non-drop arithmetic over it would be
    silently wrong, so the semicolon form reads as None until drop-frame is a feature."""
    assert frames_from_timecode("00:01:00;02", 29.97) is None


def test_a_frame_field_at_or_past_the_whole_rate_reads_as_none() -> None:
    """timecode() never writes ff >= rate, so a mirror that accepted 00:00:00:99 at 24
    fps would be reading something that is not a timecode of this rate."""
    assert frames_from_timecode("00:00:00:99", 24.0) is None
    assert frames_from_timecode("00:00:00:24", 24.0) is None


def test_a_backwards_distance_is_signed_not_wrapped() -> None:
    """A sync offset points backwards as often as forwards, and is not a position."""
    assert timecode(-1000, 59.94) == "-00:00:16:40"
    assert dual_time(-1000, 59.94) == {
        "frames": -1000,
        "seconds": -16.683,
        "timecode": "-00:00:16:40",
        "fps": 59.94,
    }


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
