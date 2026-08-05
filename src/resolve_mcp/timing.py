"""Dual time: frames are authoritative, seconds and timecode are derived.

Every result that names a position carries all four — frames, seconds, timecode, fps — so
neither the director nor the agent ever does conversion math by hand. The conversion lives
here, once, tested; nothing else in the server is allowed to reimplement it.

Timecode is non-drop-frame: frames are counted at the nearest whole rate (59.94 counts at
60), which is what Resolve's own frame numbering does. Drop-frame notation is not v1.
"""

from __future__ import annotations

from typing import Any

SECONDS_PRECISION = 3


def timecode(frames: int, fps: float) -> str:
    """``HH:MM:SS:FF`` at the nearest whole frame rate, non-drop."""
    rate = max(round(fps), 1)
    whole_seconds, frame = divmod(int(frames), rate)
    minutes, seconds = divmod(whole_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{frame:02d}"


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
