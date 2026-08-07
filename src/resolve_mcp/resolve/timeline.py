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

import contextlib
import hashlib
import re
from collections.abc import Iterator
from typing import Any, Final, NamedTuple

from ..config import Config, get_config
from ..errors import (
    InvalidRequestError,
    NoTimelineOpenError,
    TimelineNotFoundError,
)
from ..logging_config import get_logger
from ..spill import spill
from ..timing import dual_time, to_frames
from .connection import ResolveConnection
from .session import current_project, frame_rate

log = get_logger("timeline")

DEFAULT_LIST_LIMIT = 100
# A shot with its record, source and offset positions in dual time runs to roughly 700
# characters of JSON, so a hundred of them sits inside the client's 25k-token reply cap
# with the heading and track stack alongside. Raising this spends the agent's whole reply
# on one read; narrowing the range is the cheaper move.
DEFAULT_ITEM_LIMIT = 100
DETAIL_LEVELS = ("summary", "tracks", "clips")
TRACK_TYPES = ("video", "audio", "subtitle")

CLIP_TYPE = "Type"
"""The media pool property naming what kind of clip it is."""

MULTICAM_TYPE = "Multicam"
"""The one clip kind whose angles all share a single pool item — see :func:`angle_name`."""

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


def open_project(connection: ResolveConnection) -> Project:
    """The open project, or a structured refusal — never a ``None`` to trip over later."""
    return current_project(connection, "No project is open, so there are no timelines to read.")


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


def name_of(timeline: Timeline) -> str:
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
        if name_of(timeline) == name:
            return timeline
    raise TimelineNotFoundError(name, [name_of(timeline) for timeline in held])


def timeline_names(project: Project) -> list[str]:
    """Every timeline name the project holds — what a new name must not collide with."""
    return [name_of(timeline) for timeline in _timelines(project)]


def current_name(project: Project) -> str | None:
    """The open timeline's name, or ``None`` when the project has nothing open."""
    current = project.GetCurrentTimeline()
    return name_of(current) if current is not None else None


def version_of(name: str) -> tuple[str, int | None]:
    """Split ``"sunset-set v3"`` into its base name and version number."""
    match = VERSION.match(name)
    if match is None:
        return name, None
    return match.group("base"), int(match.group("number"))


FIRST_TRACK: Final = 1
"""A sequential V1 cut is one video track and one audio track: both the first of their kind.

Where the build places and where a swap looks have to be the same track, so the index is
named once rather than written as a literal 1 in each of them.
"""


def start_frame(timeline: Timeline) -> int:
    """The timeline's own first frame — an hour of timecode on a normal project.

    Every absolute position counts from here, because ``recordFrame`` is *not* clamped to
    the timeline's span (#18 (d)): a cut frame of 0 sent raw would land before it begins.
    An unreadable answer counts from 0 rather than stopping, and says so in the log.
    """
    frames = read_frames(timeline.GetStartFrame())
    if frames is None:
        log.warning("Resolve gave an unreadable start frame; counting from 0")
        return 0
    return frames


def next_free_name(requested: str, existing: set[str]) -> str:
    """A name no timeline in the project answers to, following ``<base> v<N>``.

    The project's own convention for a new cut made from an old one is the next version
    number, so a collision walks that sequence rather than inventing a suffix of its own —
    and an unversioned name starts the sequence at v2, which reads as what it is: the
    second thing to carry that name.
    """
    if requested not in existing:
        return requested
    base, version = version_of(requested)
    number = (version or 1) + 1
    while f"{base} v{number}" in existing:
        number += 1
    return f"{base} v{number}"


