"""Frame grabs: the moments the agent looks at with its own eyes.

This is the one compute route that is not a job. A grab is a seek and a single frame — fast
enough that a job record and a poll would cost more than the work — and the whole point of
it is that the agent gets a path it can open *now*, while it is reasoning about the moment
that made it ask. So the route runs inline and returns the files.

It is cached all the same, by the same key every job uses: the same moment on unchanged
media is the same JPEG, and a session that grabs a frame twice should pay once.

The grabs land on disk rather than coming back inline as bytes. The agent reads the path,
which costs one image in its context instead of one per poll of a job record — and a grab
sized to the client's image cap (1568px on the long edge) is a frame that arrives whole
rather than downscaled on the way in.
"""

from __future__ import annotations

from typing import Any

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
MAX_TIMES = 12
"""Frames per call. Each one is an image in the agent's context; a request for more than a
dozen is a scene-cut scan or a render, not a grab."""


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
    frames = _requested_frames(times, source, clip)

    params = {"clip": clip, "bin": source.bin_path, "frames": frames, "max_edge": edge}
    key = cache.cache_key(KIND, [cache.fingerprint(source.path)], params)

    if not refresh:
        hit = cache.lookup(key, config)
        if hit is not None:
            log.info("Answered %d frame grab(s) on %s from cache", len(frames), clip)
            return {**hit, "cached": True}

    grabbed = [_one_frame(source, clip, frame, edge, key, runner, config) for frame in frames]
    result = {
        "clip": clip,
        "bin": source.bin_path,
        "source": source.path,
        "max_edge": edge,
        "frames": grabbed,
    }
    cache.remember(key, KIND, result, [one["path"] for one in grabbed], config)
    return {**result, "cached": False}


def _one_frame(
    source: Source,
    clip: str,
    frame: int,
    max_edge: int,
    key: str,
    runner: Runner | None,
    config: Config,
) -> dict[str, Any]:
    """One grab, described by what was actually written rather than by what was asked for."""
    target = config.frame_dir / f"{slug(clip, 'frame')}-{key[:12]}-{frame:08d}.jpg"
    ffmpeg.grab(
        source.path,
        target,
        seconds=source.seek_seconds(frame),
        max_edge=max_edge,
        runner=runner,
        config=config,
    )
    return {"time": dual_time(frame, source.fps), **jpeg.describe(target)}


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


def _requested_frames(times: list[Any], source: Source, clip: str) -> list[int]:
    """The asked-for moments as this clip's frame numbers: whole, in range, each one once."""
    if not times:
        raise InvalidRequestError(
            cause="No time was given to grab.",
            fix=(
                "Pass at least one time: times=[1024] or "
                'times=[{"seconds": 17.5, "snap": "floor"}].'
            ),
            detail={"clip": clip},
        )
    if len(times) > MAX_TIMES:
        raise InvalidRequestError(
            cause=f"{len(times)} frames were asked for in one call.",
            fix=(
                f"Ask for at most {MAX_TIMES} at a time — each grab is an image you have to "
                "read. To find where the shots change instead, run detect_scene_cuts."
            ),
            detail={"clip": clip, "requested": len(times), "maximum": MAX_TIMES},
        )
    if not source.fps:
        raise InvalidRequestError(
            cause=f"Resolve reports no frame rate for {clip!r}, so a time cannot be seeked to.",
            fix="inspect_clip shows what Resolve knows about the clip; a still has no timeline.",
            detail={"clip": clip, "file_path": source.path},
        )

    frames: list[int] = []
    for index, value in enumerate(times):
        frame = to_frames(value, source.fps, field=f"times[{index}]")
        if frame is None:
            continue
        _within_media(frame, source, clip)
        if frame not in frames:
            frames.append(frame)
    return frames


def _within_media(frame: int, source: Source, clip: str) -> None:
    """A seek past the end exits zero and writes nothing, so the range is checked up front."""
    out = source.out
    if frame < source.start or (out is not None and frame >= out):
        raise InvalidRequestError(
            cause=f"Frame {frame} is outside the media {clip!r} holds.",
            fix="inspect_clip reports the clip's own bounds; grabs sit inside them, half-open.",
            detail={
                "clip": clip,
                "requested": dual_time(frame, source.fps),
                "bounds": {
                    "in": dual_time(source.start, source.fps),
                    "out": dual_time(out, source.fps),
                },
            },
        )
