"""Timeline markers — the review loop's transport, read and written.

The director leaves notes as GUI markers on a built cut; the agent reads them as its work
queue, and writes markers back to flag its own cut decisions and uncertainties. Three
things here are decisions rather than API calls:

* **Two clocks, and only one of them is Resolve's.** ``GetMarkers`` keys a marker by a
  frame *relative to the timeline start*, while every other timeline reading in this
  server — clip positions, ranges, the cut file — is in absolute record frames. Mixing
  them puts a note an hour away from the shot it is about on any timeline that starts at
  01:00:00:00. So the translation happens once, here: callers give and get record frames,
  and each marker also carries ``frame``, Resolve's own key, for anyone reaching past this
  server with ``run_python``.
* **An existing marker is not overwritten unless asked.** ``AddMarker`` refuses a frame
  that already carries one, and the marker sitting there is usually the director's own
  note. Reporting the collision (with the marker that caused it) is the useful answer;
  ``replace`` is the explicit way to say otherwise, and because a replacement is a delete
  followed by an add that Resolve can still refuse, the displaced marker is put back if
  the add does not land. A frame already carrying *the same marker* is neither a collision
  nor a write: that is what the envelope's reconnect replay of a half-written batch looks
  like, and calling the agent's own work a collision would be a lie.
* **Colour and bounds are checked before Resolve sees them.** An unknown colour and a
  frame past the end both come back from Resolve as a bare ``False`` with no reason, which
  in a batch is indistinguishable from a locked timeline. Checking here turns both into a
  cause the agent can act on, and keeps the failure to the one entry that caused it — one
  bad marker never sinks the batch.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..errors import InvalidRequestError, ResolveMcpError, TimelineOperationError
from ..logging_config import get_logger
from ..spill import capped
from ..timing import dual_time, to_frames
from .connection import ResolveConnection
from .session import frame_rate
from .timeline import (
    Reader,
    Timeline,
    find_timeline,
    frame_window,
    open_project,
    overlaps,
    read_frames,
)

log = get_logger("markers")

# A marker with its note, both times and its custom data runs to roughly 300 characters of
# JSON, so a review round of this size sits well inside the client's reply cap. Past it the
# full set spills to disk and a colour or range filter narrows the next read.
DEFAULT_MARKER_LIMIT = 200

# Resolve's own marker colours, in its own spelling — the set the GUI offers. Anything else
# is refused by AddMarker without a word.
MARKER_COLORS = (
    "Blue",
    "Cyan",
    "Green",
    "Yellow",
    "Red",
    "Pink",
    "Purple",
    "Fuchsia",
    "Rose",
    "Lavender",
    "Sky",
    "Mint",
    "Lemon",
    "Sand",
    "Cocoa",
    "Cream",
)
_BY_LOWER_NAME = {color.lower(): color for color in MARKER_COLORS}

# Resolve reports a marker's length in frames and never below one; a zero-length marker
# would not be visible in the GUI, which is the only place a marker is for.
MINIMUM_DURATION = 1


class MarkerClock:
    """A timeline's marker clock: where it starts, how fast it runs, and both frame numbers.

    Every marker crosses between Resolve's clock and the record clock, and the crossing
    needs the same three numbers each time — so they are read once, here, and the
    conversion lives with them rather than at each call site.
    """

    def __init__(self, connection: ResolveConnection, timeline: Timeline, fps: float | None):
        self.reader = Reader(connection)
        self.timeline = timeline
        self.fps = fps
        self.name = str(timeline.GetName() or "")
        self.start = _origin(timeline)
        self.end = read_frames(timeline.GetEndFrame())

    def record(self, relative: int) -> int:
        """Resolve's marker key as an absolute record frame."""
        return self.start + relative

    def relative(self, record: int) -> int:
        """A record frame as the key Resolve wants."""
        return record - self.start


def _origin(timeline: Timeline) -> int:
    """The timeline's first frame — the offset every marker frame is counted from.

    An unreadable start would silently shift every marker, so it is logged rather than
    quietly taken as zero.
    """
    start = read_frames(timeline.GetStartFrame())
    if start is None:
        log.warning("Timeline %r reported no start frame; treating markers as absolute", timeline)
        return 0
    return start


# --- read -------------------------------------------------------------------------------