def same_timeline(one: Timeline | None, other: Timeline | None) -> bool:
    """Whether two handles are one timeline, by Resolve's own id.

    Two proxies for the same timeline are not the same Python object — ``GetCurrentTimeline``
    and ``GetTimelineByIndex`` hand back different ones — so identity has to be asked of
    Resolve. ``GetUniqueId`` predates neither build this runs on, but a build without it
    falls back to the name, which is unique within a project because this server refuses to
    create a colliding one.
    """
    if one is None or other is None:
        return False
    first, second = getattr(one, "GetUniqueId", None), getattr(other, "GetUniqueId", None)
    if callable(first) and callable(second):
        return bool(first() == second())
    return bool(name_of(one) == name_of(other))


@contextlib.contextmanager
def current_timeline(project: Project, timeline: Timeline) -> Iterator[None]:
    """Work on the timeline that was asked for, and put the director's back afterwards.

    The render queue renders the *current* timeline, so exporting any other one means
    switching. Leaving the switch in place would move the GUI out from under whoever is
    sitting at it.
    """
    previous = project.GetCurrentTimeline()
    switched = previous is not timeline
    if switched:
        project.SetCurrentTimeline(timeline)
    try:
        yield
    finally:
        if switched and previous is not None:
            project.SetCurrentTimeline(previous)


def fingerprint(reader: Reader, timeline: Timeline) -> dict[str, Any]:
    """A timeline's identity, as far as anything outside Resolve can read it.

    Every job that takes a whole timeline as its input — an audio export, a render — keys
    its cache off this, so it lives with the other timeline reads rather than with either
    caller.

    Bounds and track counts alone would call a take swap or a reordered cut "unchanged" —
    same duration, same stack — and hand back yesterday's output, so the shots themselves
    are digested too. What no reading can see is a clip's audio level, which the scripting
    API does not expose at all; that is what ``refresh`` on the starters is for.

    Nothing here smooths a failure into a default: a field that cannot be read while
    Resolve is dying must not quietly produce a fingerprint, because every dead-handle
    reading would collide on one key and serve one concert's output for another.
    """
    return {
        "name": str(timeline.GetName()),
        "unique_id": reader.optional(timeline, "GetUniqueId", None),
        "start": timeline.GetStartFrame(),
        "end": timeline.GetEndFrame(),
        "audio_tracks": timeline.GetTrackCount("audio"),
        "video_tracks": timeline.GetTrackCount("video"),
        "structure": _structure(reader, timeline),
    }


def _structure(reader: Reader, timeline: Timeline) -> str:
    """A digest of every shot on the cut: what it is, where it starts, how long it runs."""
    digest = hashlib.sha256()
    for track_type in ("video", "audio"):
        count = int(timeline.GetTrackCount(track_type) or 0)
        for index in range(1, count + 1):
            items = reader.optional(timeline, "GetItemListInTrack", [], track_type, index) or []
            digest.update(f"{track_type}{index}:".encode())
            for item in items:
                name = reader.optional(item, "GetName", "")
                start = reader.optional(item, "GetStart", None)
                duration = reader.optional(item, "GetDuration", None)
                digest.update(f"{name}@{start}+{duration};".encode())
    return digest.hexdigest()


# --- shape ---------------------------------------------------------------------------------


def _bounds(timeline: Timeline, fps: float | None) -> dict[str, Any]:
    start = read_frames(timeline.GetStartFrame())
    end = read_frames(timeline.GetEndFrame())
    duration = end - start if start is not None and end is not None else None
    return {
        "start": dual_time(start, fps),
        "end": dual_time(end, fps),
        "duration": dual_time(duration, fps),
    }


