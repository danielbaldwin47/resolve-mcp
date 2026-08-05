"""From a clip name to the file ffmpeg will open, and the numbers that file is read in.

Both video routes start the same way and fail the same way when the media has moved — and
both need the clip's own frame numbering, which is not the file's. Resolve numbers a clip
from its ``Start`` (an hour in, for footage with an hour-based start timecode); ffmpeg
counts from zero. Getting that wrong grabs the right file at the wrong moment, silently, so
the offset lives here rather than in each caller.
"""

from __future__ import annotations

from typing import NamedTuple

from ..errors import ResolveMcpError
from ..resolve import media
from ..resolve.connection import ResolveConnection

RELINK_FIX = (
    "relink_media points a clip back at its media; list_media shows what is offline."
)


class Source(NamedTuple):
    """A clip's media as the video routes need it."""

    path: str
    bin_path: str
    fps: float | None
    start: int
    out: int | None
    reported: dict[str, str]

    def seek_seconds(self, frame: int) -> float:
        """Where ffmpeg has to seek to land on ``frame`` of this clip."""
        if not self.fps:
            raise ValueError("seek_seconds needs a frame rate; callers check for one first")
        return (frame - self.start) / self.fps

    @property
    def duration_frames(self) -> int | None:
        return None if self.out is None else self.out - self.start


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

    start, out = media.frame_bounds(reported)
    return Source(
        path=path,
        bin_path=located.bin_path,
        fps=media.frame_rate(reported),
        start=start or 0,
        out=out,
        reported=reported,
    )