def _marker(relative: int, detail: dict[str, Any], clock: MarkerClock) -> dict[str, Any]:
    """One marker in both clocks, with the fields the GUI shows."""
    duration = read_frames(detail.get("duration")) or MINIMUM_DURATION
    record = clock.record(relative)
    return {
        "frame": relative,
        "record": dual_time(record, clock.fps),
        "end": dual_time(record + duration, clock.fps),
        "duration": dual_time(duration, clock.fps),
        "color": str(detail.get("color", "")),
        "name": str(detail.get("name", "")),
        "note": str(detail.get("note", "")),
        "custom_data": str(detail.get("customData", "")),
    }


def _reported(clock: MarkerClock) -> dict[int, dict[str, Any]]:
    """Resolve's marker map, keyed by relative frame, with unreadable keys dropped.

    A marker whose frame will not parse cannot be placed on either clock — reporting it at
    a made-up position would be worse than leaving it out and saying so in the log.

    The read goes through ``Reader``: a build without ``GetMarkers`` should read as a
    timeline with no markers, while a handle that died mid-read must still raise.
    """
    reported = clock.reader.optional(clock.timeline, "GetMarkers", None)
    if not isinstance(reported, dict):
        return {}
    markers: dict[int, dict[str, Any]] = {}
    for frame, detail in reported.items():
        relative = read_frames(frame)
        if relative is None:
            log.warning("Skipping a marker at an unreadable frame %r", frame)
            continue
        markers[relative] = detail if isinstance(detail, dict) else {}
    return markers


def _all(clock: MarkerClock) -> list[dict[str, Any]]:
    """Every readable marker on one clock, in frame order — what both readers start from."""
    return [
        _marker(relative, detail, clock) for relative, detail in sorted(_reported(clock).items())
    ]


def list_markers(
    connection: ResolveConnection,
    name: str | None = None,
    color: str | None = None,
    start: Any = None,
    end: Any = None,
    limit: int = DEFAULT_MARKER_LIMIT,
    config: Config | None = None,
) -> dict[str, Any]:
    """Every marker on a timeline, in record time, narrowed by colour and range."""
    project = open_project(connection)
    timeline = find_timeline(project, name)
    clock = MarkerClock(connection, timeline, frame_rate(project, timeline))

    markers = _all(clock)
    window = frame_window(start, end, clock.fps)
    wanted = [marker for marker in markers if _matches(marker, color) and _touches(marker, window)]

    return capped(
        {
            "timeline": {
                "name": clock.name,
                "fps": clock.fps,
                "start": dual_time(clock.start, clock.fps),
                "end": dual_time(clock.end, clock.fps),
            },
            "count": len(wanted),
            "colors": _colors(wanted),
        },
        key="markers",
        whole=wanted,
        limit=limit,
        label=f"{clock.name} markers",
        fallback="markers",
        config=config,
    )


def _touches(marker: dict[str, Any], window: tuple[int | None, int | None]) -> bool:
    """Whether a marker overlaps the range — a marker with a length is a span, not a point."""
    return overlaps(marker["record"]["frames"], marker["end"]["frames"], window)


def _matches(marker: dict[str, Any], color: str | None) -> bool:
    return color is None or marker["color"].lower() == color.lower()


def _colors(markers: list[dict[str, Any]]) -> dict[str, int]:
    """How many of each colour — a review round read at a glance."""
    counts: dict[str, int] = {}
    for marker in markers:
        counts[marker["color"]] = counts.get(marker["color"], 0) + 1
    return counts


def markers_on(
    connection: ResolveConnection,
    timeline: Timeline,
    fps: float | None,
) -> list[dict[str, Any]]:
    """Every readable marker on a timeline in record time, in frame order and uncapped.

    ``list_markers`` is the agent's view and caps at :data:`DEFAULT_MARKER_LIMIT`, spilling
    the rest to a file — right for something being read, wrong for something being *moved*,
    where a cap would silently drop the markers past the two hundredth and the timeline
    would look successfully carried. Callers moving markers take this instead.

    "Readable" is the one thing it does not promise past: a marker whose frame will not
    parse is logged and left out by :func:`_reported`, exactly as it is for ``list_markers``,
    because a marker that cannot be placed on either clock cannot be moved to a third. A
    caller reporting counts is reporting what it could read, and the log is where a
    difference between that and what the GUI shows gets diagnosed.
    """
    return _all(MarkerClock(connection, timeline, fps))


