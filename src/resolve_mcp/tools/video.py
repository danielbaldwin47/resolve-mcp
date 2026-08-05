"""Video tools — seeing the picture when the audio evidence runs out.

Both routes read the file on disk the media pool points at; neither renders anything in
Resolve, so both run while the director keeps working in the GUI.
"""

from __future__ import annotations

from typing import Any

from ..resolve.connection import get_connection
from ..video import frames, scenes
from .envelope import tool


@tool
def grab_frames(
    clip: str,
    times: list[Any],
    bin: str | None = None,  # noqa: A002 - the agent-facing word for a media pool folder
    max_edge: int = frames.DEFAULT_MAX_EDGE,
    refresh: bool = False,
) -> dict[str, Any]:
    """Grab moments on a clip as JPEGs on disk and return their paths — then read them.

    Times are the clip's own frame numbers, dual time as everywhere: times=[14032] or
    times=[{"seconds": 234.5, "snap": "floor"}]. inspect_clip reports the bounds they have
    to sit inside. Up to 12 per call; each frame comes back with its time in frames, seconds
    and timecode alongside the path and the size it was written at.

    Frames land at or under 1568px on the long edge — the size an image arrives whole at —
    and are cached against the media, so grabbing the same moment twice costs one decode.
    Use this to check an angle at a moment the audio left ambiguous; to find where the shots
    change in b-roll, run detect_scene_cuts instead.
    """
    connection = get_connection()
    return frames.grab_frames(
        connection,
        clip,
        times,
        bin=bin,
        max_edge=max_edge,
        refresh=refresh,
    )


@tool
def detect_scene_cuts(
    clip: str,
    bin: str | None = None,  # noqa: A002 - the agent-facing word for a media pool folder
    threshold: float = scenes.DEFAULT_THRESHOLD,
    refresh: bool = False,
) -> dict[str, Any]:
    """Start a job cataloguing every scene cut in a clip — the b-roll question, answered once.

    Returns a job_id to poll with get_job. The finished result is a gist: how many cuts, how
    many shots, the shot lengths, the first few cut times — and path, the JSON catalog with
    every cut and every shot in dual time. Read or grep that file for the span you care
    about rather than asking for it all inline.

    threshold is how different two frames must be to count as a cut (0.05–1.0, default 0.4):
    lower it for footage that cuts between similar frames, raise it for handheld that pans.
    The scan decodes the whole clip, so it is cached against the media — an unchanged clip is
    never scanned twice.
    """
    connection = get_connection()
    return scenes.detect_scene_cuts(
        connection,
        clip,
        bin=bin,
        threshold=threshold,
        refresh=refresh,
    )


TOOLS: tuple[Any, ...] = (grab_frames, detect_scene_cuts)

__all__ = ["TOOLS", "detect_scene_cuts", "grab_frames"]
