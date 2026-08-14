"""Frame grabs: the moments the agent looks at with its own eyes.

This is the one compute route that is not a job. A grab is a seek and a single frame — fast
enough that a job record and a poll would cost more than the work — and the whole point of
it is that the agent gets a path it can open *now*, while it is reasoning about the moment
that made it ask. So the route runs inline and returns the files.

It is cached all the same, by the same key every job uses, and cached **per frame** rather
than per call: a session narrows in on a moment, so the second call is usually the first
call's times plus one more, and a batch key would decode everything again to add it.

The grabs land on disk rather than coming back inline as bytes. The agent reads the path,
which costs one image in its context instead of one per poll of a job record — and a grab
sized to the client's image cap (1568px on the long edge) is a frame that arrives whole
rather than downscaled on the way in.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ..config import Config, get_config
from ..errors import FrameGrabError, InvalidRequestError
from ..ffmpeg import Runner
from ..jobs import cache
from ..logging_config import get_logger
from ..naming import slug
from ..resolve.connection import ResolveConnection
from ..timing import dual_time, to_frames
from . import ffmpeg, jpeg
from .source import Source, locate

log = get_logger("video")

KIND = "grab_frames"

DEFAULT_MAX_EDGE = 1568
"""The client's image cap: a longer edge is downscaled on arrival, so it buys nothing."""

MIN_MAX_EDGE = 64
"""Below this a frame is a thumbnail — too small to answer the question that asked for it."""

MAX_TIMES = 12
"""Frames per call. Each one is an image in the agent's context; a request for more than a
dozen is a scene-cut scan or a render, not a grab."""


class _Grab(NamedTuple):
    """One frame, whether it was already in the cache, and what decoded it if anything did."""

    frame: dict[str, Any]
    cached: bool
    decode: dict[str, Any] | None = None


def grab_frames(
    connection: ResolveConnection,
    clip: str,
    times: list[Any],
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    max_edge: int = DEFAULT_MAX_EDGE,
    refresh: bool = False,
    runner: Runner | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Grab ``times`` on ``clip`` as JPEGs and return where they landed.

    Times are dual time, the same as everywhere else: frames, or seconds with a snap. They
    are the clip's own frame numbers — the ones ``inspect_clip`` reports — and a moment
    outside the clip's media is refused here rather than by ffmpeg, which would exit zero
    and write nothing.

    ``runner`` is the subprocess seam: the default shells out for real.
    """
    config = config or get_config()
    source = locate(connection, clip, bin, FrameGrabError)
    edge = _readable_edge(max_edge)
    frames = _requested_frames(times, source)

    fingerprint = cache.fingerprint(source.path)
    grabs = [
        _one_frame(source, frame, edge, fingerprint, refresh, runner, config) for frame in frames
    ]

    hits = sum(1 for one in grabs if one.cached)
    decodes = [one.decode for one in grabs if one.decode is not None]
    log.info("Returned %d frame(s) of %s, %d of them from cache", len(grabs), clip, hits)
    return {
        "clip": source.name,
        "bin": source.bin_path,
        "source": source.path,
        "max_edge": edge,
        "frames": [one.frame for one in grabs],
        # Null when every frame came off the cache — nothing decoded, so there is no
        # device to report (#202). Every fresh grab in one call decodes the same way.
        "decode": decodes[0] if decodes else None,
        "cached": hits == len(grabs),
    }


def _one_frame(
    source: Source,
    frame: int,
    max_edge: int,
    fingerprint: dict[str, Any],
    refresh: bool,
    runner: Runner | None,
    config: Config,
) -> _Grab:
    """One grab, described by what was actually written rather than by what was asked for."""
    params = {"clip": source.name, "bin": source.bin_path, "frame": frame, "max_edge": max_edge}
    key = cache.cache_key(KIND, [fingerprint], params)
    if not refresh:
        hit = cache.lookup(key, config)
        if hit is not None:
            return _Grab(hit, True)

    target = config.frame_dir / f"{slug(source.name, 'frame')}-{key[:12]}.jpg"
    written = ffmpeg.grab(
        source.path,
        target,
        seconds=source.seek_seconds(frame),
        max_edge=max_edge,
        runner=runner,
        config=config,
    )
    grabbed = {"time": dual_time(frame, source.fps), **jpeg.describe(target)}
    cache.remember(key, KIND, grabbed, (target,), config)
    return _Grab(grabbed, False, written.decode)


def _readable_edge(max_edge: int) -> int:
    if not MIN_MAX_EDGE <= max_edge <= DEFAULT_MAX_EDGE:
        raise InvalidRequestError(
            cause=f"max_edge={max_edge} is outside the range this server grabs at.",
            fix=(
                f"Ask for between {MIN_MAX_EDGE} and {DEFAULT_MAX_EDGE} pixels. "
                f"{DEFAULT_MAX_EDGE} is the client's own image cap — anything larger is "
                "downscaled on arrival, so it costs decode time and buys no detail."
            ),
            detail={"requested": max_edge, "maximum": DEFAULT_MAX_EDGE},
        )
    return int(max_edge)


def _requested_frames(times: list[Any], source: Source) -> list[int]:
    """The asked-for moments as this clip's frame numbers: whole, in range, each one once."""
    if not times:
        raise InvalidRequestError(
            cause="No time was given to grab.",
            fix=(
                "Pass at least one time: times=[1024] or "
                'times=[{"seconds": 17.5, "snap": "floor"}].'
            ),
            detail={"clip": source.name},
        )
    if len(times) > MAX_TIMES:
        raise InvalidRequestError(
            cause=f"{len(times)} frames were asked for in one call.",
            fix=(
                f"Ask for at most {MAX_TIMES} at a time — each grab is an image you have to "
                "read. To find where the shots change instead, run detect_scene_cuts."
            ),
            detail={"clip": source.name, "requested": len(times), "maximum": MAX_TIMES},
        )
    if not source.fps:
        raise InvalidRequestError(
            cause=(
                f"Resolve reports no frame rate for {source.name!r}, "
                "so a time cannot be seeked to."
            ),
            fix="inspect_clip shows what Resolve knows about the clip; a still has no timeline.",
            detail={"clip": source.name, "file_path": source.path},
        )

    frames: list[int] = []
    for index, value in enumerate(times):
        field = f"times[{index}]"
        frame = to_frames(value, source.fps, field=field)
        if frame is None:
            # Dropping it would return fewer frames than were asked for without saying so.
            raise InvalidRequestError(
                cause=f"{field} is empty, so there is no moment to grab.",
                fix="Every entry in times has to name a moment; remove the empty one.",
                detail={"clip": source.name, "field": field},
            )
        if not source.holds(frame):
            raise source.outside(frame)
        if frame not in frames:
            frames.append(frame)
    return frames