def read_frames(value: Any) -> int | None:
    """A frame number as Resolve reports it — sometimes a string, sometimes nothing.

    Only the parsing is forgiving. A getter that *raises* is left to raise: that is what a
    handle dying mid-read looks like, and swallowing it here would turn a lost connection
    into a half-empty reading the agent has no reason to distrust.
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class Reader:
    """Reads fields that may not answer, degrading only while Resolve is still there.

    Two failures look identical at the call site and must not be treated alike. A getter
    that refuses on one clip kind — a Fusion title has no source frames, an old build has
    no getter at all — should cost that one field and nothing more. A getter that fails
    because Resolve went away must not be smoothed into a plausible default: the agent
    would have a whole timeline of them and no reason to distrust a single one.

    The exception cannot tell them apart, so this asks the connection instead. A dead
    handle re-raises, which is what the tool layer's single reconnect exists for. The
    asking costs the same cheap version probe the connection already makes before every
    call, and only on the path where a read has already failed.
    """

    def __init__(self, connection: ResolveConnection, current: bool = True) -> None:
        self._connection = connection
        self._current = current

    @property
    def reads_current(self) -> bool:
        """Whether editor-state getters can be believed on this reader's timeline (#84).

        A handful of getters are backed by editor state rather than timeline data, and for
        a timeline that is not the project's current one they answer the falsy value of
        their own type — ``GetTakesCount`` zero, ``GetIsTrackEnabled`` and
        ``GetIsTrackLocked`` false — with no error and no ``None``. A caller cannot tell
        that apart from a genuinely empty selector or a genuinely muted track, so reporting
        the number is reporting a wrong answer confidently.

        The caller asks *this* rather than looking for ``None`` in the answer, because
        Resolve returns ``None`` from these getters for its own unrelated reasons — a clip
        kind with no selector — and that has always meant zero. Reading "unknown" out of
        the value would put the two back together one line after the reader separated them.

        Defaults to trusting: every other reader in this package works on a timeline it has
        already made current, and only the read that deliberately does not switch sets
        ``current=False``.
        """
        return self._current

    def optional(self, target: Any, method: str, default: Any, *args: Any) -> Any:
        getter = getattr(target, method, None)
        if getter is None:
            return default
        try:
            return getter(*args)
        except Exception:
            if self._connection.dropped():
                log.info("Resolve went away while reading %s", method)
                raise
            log.debug("Resolve would not answer %s%s", method, args, exc_info=True)
            return default


def _track_counts(reader: Reader, timeline: Timeline) -> dict[str, int]:
    """How many tracks of each kind — the stack that identifies a sync reference."""
    return {
        track_type: read_frames(reader.optional(timeline, "GetTrackCount", 0, track_type)) or 0
        for track_type in TRACK_TYPES
    }


def summarise(
    reader: Reader,
    timeline: Timeline,
    project: Project,
    current: str | None,
) -> dict[str, Any]:
    """The one-line view of a timeline that list and inspect both open with."""
    name = name_of(timeline)
    base, version = version_of(name)
    fps = frame_rate(project, timeline)
    return {
        "name": name,
        "base_name": base,
        "version": version,
        "current": name == current,
        "fps": fps,
        **_bounds(timeline, fps),
        "tracks": _track_counts(reader, timeline),
    }


# --- list ------------------------------------------------------------------------------------


def list_timelines(
    connection: ResolveConnection,
    limit: int = DEFAULT_LIST_LIMIT,
    config: Config | None = None,
) -> dict[str, Any]:
    """Every timeline in the project with its version, duration and track stack."""
    project = open_project(connection)
    reader = Reader(connection)
    current = current_name(project)

    timelines = [summarise(reader, timeline, project, current) for timeline in _timelines(project)]

    cap = max(int(limit), 0)
    truncated = len(timelines) > cap
    result: dict[str, Any] = {
        "count": len(timelines),
        "current": current,
        "timelines": timelines[:cap] if truncated else timelines,
        "latest_versions": _latest_versions(timelines),
        "truncated": truncated,
        "spilled_to": None,
    }
    if truncated:
        full = {**result, "timelines": timelines, "truncated": False, "spilled_to": None}
        result["spilled_to"] = spill("timelines", full, config or get_config(), fallback="timeline")
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
    make_current: bool = False,
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

    project = open_project(connection)
    timeline = find_timeline(project, name)
    current = current_name(project)
    is_current = name_of(timeline) == current

    with contextlib.ExitStack() as stack:
        made_current = False
        if make_current and not is_current:
            log.info("Switching to %r for the read, and back after (#84)", name_of(timeline))
            stack.enter_context(current_timeline(project, timeline))
            # Whether the switch *landed*, not whether it was asked for. Resolve refuses
            # ``SetCurrentTimeline`` while a modal dialog is up (#41, and ``_target`` in
            # apply.py raises on exactly this) — and a refusal here would otherwise have
            # the reader trust the very falsy answers this whole path exists to distrust,
            # with ``read_as_current`` certifying them.
            made_current = same_timeline(project.GetCurrentTimeline(), timeline)
            if not made_current:
                log.warning(
                    "Resolve would not make %r current, so its editor-state fields stay "
                    "unknown (#84); close any modal dialog in the Resolve GUI",
                    name_of(timeline),
                )
        reader = Reader(connection, current=is_current or made_current)
        heading = summarise(reader, timeline, project, current)
        fps = heading["fps"]

        window = _window(heading, start, end, fps)
        with_items = detail == "clips"
        tracks = [
            _read_track(reader, timeline, track_type, index, window, fps, with_items)
            for track_type, count in heading["tracks"].items()
            for index in range(1, count + 1)
        ]
        item_count = sum(track["item_count"] for track in tracks)
        markers = _marker_count(reader, timeline)
        currency = _currency(reader.reads_current, made_current)

    result: dict[str, Any] = {
        "timeline": {**heading, "markers": markers},
        "detail": detail,
        "range": {"in": dual_time(window[0], fps), "out": dual_time(window[1], fps)},
        "tracks": None if detail == "summary" else [_without_items(track) for track in tracks],
        "item_count": item_count,
        "currency": currency,
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
        result["spilled_to"] = spill(
            heading["name"], full, config or get_config(), fallback="timeline"
        )
    return result


UNKNOWN_OFF_CURRENT: Final = ("enabled", "locked", "takes")
"""Reply fields whose getters only answer for the current timeline (#84).

Named here rather than inferred, because the list is a live finding and not a rule: the
#84 sweep read every Timeline and TimelineItem getter twice — once with the timeline
current, once not — against a fixture built so each true value was non-falsy, since a
getter whose real answer is already ``0`` cannot be *seen* to lie. Three drifted; the
other ninety, frames and names and source bounds and ``GetClipEnabled`` among them, are
proven safe rather than merely untested. Anything added here needs the same evidence.
"""


def _currency(trustworthy: bool, made_current: bool) -> dict[str, Any]:
    """Whether the reading's editor-state fields can be believed, and what to do if not.

    ``made_current`` is what the switch achieved, never what it was asked to do: a switch
    Resolve refused leaves ``trustworthy`` false, and the reply says the fields are unknown
    rather than certifying the falsy answers as real.
    """
    return {
        "read_as_current": trustworthy,
        "made_current": made_current,
        "unknown_fields": [] if trustworthy else list(UNKNOWN_OFF_CURRENT),
        "fix": (
            None
            if trustworthy
            else (
                "Resolve answers these only for the project's current timeline, and "
                "answers falsely rather than failing for any other — so they are reported "
                "as null instead of a plausible zero. Pass make_current=true to switch to "
                "this timeline for the read and switch back, or open it in Resolve."
            )
        ),
    }


def _window(heading: dict[str, Any], start: Any, end: Any, fps: float | None) -> tuple[int, int]:
    """The range to read, half-open ``[in, out)``, defaulting to the whole timeline.

    The default comes from the heading rather than from Resolve again: two reads of the
    same bounds are two chances to disagree, and the range would then not be the range the
    reply says it is.
    """
    asked_start, asked_end = frame_window(start, end, fps)
    bounds = {edge: (heading[edge] or {}).get("frames") for edge in ("start", "end")}
    first = asked_start if asked_start is not None else (bounds["start"] or 0)
    last = asked_end if asked_end is not None else bounds["end"]
    if last is None:
        last = first
    if last < first:
        raise _backwards(first, last)
    return first, last


def frame_window(start: Any, end: Any, fps: float | None) -> tuple[int | None, int | None]:
    """A caller's range read as frames, order checked, either edge left open.

    Shared with the marker reader: two half-open ranges parsed in two places would drift
    apart, and a range that means something different per tool is worse than no range.
    """
    first = to_frames(start, fps, field="start")
    last = to_frames(end, fps, field="end")
    if first is not None and last is not None and last < first:
        raise _backwards(first, last)
    return first, last


def _backwards(first: int, last: int) -> InvalidRequestError:
    return InvalidRequestError(
        cause=f"The range ends at {last} but starts at {first}.",
        fix="Ranges are half-open [start, end) and run forwards; swap the two.",
        detail={"start": first, "end": last},
    )


def overlaps(first: int, last: int, window: tuple[int | None, int | None]) -> bool:
    """Whether ``[first, last)`` touches the window — an edge that only meets it does not.

    An open edge of the window excludes nothing, which is what an unasked-for start or end
    means.
    """
    start, end = window
    return (end is None or first < end) and (start is None or last > start)


def _read_track(
    reader: Reader,
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
            (item, _placement(item)) for item in items_in_track(timeline, track_type, index)
        )
        if _touches(placement, window)
    ]
    where = (track_type, index)
    readable = reader.reads_current
    return {
        "type": track_type,
        "index": index,
        "name": str(reader.optional(timeline, "GetTrackName", "", *where) or ""),
        # ``None``, not ``False``, off the current timeline — see ``Reader.reads_current``.
        "enabled": bool(reader.optional(timeline, "GetIsTrackEnabled", True, *where))
        if readable
        else None,
        "locked": bool(reader.optional(timeline, "GetIsTrackLocked", False, *where))
        if readable
        else None,
        "item_count": len(in_range),
        "items": (
            [
                read_item(reader, item, track_type, index, fps, placement)
                for item, placement in in_range
            ]
            if with_items
            else []
        ),
    }


def _without_items(track: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in track.items() if key != "items"}


def _capped(tracks: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Share ``cap`` shots across the stack, a round at a time.

    Filling track by track would spend the whole budget on a busy V1 and hand back a
    stacked reference with every angle below it empty — which is the one layout this tool
    exists to make readable. Taking a round from each track keeps the stack visible, and
    each track still reads in its own order.
    """
    kept: list[list[dict[str, Any]]] = [[] for _ in tracks]
    left = cap
    depth = 0
    while left > 0 and any(len(track["items"]) > depth for track in tracks):
        for position, track in enumerate(tracks):
            if left == 0:
                break
            if len(track["items"]) > depth:
                kept[position].append(track["items"][depth])
                left -= 1
        depth += 1
    return [{**track, "items": kept[position]} for position, track in enumerate(tracks)]


def _touches(placement: Placement, window: tuple[int, int]) -> bool:
    """Whether a shot overlaps the range at all — an edge that only meets it does not.

    A shot whose position cannot be read is kept: dropping it would quietly shorten the
    reading of a cut, which is worse than one entry the agent has to look at twice.
    """
    if placement.start is None or placement.end is None:
        return True
    return overlaps(placement.start, placement.end, window)


def items_in_track(timeline: Timeline, track_type: str, index: int) -> list[TimelineItem]:
    """The shots on one track. An empty track answers ``None`` rather than an empty list."""
    return list(timeline.GetItemListInTrack(track_type, index) or [])


def _marker_count(reader: Reader, timeline: Timeline) -> int:
    markers = reader.optional(timeline, "GetMarkers", None)
    return len(markers) if isinstance(markers, dict) else 0


def _placement(item: TimelineItem) -> Placement:
    """Where a shot sits, in the two numbers everything else is derived from."""
    return Placement(read_frames(item.GetStart()), read_frames(item.GetDuration()))


def read_item(
    reader: Reader,
    item: TimelineItem,
    track_type: str,
    index: int,
    fps: float | None,
    placement: Placement,
) -> dict[str, Any]:
    """One shot: where it sits, where it came from, and the offset between the two."""
    record_in, duration = placement.start, placement.duration
    record_out = placement.end
    source_in, source_out = source_bounds(reader, item, duration)
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
        "clip": clip_name(reader, item),
        "enabled": bool(reader.optional(item, "GetClipEnabled", True)),
        # ``GetTakesCount``, not ``GetTakeCount``: the plural is the method the scripting
        # README actually declares (line 523), and fusionscript answers an unknown name
        # with ``None`` rather than raising — so the singular read as zero takes forever.
        # ``None`` rather than 0 off the current timeline is #84: same wrong number, and
        # this time Resolve is the one saying it. A ``None`` *from* the getter still means
        # zero — see ``Reader.reads_current`` for why the two are not read off one value.
        "takes": (
            int(reader.optional(item, "GetTakesCount", 0) or 0) if reader.reads_current else None
        ),
    }


