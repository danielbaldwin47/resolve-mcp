"""Timeline read wrappers: list with versions and durations, inspect at a chosen detail.

Thin, testable, and MCP-free like the rest of this layer. Four things here are decisions
rather than API calls, and are the reason this file exists at all:

* **Versions come from the name.** ``<base> v<N>`` is the project's own convention for cut
  versions (the cut file materialises ``<name> v<N>``), and Resolve has no version concept
  of its own — so the number is parsed off the name, and a timeline that does not follow
  the convention has no version rather than a guessed one.
* **Out points are half-open ``[in, out)``, computed from start + duration.** The scripting
  docs do not settle whether a timeline item's ``GetEnd()`` is the last frame or one past
  it, and a one-frame error in a cut point is invisible until it is expensive — so
  ``GetEnd()`` is never read; ``GetDuration()`` is, and the out point follows from it. A
  timeline itself has no duration getter, so its own end has to be taken from
  ``GetEndFrame()`` as exclusive; that is the one place the reading rests on the
  convention rather than side-stepping it, and it is what the live smoke tier checks.
* **The sync offset is the mapping, not a measurement.** For a hand-synced stacked
  reference, ``timeline_frame = source_frame + sync_offset`` per angle, so the offset is
  the record start minus the source start. The server computes it and stops there: what it
  means for a project is the agent's call (spec #22 keeps the server out of sync math).
* **Detail level and range are the pagination.** A concert timeline holds thousands of
  items, far past any one reply. ``summary`` and ``tracks`` answer most questions without
  listing a single clip; past ``limit`` clips the full reading spills to disk, and the
  range narrows the next read.
"""

from __future__ import annotations

import re
from typing import Any, NamedTuple

from ..config import Config, get_config
from ..errors import (
    InvalidRequestError,
    NoProjectOpenError,
    NoTimelineOpenError,
    TimelineNotFoundError,
)
from ..logging_config import get_logger
from ..spill import spill
from ..timing import dual_time, to_frames
from .connection import ResolveConnection
from .session import frame_rate

log = get_logger("timeline")

DEFAULT_LIST_LIMIT = 100
# A shot with its record, source and offset positions in dual time runs to roughly 700
# characters of JSON, so a hundred of them sits inside the client's 25k-token reply cap
# with the heading and track stack alongside. Raising this spends the agent's whole reply
# on one read; narrowing the range is the cheaper move.
DEFAULT_ITEM_LIMIT = 100
DETAIL_LEVELS = ("summary", "tracks", "clips")
TRACK_TYPES = ("video", "audio", "subtitle")

VERSION = re.compile(r"^(?P<base>.*?)[\s_-]*v(?P<number>\d+)$", re.IGNORECASE)

Project = Any
Timeline = Any
TimelineItem = Any


class Placement(NamedTuple):
    """Where a shot sits on the timeline, read once and passed along.

    Range filtering and the shot's own reading both need these two numbers, and every read
    of them is a round trip into Resolve — so they travel together rather than being asked
    for twice.
    """

    start: int | None
    duration: int | None

    @property
    def end(self) -> int | None:
        """One past the last frame, the half-open convention the cut file uses."""
        if self.start is None or self.duration is None:
            return None
        return self.start + self.duration


# --- reaching a timeline ------------------------------------------------------------------


def _project(connection: ResolveConnection) -> Project:
    manager = connection.handle().GetProjectManager()
    project = manager.GetCurrentProject() if manager is not None else None
    if project is None:
        raise NoProjectOpenError(cause="No project is open, so there are no timelines to read.")
    return project


