"""What a sampled-decode scan shares: the range it runs over, its sample grid, and its runs.

Two scans decode a range of a clip a few frames a second and turn the readings into windows —
``occlusion``, which asks whether something is standing in front of the camera, and
``quality``, which asks whether the picture the camera got is worth cutting to. What they
measure is entirely different and lives apart, in ``blocking`` and ``picture``. What they do
around the measurement is the same to the line: check the range is a range this clip has,
work out which frame each sample was taken at, and gather the samples that failed into runs.

That part lives here rather than twice, because the failure modes it guards are ones a copy
drifts into silently. A range check that clamps instead of refusing answers for footage
nobody decoded; a sample-time rule that reads the frame times back out of ffmpeg instead of
deriving them from the rate shifts every window after a dropped frame. Both scans have to
agree about those, and the way to make two things agree is to have one of them.
"""

from __future__ import annotations

from typing import Any

from ..errors import InvalidRequestError
from ..timing import IN_POINT, frames_from_seconds, to_frames
from .source import Source


def readable_range(
    start: Any,
    end: Any,
    source: Source,
    max_seconds: float,
    scan: str,
) -> tuple[int, int]:
    """The range to scan as this clip's own frame numbers, half-open and inside its media.

    ``scan`` names the caller in the refusal — "an occlusion scan", "a quality scan" — because
    the fix an agent needs is about the tool it called, not about the shared arithmetic.
    """
    if not source.fps:
        raise InvalidRequestError(
            cause=(
                f"Resolve reports no frame rate for {source.name!r}, so a range cannot be "
                "sampled."
            ),
            fix="inspect_clip shows what Resolve knows about the clip; a still has no timeline.",
            detail={"clip": source.name, "file_path": source.path},
        )

    first = to_frames(start, source.fps, field="start")
    first = source.start if first is None else first
    last = to_frames(end, source.fps, field="end")
    if last is None:
        last = source.out
    if last is None:
        raise InvalidRequestError(
            cause=f"Resolve reports no end for {source.name!r}, so the range has no out point.",
            fix="Pass end explicitly — inspect_clip reports what Resolve does know about it.",
            detail={"clip": source.name, "bounds": source.bounds},
        )

    if not source.holds(first) or first >= last:
        raise source.outside(first)
    if source.out is not None and last > source.out:
        raise source.outside(last)

    seconds = (last - first) / source.fps
    if seconds > max_seconds:
        raise InvalidRequestError(
            cause=f"The range asked for is {seconds:.0f}s of footage.",
            fix=(
                f"Scan at most {max_seconds:.0f}s at a time — {scan} answers 'is this angle "
                "usable through this song', so pass the song's range."
            ),
            detail={"clip": source.name, "seconds": round(seconds, 1)},
        )
    return int(first), int(last)


def sample_frame(index: int, first: int, rate: float, source: Source) -> int:
    """Which of the clip's own frames the nth sample was taken at.

    Derived from the rate rather than read back out of ffmpeg: the ``fps`` filter emits frames
    on an exact grid from the seek point, so the nth sample is the nth interval — and a
    decoder that dropped one would otherwise shift every later time.
    """
    return first + frames_from_seconds(index / rate, source.fps or 1.0, IN_POINT)


def sample_step(rate: float, source: Source) -> int:
    """How many of the clip's frames one sample stands for — a window's trailing edge."""
    return max(1, frames_from_seconds(1.0 / rate, source.fps or 1.0, IN_POINT))


def runs(failed: list[bool], gap: int) -> list[tuple[int, int]]:
    """Index runs of ``True``, merging any two separated by at most ``gap`` good ones.

    Half-open, so a run is ``[begin, stop)`` and a lone failure is one sample long.
    """
    found: list[tuple[int, int]] = []
    for index, flag in enumerate(failed):
        if not flag:
            continue
        if found and index - found[-1][1] <= gap:
            found[-1] = (found[-1][0], index + 1)
        else:
            found.append((index, index + 1))
    return found
