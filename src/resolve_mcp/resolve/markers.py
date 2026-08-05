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
  ``replace`` is the explicit way to say otherwise.
* **Colour and bounds are checked before Resolve sees them.** An unknown colour and a
  frame past the end both come back from Resolve as a bare ``False`` with no reason, which
  in a batch is indistinguishable from a locked timeline. Checking here turns both into a
  cause the agent can act on, and keeps the failure to the one entry that caused it — one
  bad marker never sinks the batch.
"""

from __future__ import annotations

from typing import Any

from ..config import Config, get_config
from ..errors import InvalidRequestError, ResolveMcpError, TimelineOperationError
from ..logging_config import get_logger
from ..spill import spill
from ..timing import dual_time, to_frames
from .connection import ResolveConnection
from .session import frame_rate
from .timeline import Timeline, find_timeline, open_project, read_frames

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


class Placed:
    """One timeline's marker plumbing: where it starts, how fast it runs, what it holds."""

    def __init__(self, timeline: Timeline, fps: float | None) -> None:
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


def _marker(relative: int, detail: dict[str, Any], placed: Placed) -> dict[str, Any]:
    """One marker in both clocks, with the fields the GUI shows."""
    duration = read_frames(detail.get("duration")) or MINIMUM_DURATION
    record = placed.record(relative)
    return {
        "frame": relative,
        "record": dual_time(record, placed.fps),
        "end": dual_time(record + duration, placed.fps),
        "duration": dual_time(duration, placed.fps),
        "color": str(detail.get("color", "")),
        "name": str(detail.get("name", "")),
        "note": str(detail.get("note", "")),
        "custom_data": str(detail.get("customData", "")),
    }


def _reported(timeline: Timeline) -> dict[int, dict[str, Any]]:
    """Resolve's marker map, keyed by relative frame, with unreadable keys dropped.

    A marker whose frame will not parse cannot be placed on either clock — reporting it at
    a made-up position would be worse than leaving it out and saying so in the log.
    """
    reported = timeline.GetMarkers()
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
    placed = Placed(timeline, frame_rate(project, timeline))

    markers = [
        _marker(relative, detail, placed)
        for relative, detail in sorted(_reported(timeline).items())
    ]
    window = _window(start, end, placed.fps)
    wanted = [marker for marker in markers if _matches(marker, color) and _touches(marker, window)]

    cap = max(int(limit), 0)
    truncated = len(wanted) > cap
    result: dict[str, Any] = {
        "timeline": {
            "name": placed.name,
            "fps": placed.fps,
            "start": dual_time(placed.start, placed.fps),
            "end": dual_time(placed.end, placed.fps),
        },
        "count": len(wanted),
        "markers": wanted[:cap] if truncated else wanted,
        "colors": _colors(wanted),
        "truncated": truncated,
        "spilled_to": None,
    }
    if truncated:
        full = {**result, "markers": wanted, "truncated": False, "spilled_to": None}
        result["spilled_to"] = spill(
            f"{placed.name} markers", full, config or get_config(), fallback="markers"
        )
    return result


def _window(start: Any, end: Any, fps: float | None) -> tuple[int | None, int | None]:
    """The record range to read, half-open ``[start, end)``, open at either end."""
    first = to_frames(start, fps, field="start")
    last = to_frames(end, fps, field="end")
    if first is not None and last is not None and last < first:
        raise InvalidRequestError(
            cause=f"The range ends at {last} but starts at {first}.",
            fix="Ranges are half-open [start, end) and run forwards; swap the two.",
            detail={"start": first, "end": last},
        )
    return first, last


def _touches(marker: dict[str, Any], window: tuple[int | None, int | None]) -> bool:
    """Whether a marker overlaps the range — a marker with a length is a span, not a point."""
    first, last = window
    record = (marker["record"] or {}).get("frames")
    finish = (marker["end"] or {}).get("frames")
    if record is None or finish is None:
        return True
    return (last is None or record < last) and (first is None or finish > first)


def _matches(marker: dict[str, Any], color: str | None) -> bool:
    return color is None or marker["color"].lower() == color.lower()


def _colors(markers: list[dict[str, Any]]) -> dict[str, int]:
    """How many of each colour — a review round read at a glance."""
    counts: dict[str, int] = {}
    for marker in markers:
        counts[marker["color"]] = counts.get(marker["color"], 0) + 1
    return counts


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
    placed = Placed(timeline, frame_rate(project, timeline))
    held = _reported(timeline)

    results: list[dict[str, Any]] = []
    for entry in markers or []:
        results.append(_write(placed, entry, held, replace))
    added = sum(1 for result in results if result["ok"])
    log.info(
        "Wrote %d of %d markers to %s", added, len(results), placed.name or "the open timeline"
    )
    return {
        "timeline": placed.name,
        "results": results,
        "added": added,
        "failed": len(results) - added,
    }


