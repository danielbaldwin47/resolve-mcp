"""Dual time: frames are authoritative, seconds and timecode are derived.

Every result that names a position carries all four — frames, seconds, timecode, fps — so
neither the director nor the agent ever does conversion math by hand. The conversion lives
here, once, tested; nothing else in the server is allowed to reimplement it.

Timecode is non-drop-frame: frames are counted at the nearest whole rate (59.94 counts at
60), which is what Resolve's own frame numbering does. Drop-frame notation is not v1.

Reading a time *in* is the mirror of that rule: ``to_frames`` takes frames as given and
turns seconds into frames only when the caller says which way to snap. Seconds rarely land
on a frame boundary, and a server that picked floor or ceil on the caller's behalf would
move a cut point by a frame without anyone deciding to — so it refuses instead.

Ranges are half-open ``[in, out)`` everywhere: duration is ``out - in``, and adjacent
takes share a boundary frame without either owning it twice.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from .errors import InvalidRequestError

SECONDS_PRECISION = 3
SNAPS = ("floor", "ceil")

Snap = Literal["floor", "ceil"]
"""Which way a seconds value that lands between frames is resolved."""

IN_POINT: Snap = "floor"
"""In points snap back: the frame the moment falls on is included."""

OUT_POINT: Snap = "ceil"
"""Out points snap forward: half-open, so the moment stays inside the range."""

_BOUNDARY_TOLERANCE = 9
"""Decimal places kept before snapping — enough to kill float noise, not a real fraction."""


def timecode(frames: int, fps: float) -> str:
    """``HH:MM:SS:FF`` at the nearest whole frame rate, non-drop.

    A negative count is signed rather than wrapped: not every number here is a position on
    a timeline — a sync offset is a distance, and it routinely points backwards.
    """
    rate = max(round(fps), 1)
    if frames < 0:
        return f"-{timecode(-int(frames), fps)}"
    whole_seconds, frame = divmod(int(frames), rate)
    minutes, seconds = divmod(whole_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"


def frames_from_seconds(seconds: float, fps: float, snap: Snap) -> int:
    """Seconds to frames, snapped the way the caller asked — never silently rounded.

    A seconds value the director typed almost never lands on a frame boundary, and which
    way it moves changes the cut: an in point that rounded up would drop the frame the
    moment happens on. So the direction is a required argument, not a default.

    A value already on a boundary stays put in both directions; the tolerance below only
    absorbs binary representation error, never a real fraction of a frame.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive to convert seconds to frames, got {fps!r}")
    exact = round(seconds * fps, _BOUNDARY_TOLERANCE)
    return int(math.floor(exact) if snap == "floor" else math.ceil(exact))


def duration_frames(in_frame: int, out_frame: int) -> int:
    """Frames covered by the half-open range ``[in, out)``."""
    return int(out_frame) - int(in_frame)


def ranges_overlap(a_in: int, a_out: int, b_in: int, b_out: int) -> bool:
    """Whether two half-open ranges share a frame. Touching at a boundary does not."""
    return a_in < b_out and b_in < a_out


def dual_time(frames: int | None, fps: float | None) -> dict[str, Any] | None:
    """A position in all four representations, or ``None`` if there is no position.

    An unknown fps still yields frames — the authoritative number — rather than nothing.
    """
    if frames is None:
        return None
    if fps is None or fps <= 0:
        return {"frames": int(frames), "seconds": None, "timecode": None, "fps": None}
    return {
        "frames": int(frames),
        "seconds": round(int(frames) / fps, SECONDS_PRECISION),
        "timecode": timecode(frames, fps),
        "fps": fps,
    }


def to_frames(value: Any, fps: float | None, field: str = "time") -> int | None:
    """A caller-supplied time as frames, or ``None`` if nothing was asked for.

    Accepts a whole number of frames (bare, or ``{"frames": 96}``) and seconds carrying an
    explicit snap: ``{"seconds": 2.52, "snap": "floor"}``. Bare seconds are refused — see
    the module docstring for why the server will not choose the rounding.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise _unreadable(field, value)
    if isinstance(value, int | float):
        return _whole_frames(value, field)
    if not isinstance(value, dict):
        raise _unreadable(field, value)

    frames = value.get("frames")
    has_seconds = "seconds" in value
    if frames is not None and has_seconds:
        raise _unreadable(field, value)
    if frames is None and not has_seconds:
        raise _unreadable(field, value)
    if frames is not None:
        return _whole_frames(frames, field)

    snap = value.get("snap")
    if snap not in SNAPS:
        raise InvalidRequestError(
            cause=f"{field} was given in seconds without saying how to snap it to a frame.",
            fix=f'Add "snap": "floor" or "ceil" — {field} in seconds is a range, not a frame.',
            detail={"field": field, "value": value},
        )
    if fps is None or fps <= 0:
        raise InvalidRequestError(
            cause=f"{field} was given in seconds but the fps here is unknown.",
            fix=f"Pass {field} in frames instead — frames need no rate to be exact.",
            detail={"field": field, "value": value},
        )
    try:
        exact = float(value["seconds"]) * fps
    except (TypeError, ValueError) as exc:
        raise _unreadable(field, value) from exc
    return int(math.floor(exact) if snap == "floor" else math.ceil(exact))


def _whole_frames(value: Any, field: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise _unreadable(field, value) from exc
    if number != int(number):
        raise InvalidRequestError(
            cause=f"{field} is {number}, which is not a whole frame.",
            fix=(
                f"Frames are whole numbers. For a time between frames pass "
                f'{field}={{"seconds": …, "snap": "floor"}} and say which way to snap.'
            ),
            detail={"field": field, "value": value},
        )
    return int(number)


def _unreadable(field: str, value: Any) -> InvalidRequestError:
    return InvalidRequestError(
        cause=f"{field}={value!r} is not a time this server can read.",
        fix=(
            f'Give {field} as frames (96 or {{"frames": 96}}) or as seconds with a snap '
            f'({{"seconds": 2.52, "snap": "floor"}}) — one of the two, never both.'
        ),
        detail={"field": field, "value": repr(value)},
    )