def markers_by_name(
    connection: ResolveConnection,
    timeline: Timeline,
    fps: float | None,
    color: str | None = None,
) -> dict[str, list[int]]:
    """Marker name -> every record frame carrying it, for one colour or all of them.

    A name is not unique on a timeline — nothing stops the GUI carrying two markers with
    the same note — so every frame is kept and the caller decides what a repeat means. A
    caller joining data to markers by name (titling does) has to be able to say "that
    song is marked twice" rather than silently take the first.
    """
    clock = MarkerClock(connection, timeline, fps)
    found: dict[str, list[int]] = {}
    for relative, detail in sorted(_reported(clock).items()):
        marker = _marker(relative, detail, clock)
        if not _matches(marker, color):
            continue
        found.setdefault(marker["name"], []).append(marker["record"]["frames"])
    return found


# --- write ------------------------------------------------------------------------------


def set_markers(
    connection: ResolveConnection,
    markers: list[dict[str, Any]] | None,
    name: str | None = None,
    replace: bool = False,
) -> dict[str, Any]:
    """Write a batch of markers, each reported on its own. One bad entry sinks only itself."""
    project = open_project(connection)
    timeline = find_timeline(project, name)
    clock = MarkerClock(connection, timeline, frame_rate(project, timeline))
    existing = _reported(clock)

    results: list[dict[str, Any]] = []
    for entry in markers or []:
        results.append(_write(clock, entry, existing, replace))
    added = sum(1 for result in results if result["ok"])
    log.info("Wrote %d of %d markers to %s", added, len(results), clock.name or "the open timeline")
    return {
        "timeline": clock.name,
        "results": results,
        "added": added,
        "failed": len(results) - added,
    }


def _write(
    clock: MarkerClock,
    entry: Any,
    existing: dict[int, dict[str, Any]],
    replace: bool,
) -> dict[str, Any]:
    """One marker: validated, put on Resolve's clock, written, and reported.

    A replacement is a delete followed by an add, and Resolve can refuse the add after
    taking the delete — so the marker that was there is carried through the write and put
    back if the add does not land. Losing the director's note to a failed overwrite is the
    one outcome this path must not have.
    """
    try:
        request = _request(clock, entry)
    except ResolveMcpError as exc:
        return {"ok": False, "record": None, "frame": None, "error": exc.payload()}

    relative = request["frame"]
    displaced = existing.get(relative)
    if displaced is not None:
        if _same(displaced, request):
            return _already_there(clock, relative, request)
        if not replace:
            return _occupied(clock, relative, displaced)
    if displaced is not None and not clock.timeline.DeleteMarkerAtFrame(relative):
        return _refused(
            clock,
            relative,
            f"Resolve would not delete the marker at record frame {clock.record(relative)}.",
        )
    if not _add(clock, relative, request):
        return _refused(
            clock,
            relative,
            f"Resolve refused a {request['color']} marker at record frame "
            f"{clock.record(relative)}."
            + (_restore(clock, relative, displaced) if displaced is not None else ""),
        )
    existing[relative] = _as_reported(request)
    return {
        "ok": True,
        "record": dual_time(clock.record(relative), clock.fps),
        "frame": relative,
        "color": request["color"],
        "name": request["name"],
        "replaced": displaced is not None,
        "unchanged": False,
    }


def _add(clock: MarkerClock, relative: int, request: dict[str, Any]) -> bool:
    return bool(
        clock.timeline.AddMarker(
            relative,
            request["color"],
            request["name"],
            request["note"],
            request["duration"],
            request["custom_data"],
        )
    )


def _restore(clock: MarkerClock, relative: int, displaced: dict[str, Any]) -> str:
    """Put back the marker a refused replacement deleted, and say which way it went.

    A marker Resolve reported without a colour is put back in the default one: a note in
    the wrong colour is recoverable, a note that is gone is not.
    """
    put_back = clock.timeline.AddMarker(
        relative,
        str(displaced.get("color") or MARKER_COLORS[0]),
        str(displaced.get("name") or ""),
        str(displaced.get("note") or ""),
        read_frames(displaced.get("duration")) or MINIMUM_DURATION,
        str(displaced.get("customData") or ""),
    )
    if put_back:
        return " The marker that was there has been put back."
    log.error("Could not restore the marker at relative frame %d after a refused write", relative)
    return " The marker that was there was deleted first and could not be put back."


def _same(displaced: dict[str, Any], request: dict[str, Any]) -> bool:
    """Whether the marker already there is the one being asked for, field for field."""
    return _as_reported(request) == {
        "color": str(displaced.get("color") or ""),
        "name": str(displaced.get("name") or ""),
        "note": str(displaced.get("note") or ""),
        "duration": read_frames(displaced.get("duration")) or MINIMUM_DURATION,
        "customData": str(displaced.get("customData") or ""),
    }


