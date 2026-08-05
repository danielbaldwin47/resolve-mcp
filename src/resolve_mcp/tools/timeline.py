"""Timeline read tools — finding the sync reference and reading what is on a cut.

Times are dual everywhere: frames are authoritative and every value comes back as frames,
seconds, timecode and fps together. A time given in seconds must say how to snap it
({"seconds": 2.52, "snap": "floor"} or "ceil"), because seconds rarely land on a frame and
this server will not choose the rounding on your behalf. Ranges are half-open [start, end),
the same convention the cut file uses.
"""

from __future__ import annotations

from typing import Any

from ..resolve import timeline as timelines
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def list_timelines(limit: int = timelines.DEFAULT_LIST_LIMIT) -> dict[str, Any]:
    """List the project's timelines with version, duration, fps and track stack.

    version is the number parsed off a "<name> v<N>" name, and latest_versions names the
    newest one per base name — the cut to carry on from. The sync reference is the timeline
    carrying an angle per video track, so the track counts are what identifies it. Past
    limit entries the full listing spills to disk and spilled_to holds the path.
    """
    connection = get_connection()
    return timelines.list_timelines(connection, limit=limit)


@tool
def inspect_timeline(
    timeline: str | None = None,
    detail: str = "tracks",
    start: Any = None,
    end: Any = None,
    limit: int = timelines.DEFAULT_ITEM_LIMIT,
) -> dict[str, Any]:
    """Read one timeline (the open one by default) at a chosen detail level and range.

    detail is summary (how long, how many tracks, how many shots), tracks (the stack, with
    names, lock and enable state) or clips (every shot in the range). start and end take
    frames (96) or seconds with an explicit snap ({"seconds": 2.52, "snap": "floor"}), and
    default to the whole timeline; a shot is in range when it overlaps it.

    Each shot carries its record position, its source position, and sync_offset — the
    difference between the two, so that timeline_frame = source_frame + sync_offset. On the
    director's stacked reference that offset is the per-angle sync, one angle per track.

    Past limit shots the reply is capped and the full reading spills to disk (spilled_to);
    narrow the range or read that file rather than raising the cap.
    """
    connection = get_connection()
    return timelines.inspect_timeline(
        connection,
        name=timeline,
        detail=detail,
        start=start,
        end=end,
        limit=limit,
    )


TOOLS: tuple[Any, ...] = (
    list_timelines,
    inspect_timeline,
)

__all__ = ["TOOLS", "inspect_timeline", "list_timelines"]