def source_bounds(
    reader: Reader,
    item: TimelineItem,
    duration: int | None,
) -> tuple[int | None, int | None]:
    """Where the shot starts and ends in its own media, both on the same clock.

    ``GetSourceStartFrame``/``GetSourceEndFrame`` are the direct answer and exist only on
    newer builds; the end is the last frame, hence the plus one. Reading it rather than
    deriving it matters on a retime, where a shot covers more or fewer source frames than
    it occupies on the timeline.

    Failing those, ``GetLeftOffset`` is how far into the media pool item the shot begins —
    the same number whenever the media itself starts at frame zero, which camera files do.
    The two routes are never mixed: an in point counted from the media start against an
    out point in absolute source frames would be a span that means nothing.
    """
    start = read_frames(reader.optional(item, "GetSourceStartFrame", None))
    if start is not None:
        last = read_frames(reader.optional(item, "GetSourceEndFrame", None))
        if last is not None:
            return start, last + 1
        return start, (start + duration if duration is not None else None)

    offset = read_frames(reader.optional(item, "GetLeftOffset", None))
    if offset is None:
        return None, None
    return offset, (offset + duration if duration is not None else None)


def clip_name(reader: Reader, item: TimelineItem) -> str | None:
    """The media pool clip a shot came from — a generator or title has none."""
    return _pool_name(reader, reader.optional(item, "GetMediaPoolItem", None))


