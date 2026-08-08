"""From a clip name to the file ffmpeg will open, and the numbers that file is read in.

Both video routes start the same way and fail the same way when the media has moved — and
both need the clip's own frame numbering, which is not the file's. Resolve numbers a clip
from its ``Start`` (an hour in, for footage with an hour-based start timecode); ffmpeg
counts from zero. Getting that wrong grabs the right file at the wrong moment, silently, so
the offset lives here rather than in each caller.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ..errors import InvalidRequestError, ResolveMcpError
from ..resolve import media
from ..resolve.connection import ResolveConnection
from ..timing import dual_time

RELINK_FIX = "relink_media points a clip back at its media; list_media shows what is offline."


class Source(NamedTuple):
    """A clip's media as the video routes need it, in the clip's own frame numbering."""

    name: str
    path: str
    bin_path: str
    fps: float | None
    start: int
    out: int | None

    def seek_seconds(self, frame: int) -> float:
        """Where ffmpeg has to seek to land on ``frame`` of this clip."""
        if not self.fps:
            raise ValueError("seek_seconds needs a frame rate; callers check for one first")
        return (frame - self.start) / self.fps

    def holds(self, frame: int) -> bool:
        """Whether ``frame`` is inside this clip's media, half-open ``[start, out)``."""
        return frame >= self.start and (self.out is None or frame < self.out)

    def outside(self, frame: int) -> InvalidRequestError:
        """Why a frame this clip does not hold cannot be asked for.

        Worth refusing rather than passing on: ffmpeg seeked past the end of a file exits
        zero and writes nothing, which reads as a server bug rather than as a bad time.
        """
        return InvalidRequestError(
            cause=f"Frame {frame} is outside the media {self.name!r} holds.",
            fix="inspect_clip reports the clip's own bounds; times sit inside them, half-open.",
            detail={
                "clip": self.name,
                "requested": dual_time(frame, self.fps),
                "bounds": self.bounds,
            },
        )

    @property
    def bounds(self) -> dict[str, Any]:
        """The clip's media bounds in dual time, half-open."""
        return {"in": dual_time(self.start, self.fps), "out": dual_time(self.out, self.fps)}


def locate(
    connection: ResolveConnection,
    clip: str,
    bin_path: str | None,
    failure: type[ResolveMcpError],
) -> Source:
    """Find the clip and the file behind it, or raise ``failure`` naming what to do about it.

    The failure type is the caller's because the fix is: a grab that cannot find its media
    and a scan that cannot find its media are the same problem to the director and different
    tools to the agent.
    """
    pool = media.media_pool(connection)
    located = media.find_clip(pool, clip, bin_path)
    reported = media.properties(located.clip)
    path = reported.get(media.FILE_PATH, "")
    if not path or media.is_offline(path):
        raise failure(
            cause=f"{clip!r} has no readable file on disk.",
            fix=RELINK_FIX,
            detail={"clip": clip, "file_path": path},
        )

    fps = media.frame_rate(reported)
    # The clip's own rate is the only one this seam has; it feeds the Duration fallback
    # so a grab sees the same bounds a listing and a cut do.
    start, out = media.frame_bounds(reported, fps=fps)
    return Source(
        name=clip,
        path=path,
        bin_path=located.bin_path,
        fps=fps,
        start=start or 0,
        out=out,
    )