def _already_there(clock: MarkerClock, relative: int, request: dict[str, Any]) -> dict[str, Any]:
    """The marker asked for is already on that frame, to the letter — so nothing to do.

    This is what a batch replayed after a dropped connection looks like: the envelope
    retries the whole call, and the markers the first attempt landed are still there.
    Reporting those as collisions would accuse the agent of overwriting the director's
    notes when they are its own work, unchanged.
    """
    return {
        "ok": True,
        "record": dual_time(clock.record(relative), clock.fps),
        "frame": relative,
        "color": request["color"],
        "name": request["name"],
        "replaced": False,
        "unchanged": True,
    }


def _occupied(clock: MarkerClock, relative: int, displaced: dict[str, Any]) -> dict[str, Any]:
    """A different marker holds that frame — usually the director's own note."""
    record = clock.record(relative)
    return {
        "ok": False,
        "record": dual_time(record, clock.fps),
        "frame": relative,
        "error": InvalidRequestError(
            cause=f"A different marker is already on record frame {record}.",
            fix=(
                "That marker is probably the director's own note. Pick another frame, or "
                "pass replace=true to overwrite it deliberately."
            ),
            detail={"record": record, "frame": relative, "existing": displaced},
        ).payload(),
    }


def _as_reported(request: dict[str, Any]) -> dict[str, Any]:
    """A written marker in the shape ``GetMarkers`` would report it back."""
    return {
        "color": request["color"],
        "name": request["name"],
        "note": request["note"],
        "duration": request["duration"],
        "customData": request["custom_data"],
    }


def _refused(clock: MarkerClock, relative: int, cause: str) -> dict[str, Any]:
    return {
        "ok": False,
        "record": dual_time(clock.record(relative), clock.fps),
        "frame": relative,
        "error": TimelineOperationError(cause=cause).payload(),
    }


def _request(
    clock: MarkerClock,
    entry: Any,
) -> dict[str, Any]:
    """A marker request read into the shape ``AddMarker`` takes, or a stated refusal."""
    if not isinstance(entry, dict):
        raise InvalidRequestError(
            cause=f"{entry!r} is not a marker.",
            fix='Each marker is an object: {"frame": 1200, "color": "Blue", "note": "…"}.',
            detail={"entry": repr(entry)},
        )

    record = to_frames(entry.get("frame"), clock.fps, field="frame")
    if record is None:
        raise InvalidRequestError(
            cause="A marker was given without a frame to sit on.",
            fix=(
                "Give frame as a record frame (1200) or as seconds with a snap "
                '({"seconds": 20.0, "snap": "floor"}) — the same clock inspect_timeline reads.'
            ),
            detail={"entry": entry},
        )
    _within(clock, record)

    duration = to_frames(entry.get("duration"), clock.fps, field="duration")
    duration = MINIMUM_DURATION if duration is None else duration
    if duration < MINIMUM_DURATION:
        raise InvalidRequestError(
            cause=f"A marker duration of {duration} frames is shorter than a frame.",
            fix="Leave duration out for a single-frame marker, or give at least 1 frame.",
            detail={"duration": duration},
        )

    return {
        "frame": clock.relative(record),
        "color": _color(entry.get("color")),
        "name": str(entry.get("name") or ""),
        "note": str(entry.get("note") or ""),
        "duration": duration,
        "custom_data": str(entry.get("custom_data") or ""),
    }


def _within(clock: MarkerClock, record: int) -> None:
    """A frame outside the timeline is refused here — Resolve just drops it silently."""
    if clock.end is None:
        return
    if clock.start <= record < clock.end:
        return
    raise InvalidRequestError(
        cause=f"Record frame {record} is outside {clock.name or 'the timeline'}.",
        fix=(
            f"Markers sit inside the timeline's own range, half-open [{clock.start}, {clock.end})."
        ),
        detail={"record": record, "bounds": {"start": clock.start, "end": clock.end}},
    )


def _color(requested: Any) -> str:
    """Resolve's own spelling of a colour, whatever case it arrived in."""
    color = _BY_LOWER_NAME.get(str(requested or "").strip().lower())
    if color is None:
        raise InvalidRequestError(
            cause=f"{requested!r} is not a marker colour Resolve has.",
            fix=f"Use one of: {', '.join(MARKER_COLORS)}.",
            detail={"requested": requested, "available": list(MARKER_COLORS)},
        )
    return color
