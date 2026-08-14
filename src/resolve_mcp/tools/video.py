"""Video tools — seeing the picture when the audio evidence runs out.

Both routes read the file on disk the media pool points at; neither renders anything in
Resolve, so both run while the director keeps working in the GUI.
"""

from __future__ import annotations

from typing import Any

from ..resolve.connection import get_connection
from ..video import frames, occlusion, scenes
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


@tool
def analyze_occlusion(
    clip: str,
    bin: str | None = None,  # noqa: A002 - the agent-facing word for a media pool folder
    start: Any = None,
    end: Any = None,
    sample_fps: float = occlusion.DEFAULT_SAMPLE_FPS,
    threshold: float = occlusion.DEFAULT_THRESHOLD,
    refresh: bool = False,
) -> dict[str, Any]:
    """Start a job scoring how much of an angle is blocked by something in the near field.

    The question before you cut to a camera: is anyone's head, hat, phone or back in the way?
    Returns a job_id to poll with get_job. Run it over the range of a song on every angle you
    are considering, then keep your cuts out of the windows it reports.

    start and end are the clip's own frame numbers, dual time as everywhere — start=85653 or
    start={"seconds": 3572.1, "snap": "floor"} — and default to the whole clip. inspect_clip
    reports the bounds they sit inside. Frames are sampled about once a second and scored 0-1
    on near-field blocking: a large, dark or textureless blob anchored to the bottom or side
    of frame, scored above what this shot covers when nothing is in the way, and scored higher
    when it wipes in mid-shot.

    Every window comes back with a kind. obstruction means something is covering a player the
    shot is framed on — that is the veto, and obstructions is how many there are. scene means
    near-field mass the shot was framed to include: a piano lid, a head parked in a corner, the
    drummer at the edge of his own four-shot, or that furniture moved by a mid-take reframe. The
    score cannot tell those apart — a real blocking has scored below a drummer's arm — so read
    the kind, not the score, and read peak_novel and peak_hidden when you want to argue with it.

    An obstruction window spans the samples that scored, not the body's own in and out: the
    detector loses a body once it stops moving, so a crossing can outlast its window by a second
    or more at either end. Leave margin, or grab_frames the edges.

    Inline you get the worst windows — obstructions first — in, out, duration, peak and mean
    score, plus how many samples were blocked and the run's baseline. path is the JSON with the
    whole per-sample curve; read it when you need to justify or dispute a window. A score near
    the threshold is a partial block, so grab_frames the peak and look before you write the
    angle off.
    """
    connection = get_connection()
    return occlusion.analyze_occlusion(
        connection,
        clip,
        bin=bin,
        start=start,
        end=end,
        sample_fps=sample_fps,
        threshold=threshold,
        refresh=refresh,
    )


TOOLS: tuple[Any, ...] = (grab_frames, detect_scene_cuts, analyze_occlusion)

__all__ = ["TOOLS", "analyze_occlusion", "detect_scene_cuts", "grab_frames"]
