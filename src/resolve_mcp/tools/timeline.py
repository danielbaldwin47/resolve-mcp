"""Timeline read tools — finding the sync reference and reading what is on a cut.

Times are dual everywhere: frames are authoritative and every value comes back as frames,
seconds, timecode and fps together. A time given in seconds must say how to snap it
({"seconds": 2.52, "snap": "floor"} or "ceil"), because seconds rarely land on a frame and
this server will not choose the rounding on your behalf. Ranges are half-open [start, end),
the same convention the cut file uses.
"""

from __future__ import annotations

from typing import Any

from ..resolve import interchange
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
def export_timeline(
    timeline: str | None = None,
    format: str = interchange.DEFAULT_FORMAT,  # noqa: A002 - the agent-facing word
    path: str | None = None,
) -> dict[str, Any]:
    """Write a timeline (the open one by default) out as otio, fcpxml or drt.

    This is the structural escape hatch: the scripting API cannot cut a transition, so a
    dissolve is made by exporting to otio, editing the transition into that document by
    hand, and importing it back with import_timeline — which materialises it as a real
    transition. Use fcpxml to hand the cut to another NLE, drt for a Resolve-native copy.

    Without a path the file lands in the server's cache under a timestamped name, and path
    holds where it went. An explicit path keeps its folder but gets the format's own
    suffix. export_type names the Resolve export constant used — fcpxml is versioned and
    the newest one this build offers is written.
    """
    connection = get_connection()
    return interchange.export_timeline(connection, name=timeline, export_format=format, path=path)


@tool
def import_timeline(
    path: str,
    name: str | None = None,
    import_source_clips: bool = True,
    source_media_path: str | None = None,
) -> dict[str, Any]:
    """Materialise a *new* timeline from an otio, fcpxml or drt file. Nothing is overwritten.

    The name defaults to the file's own stem; a name already in the project is never
    reused — the import lands on the next free version of it ("sunset-set v3" becomes
    "sunset-set v5" when v4 is taken), and the reply gives requested_name alongside the
    timeline that was actually made, with renamed saying whether the two differ.

    Source clips are imported by default, which is what an OTIO round trip needs to relink
    to media; pass source_media_path when the media sits somewhere other than where the
    document says. The reply is the new timeline's heading — version, fps, bounds, track
    stack; inspect_timeline reads what is on it.

    A .drt is Resolve's own document and accepts none of these: it names its own timeline
    and carries its own media links, so name and source_media_path are refused for one
    rather than quietly ignored, and requested_name comes back null. The result is still
    checked against the timelines the project already had, so a .drt cannot land on one.
    """
    connection = get_connection()
    return interchange.import_timeline(
        connection,
        path,
        name=name,
        import_source_clips=import_source_clips,
        source_media_path=source_media_path,
    )


TOOLS: tuple[Any, ...] = (
    list_timelines,
    inspect_timeline,
    export_timeline,
    import_timeline,
)

__all__ = [
    "TOOLS",
    "export_timeline",
    "import_timeline",
    "inspect_timeline",
    "list_timelines",
]
