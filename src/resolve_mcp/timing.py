"""Dual time: frames are authoritative, seconds and timecode are derived.

Every result that names a position carries all four — frames, seconds, timecode, fps — so
neither the director nor the agent ever does conversion math by hand. The conversion lives
here, once, tested; nothing else in the server is allowed to reimplement it.

Timecode is non-drop-frame: frames are counted at the nearest whole rate (59.94 counts at
60), which is what Resolve's own frame numbering does. Drop-frame notation is not v1.

Ranges are half-open ``[in, out)`` everywhere: duration is ``out - in``, and adjacent
takes share a boundary frame without either owning it twice.
"""

from __future__ import annotations

import math
from typing import Any, Literal

SECONDS_PRECISION = 3

Snap = Literal["floor", "ceil"]
"""Which way a seconds value that lands between frames is resolved."""

IN_POINT: Snap = "floor"
"""In points snap back: the frame the moment falls on is included."""

OUT_POINT: Snap = "ceil"
"""Out points snap forward: half-open, so the moment stays inside the range."""

_BOUNDARY_TOLERANCE = 9
"""Decimal places kept before snapping — enough to kill float noise, not a real fraction."""


def timecode(frames: int, fps: float) -> str:
    """``HH:MM:SS:FF`` at the nearest whole frame rate, non-drop."""
    rate = max(round(fps), 1)
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