def _write(
    placed: Placed,
    entry: Any,
    held: dict[int, dict[str, Any]],
    replace: bool,
) -> dict[str, Any]:
    """One marker: validated, placed on Resolve's clock, written, and reported."""
    try:
        request = _request(placed, entry, held, replace)
    except ResolveMcpError as exc:
        return {"ok": False, "record": None, "frame": None, "error": exc.payload()}

    relative = request["frame"]
    replaced = relative in held
    if replaced and not placed.timeline.DeleteMarkerAtFrame(relative):
        return _refused(
            placed,
            relative,
            f"Resolve would not delete the marker at record frame {placed.record(relative)}.",
        )
    added = placed.timeline.AddMarker(
        relative,
        request["color"],
        request["name"],
        request["note"],
        request["duration"],
        request["custom_data"],
    )
    if not added:
        return _refused(
            placed,
            relative,
            f"Resolve refused a {request['color']} marker at record frame "
            f"{placed.record(relative)}.",
        )
    held[relative] = {
        "color": request["color"],
        "name": request["name"],
        "note": request["note"],
        "duration": request["duration"],
        "customData": request["custom_data"],
    }
    return {
        "ok": True,
        "record": dual_time(placed.record(relative), placed.fps),
        "frame": relative,
        "color": request["color"],
        "name": request["name"],
        "replaced": replaced,
    }


def _refused(placed: Placed, relative: int, cause: str) -> dict[str, Any]:
    return {
        "ok": False,
        "record": dual_time(placed.record(relative), placed.fps),
        "frame": relative,
        "error": TimelineOperationError(cause=cause).payload(),
    }


def _request(
    placed: Placed,
    entry: Any,
    held: dict[int, dict[str, Any]],
    replace: bool,
) -> dict[str, Any]:
    """A marker request read into the shape ``AddMarker`` takes, or a stated refusal."""
    if not isinstance(entry, dict):
        raise InvalidRequestError(
            cause=f"{entry!r} is not a marker.",
            fix='Each marker is an object: {"frame": 1200, "color": "Blue", "note": "…"}.',
            detail={"entry": repr(entry)},
        )

    record = to_frames(entry.get("frame"), placed.fps, field="frame")
    if record is None:
        raise InvalidRequestError(
            cause="A marker was given without a frame to sit on.",
            fix=(
                "Give frame as a record frame (1200) or as seconds with a snap "
                '({"seconds": 20.0, "snap": "floor"}) — the same clock inspect_timeline reads.'
            ),
            detail={"entry": entry},
        )
    _within(placed, record)

    duration = to_frames(entry.get("duration"), placed.fps, field="duration")
    duration = MINIMUM_DURATION if duration is None else duration
    if duration < MINIMUM_DURATION:
        raise InvalidRequestError(
            cause=f"A marker duration of {duration} frames is shorter than a frame.",
            fix="Leave duration out for a single-frame marker, or give at least 1 frame.",
            detail={"duration": duration},
        )

    relative = placed.relative(record)
    if relative in held and not replace:
        raise InvalidRequestError(
            cause=f"A marker is already on record frame {record}.",
            fix=(
                "That marker is probably the director's own note. Pick another frame, or "
                "pass replace=true to overwrite it deliberately."
            ),
            detail={"record": record, "frame": relative, "existing": held[relative]},
        )

    return {
        "frame": relative,
        "color": _color(entry.get("color")),
        "name": str(entry.get("name") or ""),
        "note": str(entry.get("note") or ""),
        "duration": duration,
        "custom_data": str(entry.get("custom_data") or ""),
    }


def _within(placed: Placed, record: int) -> None:
    """A frame outside the timeline is refused here — Resolve just drops it silently."""
    if placed.end is None:
        return
    if placed.start <= record < placed.end:
        return
    raise InvalidRequestError(
        cause=f"Record frame {record} is outside {placed.name or 'the timeline'}.",
        fix=(
            f"Markers sit inside the timeline's own range, half-open "
            f"[{placed.start}, {placed.end})."
        ),
        detail={"record": record, "bounds": {"start": placed.start, "end": placed.end}},
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