def _timelines(project: Project) -> list[Timeline]:
    """Every timeline the project holds, in Resolve's own order.

    ``GetTimelineByIndex`` is one-based and hands back ``None`` for an index it cannot
    resolve; a timeline that will not answer is skipped rather than sinking the listing,
    because the other timelines are still what the agent asked for.
    """
    try:
        count = int(project.GetTimelineCount() or 0)
    except (TypeError, ValueError):
        log.warning("Resolve gave an unreadable timeline count", exc_info=True)
        return []
    found = []
    for index in range(1, count + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline is None:
            log.warning("Resolve returned no timeline at index %d of %d", index, count)
            continue
        found.append(timeline)
    return found


def _name(timeline: Timeline) -> str:
    return str(timeline.GetName() or "")


def find_timeline(project: Project, name: str | None) -> Timeline:
    """The named timeline, or the open one when no name is given."""
    if name is None:
        current = project.GetCurrentTimeline()
        if current is None:
            raise NoTimelineOpenError(
                cause="No timeline is open in the project, and none was named.",
            )
        return current
    held = _timelines(project)
    for timeline in held:
        if _name(timeline) == name:
            return timeline
    raise TimelineNotFoundError(name, [_name(timeline) for timeline in held])


def version_of(name: str) -> tuple[str, int | None]:
    """Split ``"sunset-set v3"`` into its base name and version number."""
    match = VERSION.match(name)
    if match is None:
        return name, None
    return match.group("base"), int(match.group("number"))


# --- shape ---------------------------------------------------------------------------------


def _bounds(timeline: Timeline, fps: float | None) -> dict[str, Any]:
    start = _frames(timeline.GetStartFrame())
    end = _frames(timeline.GetEndFrame())
    duration = end - start if start is not None and end is not None else None
    return {
        "start": dual_time(start, fps),
        "end": dual_time(end, fps),
        "duration": dual_time(duration, fps),
    }


def _frames(value: Any) -> int | None:
    """A frame number as Resolve reports it — sometimes a string, sometimes nothing.

    Only the parsing is forgiving. A getter that *raises* is left to raise: that is what a
    handle dying mid-read looks like, and swallowing it here would turn a lost connection
    into a half-empty reading the agent has no reason to distrust.
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _optional(getter: Any, default: Any, *args: Any) -> Any:
    """Read something this Resolve build may not offer; the default is not a failure.

    Getters come and go between versions and a few refuse on particular clip kinds, so a
    reading that cannot be taken falls back rather than sinking the whole inspection.
    """
    if getter is None:
        return default
    try:
        return getter(*args)
    except Exception:  # noqa: BLE001 - one unreadable field is not a lost timeline
        log.debug("Could not read %s%s", getattr(getter, "__name__", getter), args, exc_info=True)
        return default


def _track_counts(timeline: Timeline) -> dict[str, int]:
    counts = {}
    for track_type in TRACK_TYPES:
        try:
            counts[track_type] = int(_optional(timeline.GetTrackCount, 0, track_type) or 0)
        except (TypeError, ValueError):
            log.debug("Unreadable %s track count", track_type, exc_info=True)
            counts[track_type] = 0
    return counts


def summarise(timeline: Timeline, project: Project, current: str | None) -> dict[str, Any]:
    """The one-line view of a timeline that list and inspect both open with."""
    name = _name(timeline)
    base, version = version_of(name)
    fps = frame_rate(project, timeline)
    return {
        "name": name,
        "base_name": base,
        "version": version,
        "current": name == current,
        "fps": fps,
        **_bounds(timeline, fps),
        "tracks": _track_counts(timeline),
    }


# --- list ------------------------------------------------------------------------------------


def list_timelines(
    connection: ResolveConnection,
    limit: int = DEFAULT_LIST_LIMIT,
    config: Config | None = None,
) -> dict[str, Any]:
    """Every timeline in the project with its version, duration and track stack."""
    project = _project(connection)
    current = project.GetCurrentTimeline()
    current_name = _name(current) if current is not None else None

    timelines = [summarise(timeline, project, current_name) for timeline in _timelines(project)]

    cap = max(int(limit), 0)
    truncated = len(timelines) > cap
    result: dict[str, Any] = {
        "count": len(timelines),
        "current": current_name,
        "timelines": timelines[:cap] if truncated else timelines,
        "latest_versions": _latest_versions(timelines),
        "truncated": truncated,
        "spilled_to": None,
    }
    if truncated:
        full = {**result, "timelines": timelines, "truncated": False, "spilled_to": None}
        result["spilled_to"] = spill("timelines", full, config or get_config(), "timeline")
    return result


def _latest_versions(timelines: list[dict[str, Any]]) -> dict[str, str]:
    """The newest numbered timeline per base name — the answer to "where did I get to"."""
    latest: dict[str, dict[str, Any]] = {}
    for entry in timelines:
        if entry["version"] is None:
            continue
        best = latest.get(entry["base_name"])
        if best is None or entry["version"] > best["version"]:
            latest[entry["base_name"]] = entry
    return {base: entry["name"] for base, entry in latest.items()}


# --- inspect ----------------------------------------------------------------------------------


def inspect_timeline(
    connection: ResolveConnection,
    name: str | None = None,
    detail: str = "tracks",
    start: Any = None,
    end: Any = None,
    limit: int = DEFAULT_ITEM_LIMIT,
    config: Config | None = None,
) -> dict[str, Any]:
    """Read one timeline at a chosen detail level, over a chosen range."""
    if detail not in DETAIL_LEVELS:
        raise InvalidRequestError(
            cause=f"{detail!r} is not a detail level.",
            fix=(
                "Use summary (how long, how many tracks), tracks (the stack, named) or "
                "clips (every shot in the range)."
            ),
            detail={"requested": detail, "available": list(DETAIL_LEVELS)},
        )

    project = _project(connection)
    timeline = find_timeline(project, name)
    current = project.GetCurrentTimeline()
    heading = summarise(timeline, project, _name(current) if current is not None else None)
    fps = heading["fps"]

    window = _window(timeline, start, end, fps)
    with_items = detail == "clips"
    tracks = [
        _read_track(timeline, track_type, index, window, fps, with_items)
        for track_type in TRACK_TYPES
        for index in range(1, heading["tracks"][track_type] + 1)
    ]
    item_count = sum(track["item_count"] for track in tracks)

    result: dict[str, Any] = {
        "timeline": {**heading, "markers": _marker_count(timeline)},
        "detail": detail,
        "range": {"in": dual_time(window[0], fps), "out": dual_time(window[1], fps)},
        "tracks": None if detail == "summary" else [_without_items(track) for track in tracks],
        "item_count": item_count,
        "truncated": False,
        "spilled_to": None,
    }
    if not with_items:
        return result

    cap = max(int(limit), 0)
    result["tracks"] = _capped(tracks, cap)
    if item_count > cap:
        result["truncated"] = True
        full = {**result, "tracks": tracks, "truncated": False, "spilled_to": None}
        result["spilled_to"] = spill(heading["name"], full, config or get_config(), "timeline")
    return result


def _window(timeline: Timeline, start: Any, end: Any, fps: float | None) -> tuple[int, int]:
    """The range to read, half-open ``[in, out)``, defaulting to the whole timeline."""
    asked_start = to_frames(start, fps, field="start")
    asked_end = to_frames(end, fps, field="end")
    first = asked_start if asked_start is not None else (_frames(timeline.GetStartFrame()) or 0)
    last = asked_end if asked_end is not None else _frames(timeline.GetEndFrame())
    if last is None:
        last = first
    if last < first:
        raise InvalidRequestError(
            cause=f"The range ends at {last} but starts at {first}.",
            fix="Ranges are half-open [start, end) and run forwards; swap the two.",
            detail={"start": first, "end": last},
        )
    return first, last


def _read_track(
    timeline: Timeline,
    track_type: str,
    index: int,
    window: tuple[int, int],
    fps: float | None,
    with_items: bool,
) -> dict[str, Any]:
    """One track, and the shots on it that touch the range.

    The range is applied to raw frame numbers before anything is shaped: a concert track
    holds thousands of shots, and building the dual-time reading of each one only to throw
    it away is the difference between a quick read and a slow one.
    """
    in_range = [
        (item, placement)
        for item, placement in (
            (item, _placement(item)) for item in _items_in_track(timeline, track_type, index)
        )
        if _touches(placement, window)
    ]
    return {
        "type": track_type,
        "index": index,
        "name": str(_optional(timeline.GetTrackName, "", track_type, index) or ""),
        "enabled": bool(_optional(timeline.GetIsTrackEnabled, True, track_type, index)),
        "locked": bool(_optional(timeline.GetIsTrackLocked, False, track_type, index)),
        "item_count": len(in_range),
        "items": (
            [read_item(item, track_type, index, fps, placement) for item, placement in in_range]
            if with_items
            else []
        ),
    }


def _without_items(track: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in track.items() if key != "items"}


def _capped(tracks: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Keep the first ``cap`` items across the whole stack, tracks in order."""
    left = cap
    capped = []
    for track in tracks:
        kept = track["items"][:left]
        left -= len(kept)
        capped.append({**track, "items": kept})
    return capped


def _touches(placement: Placement, window: tuple[int, int]) -> bool:
    """Whether a shot overlaps the range at all — an edge that only meets it does not.

    A shot whose position cannot be read is kept: dropping it would quietly shorten the
    reading of a cut, which is worse than one entry the agent has to look at twice.
    """
    first, last = window
    if placement.start is None or placement.end is None:
        return True
    return bool(placement.start < last and placement.end > first)


def _items_in_track(timeline: Timeline, track_type: str, index: int) -> list[TimelineItem]:
    """The shots on one track. An empty track answers ``None`` rather than an empty list."""
    return list(timeline.GetItemListInTrack(track_type, index) or [])


def _marker_count(timeline: Timeline) -> int:
    markers = _optional(timeline.GetMarkers, None)
    return len(markers) if isinstance(markers, dict) else 0


def _placement(item: TimelineItem) -> Placement:
    """Where a shot sits, in the two numbers everything else is derived from."""
    return Placement(_frames(item.GetStart()), _frames(item.GetDuration()))


def read_item(
    item: TimelineItem,
    track_type: str,
    index: int,
    fps: float | None,
    placement: Placement,
) -> dict[str, Any]:
    """One shot: where it sits, where it came from, and the offset between the two."""
    record_in, duration = placement.start, placement.duration
    record_out = placement.end
    source_in = _source_start(item)
    source_out = source_in + duration if source_in is not None and duration is not None else None
    offset = record_in - source_in if record_in is not None and source_in is not None else None
    return {
        "name": str(item.GetName() or ""),
        "track": {"type": track_type, "index": index},
        "record": {
            "in": dual_time(record_in, fps),
            "out": dual_time(record_out, fps),
            "duration": dual_time(duration, fps),
        },
        "source": {"in": dual_time(source_in, fps), "out": dual_time(source_out, fps)},
        "sync_offset": dual_time(offset, fps),
        "clip": _clip_name(item),
        "enabled": bool(_optional(getattr(item, "GetClipEnabled", None), True)),
        "takes": int(_optional(getattr(item, "GetTakeCount", None), 0) or 0),
    }


def _source_start(item: TimelineItem) -> int | None:
    """Where the shot starts in its own media.

    ``GetSourceStartFrame`` is the direct answer and only exists on newer builds;
    ``GetLeftOffset`` carries the same number counted from the media's first frame, which
    is what an offset against a common clock needs either way.
    """
    for getter in ("GetSourceStartFrame", "GetLeftOffset"):
        frames = _frames(_optional(getattr(item, getter, None), None))
        if frames is not None:
            return frames
    return None


def _clip_name(item: TimelineItem) -> str | None:
    """The media pool clip a shot came from — a generator or title has none."""
    clip = _optional(item.GetMediaPoolItem, None)
    if clip is None:
        return None
    name = _optional(clip.GetName, None)
    return None if name is None else str(name)
