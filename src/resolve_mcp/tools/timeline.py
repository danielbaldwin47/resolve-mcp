"""Timeline read tools — finding the sync reference and reading what is on a cut.

Times are dual everywhere: frames are authoritative and every value comes back as frames,
seconds, timecode and fps together. A time given in seconds must say how to snap it
({"seconds": 2.52, "snap": "floor"} or "ceil"), because seconds rarely land on a frame and
this server will not choose the rounding on your behalf. Ranges are half-open [start, end),
the same convention the cut file uses.
"""

from __future__ import annotations

from typing import Any

from ..resolve import markers as marker_wrapper
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


@tool
def list_markers(
    timeline: str | None = None,
    color: str | None = None,
    start: Any = None,
    end: Any = None,
    limit: int = marker_wrapper.DEFAULT_MARKER_LIMIT,
) -> dict[str, Any]:
    """Read a timeline's markers — the director's review notes — in record time.

    Each marker carries record (where it sits on the timeline, dual time), end (record plus
    its length, half-open), color, name, note and custom_data. frame is Resolve's own key
    for the marker, counted from the timeline start rather than from zero; record is the
    number to plan against, the same clock inspect_timeline reads.

    color narrows to one colour (any case), start and end to a record range — frames (1200)
    or seconds with an explicit snap. colors counts what came back, which is the shape of a
    review round. Past limit markers the full set spills to disk (spilled_to).
    """
    connection = get_connection()
    return marker_wrapper.list_markers(
        connection,
        name=timeline,
        color=color,
        start=start,
        end=end,
        limit=limit,
    )


@tool
def set_markers(
    markers: list[dict[str, Any]],
    timeline: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Write markers onto a timeline — cut decisions, uncertainties, proposed song starts.

    Each entry is {"frame": 1200, "color": "Blue", "name": "…", "note": "…"}, with optional
    duration (frames, 1 by default) and custom_data. frame is a record frame — frames or
    seconds with a snap — on the same clock list_markers and inspect_timeline report; this
    server translates it to the frame Resolve keys markers by.

    Colour must be one Resolve has (Blue, Cyan, Green, Yellow, Red, Pink, Purple, Fuchsia,
    Rose, Lavender, Sky, Mint, Lemon, Sand, Cocoa, Cream). A frame that already carries a
    different marker is refused with that marker in the error — it is usually the director's
    own note — unless replace is true; a frame already carrying this exact marker comes back
    ok with unchanged: true. Every entry is reported separately; one bad entry never sinks
    the batch.
    """
    connection = get_connection()
    return marker_wrapper.set_markers(connection, markers, name=timeline, replace=replace)


TOOLS: tuple[Any, ...] = (
    list_timelines,
    inspect_timeline,
    list_markers,
    set_markers,
)

__all__ = ["TOOLS", "inspect_timeline", "list_markers", "list_timelines", "set_markers"]