def _pool_name(reader: Reader, clip: Any) -> str | None:
    """The pool clip's own name, for callers that already hold the clip."""
    if clip is None:
        return None
    name = reader.optional(clip, "GetName", None)
    return None if name is None else str(name)


def angle_name(reader: Reader, item: TimelineItem) -> str | None:
    """What to call the angle a shot came from, which is not always its pool clip.

    A multicam clip is the exception, and the reason this is not just :func:`clip_name`:
    every angle of a multicam shares *one* media pool item, so the pool name is the same
    string for the drummer cam and the wide. A cut measured by it has no angle switches in
    it at all — which is the one signal a style corpus is read for (#21), so getting this
    wrong does not look like an error, it looks like an editor who never cut away.

    Resolve puts the angle in the timeline item's own name instead ("<clip> - Video 2"), so
    that is what a multicam shot answers. Everything else keeps the pool name, which
    survives a shot being renamed on the timeline.
    """
    return angle_of(reader, item, reader.optional(item, "GetMediaPoolItem", None))


def angle_of(reader: Reader, item: TimelineItem, clip: Any) -> str | None:
    """:func:`angle_name` for a caller that already fetched the pool item.

    Every getter here is a call across to Resolve, and the shot read is one pass over a
    whole track — so a caller that needs the pool item for its own reasons hands it in
    rather than making the same round trip twice more.
    """
    if clip is None:
        return None
    kind = reader.optional(clip, "GetClipProperty", None, CLIP_TYPE)
    if str(kind or "") == MULTICAM_TYPE:
        angle = reader.optional(item, "GetName", None)
        if angle is not None:
            return str(angle)
    return _pool_name(reader, clip)
