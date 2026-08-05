"""Applying a titles file: one owned track, cleared and re-placed from the file every time.

This is to ``titles.py`` what ``build.py`` is to ``cut.py`` — the rules have already run,
and everything here writes. But it works on a timeline someone else built, so the safety
it needs is different from a build's: a build makes a *new* version and can afford to
fail half-done, while an apply edits the version under review and must never destroy the
good state it is replacing.

That gives the order everything here follows:

* **Validate everything, then touch nothing until it all passes.** The rules run over the
  file, the timeline's markers and the media pool before the Titles track is looked at, so
  a refused apply leaves the previous titles exactly where they were.
* **Own one track, completely.** The topmost video track named ``Titles`` belongs to this
  tool: it is created if absent, cleared whole, and re-placed from the file. The name is
  not the caller's to choose — a tool that could be pointed at any track could clear one
  it does not own. Nothing else on the timeline is read or written, which is what makes
  "rebuild the cut, re-apply the titles" safe.
* **Songs are found by marker, not by frame.** Every event's position is an offset from
  the blue marker naming its song, so the same file lands correctly on every rebuild.
* **Resolve's answer is never the evidence.** ``AppendToTimeline`` writes to the *current*
  timeline (so the switch is made and verified first), reports success on a locked track,
  and slides an append that overlaps. Every placement is read back off the track, and
  every title is read back after *all* the writes — an instance that shares its comp with
  another only shows up once a later write has had the chance to overwrite it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from ..errors import TitlesApplyFailedError, TitlesInvalidError
from ..logging_config import get_logger
from ..timing import dual_time
from ..titles.schema import TRACK_NAME
from ..titles.validate import Event
from . import fusion, media
from . import timeline as timeline_read
from . import titles as titles_read
from .connection import ResolveConnection

log = get_logger("titles")

Item = Any
Pool = Any
Project = Any
Timeline = Any

VIDEO: Final = "video"
MEDIA_TYPE_VIDEO: Final = 1
"""Resolve's ``mediaType`` for a video-only append. Never omitted: it drops the clip."""

TEMPLATE_SOURCE_IN: Final = 0
"""A title template is placed from its own first frame; it has no other content."""


@dataclass(frozen=True)
class OwnedTrack:
    """The one track this tool writes to, and the timeline it is on.

    The three of them travel together through every step and into every failure's
    ``detail``, because a titling failure the agent can act on is always "which track of
    which timeline" — and the name is a constant rather than a field so that no caller
    can widen what "owned" means.
    """

    index: int
    timeline: str
    created: bool

    @property
    def name(self) -> str:
        return TRACK_NAME

    def detail(self, **extra: Any) -> dict[str, Any]:
        return {
            "timeline": self.timeline,
            "track": self.name,
            "track_index": self.index,
            **extra,
        }

    def as_dict(self) -> dict[str, Any]:
        return {"index": self.index, "name": self.name, "created": self.created}


def apply_titles(connection: ResolveConnection, titles_file: str) -> dict[str, Any]:
    """Clear the Titles track and re-place every event in ``titles_file`` onto it."""
    checked = titles_read.preflight(connection, titles_file)
    if checked.errors:
        raise TitlesInvalidError(
            cause=f"The titles file has {len(checked.errors)} error(s), so nothing was "
            f"applied and the Titles track was not touched.",
            detail={
                "titles_file": str(checked.loaded.path),
                "content_hash": checked.loaded.content_hash,
                "errors": [finding.as_dict() for finding in checked.errors],
                "warnings": [finding.as_dict() for finding in checked.warnings],
            },
        )
    # No error means the document parsed, matched the schema and resolved against the
    # project — every reading below is one the rules have already been over.
    project, timeline = checked.project, checked.timeline
    pool = media.media_pool(connection)

    track = _own_track(timeline, timeline_read.name_of(timeline))
    _refuse_locked(timeline, track)
    _target(project, timeline, track.timeline)
    cleared = _clear(timeline, track)
    _place(pool, checked.events, checked.templates, track)
    items = _verify(timeline, checked.events, track)
    titled = _write_titles(checked.events, items)

    log.info("Applied %d title(s) to %s of %r", len(titled), track.name, track.timeline)
    return {
        "titles_file": str(checked.loaded.path),
        "content_hash": checked.loaded.content_hash,
        "timeline": timeline_read.summarise(
            timeline_read.Reader(connection),
            timeline,
            project,
            timeline_read.current_name(project),
        ),
        "track": track.as_dict(),
        "cleared": cleared,
        "placed": [_placed(event, node, fade, checked.fps) for event, node, fade in titled],
        "warnings": [finding.as_dict() for finding in checked.warnings],
    }


def _placed(
    event: Event,
    node: fusion.TitleNode,
    fade: fusion.Fade,
    fps: float | None,
) -> dict[str, Any]:
    return {
        "id": event.id,
        "song": event.song,
        "kind": event.kind,
        "template": event.template,
        "text": event.text,
        "record": dual_time(event.record_in, fps),
        "duration": dual_time(event.duration, fps),
        "node": {"name": node.name, "text_plus_in_comp": node.of_how_many},
        "fade": fade.as_dict(),
        "note": event.note,
    }


def _own_track(timeline: Timeline, name: str) -> OwnedTrack:
    """The topmost video track called ``Titles``, created and named if there is none.

    Topmost rather than first: a title belongs over the cut, and a project template that
    already carries a ``Titles`` track lower down is the operator's business. A track that
    is added but cannot be *named* is refused, because the next apply would not recognise
    it and would stack a second one on top.
    """
    matching = [
        index
        for index in range(1, _track_count(timeline) + 1)
        if str(timeline.GetTrackName(VIDEO, index) or "") == TRACK_NAME
    ]
    if matching:
        return OwnedTrack(max(matching), name, created=False)

    if not timeline.AddTrack(VIDEO):
        raise TitlesApplyFailedError(
            cause=f"Resolve refused to add a {TRACK_NAME!r} track to {name!r}, so nothing "
            f"was applied.",
            detail={"timeline": name, "track": TRACK_NAME},
        )
    added = OwnedTrack(_track_count(timeline), name, created=True)
    rename = getattr(timeline, "SetTrackName", None)
    if not (callable(rename) and rename(VIDEO, added.index, TRACK_NAME)):
        raise TitlesApplyFailedError(
            cause=f"Resolve added video track {added.index} to {name!r} but would not name "
            f"it {TRACK_NAME!r}, so nothing was applied.",
            fix=f"Delete the empty video track {added.index}, or rename it {TRACK_NAME!r} by "
            f"hand, and apply again — an unnamed track would be left behind on the next apply.",
            detail=added.detail(),
        )
    log.info("Added the %s track to %r as video %d", TRACK_NAME, name, added.index)
    return added


def _track_count(timeline: Timeline) -> int:
    try:
        return int(timeline.GetTrackCount(VIDEO) or 0)
    except (TypeError, ValueError):
        log.warning("Resolve gave an unreadable video track count")
        return 0


def _refuse_locked(timeline: Timeline, track: OwnedTrack) -> None:
    """A locked track takes the append, reports items and places nothing — and clearing it
    would not work either, so this is checked before anything is deleted."""
    if not timeline.GetIsTrackLocked(VIDEO, track.index):
        return
    raise TitlesApplyFailedError(
        cause=f"The {track.name!r} track of {track.timeline!r} is locked; Resolve would "
        f"report the titles as placed and place none, so nothing was applied.",
        fix="Unlock the track in the timeline header and apply again.",
        detail=track.detail(),
    )


def _target(project: Project, timeline: Timeline, name: str) -> None:
    """Make the target current, and check it — appends go to whatever is current (#41).

    A build that does not honour the switch would otherwise take every title of this file
    and place it on the cut the operator has open, reporting success the whole way.
    """
    project.SetCurrentTimeline(timeline)
    current = project.GetCurrentTimeline()
    if current is None or not _same(current, timeline):
        raise TitlesApplyFailedError(
            cause=f"Resolve would not open {name!r}, and an append lands on whatever "
            f"timeline is current — so nothing was applied.",
            fix="Close any modal dialog in the Resolve GUI, open the timeline, and apply again.",
            detail={
                "timeline": name,
                "current": None if current is None else timeline_read.name_of(current),
            },
        )


def _same(one: Timeline, other: Timeline) -> bool:
    """Identity by Resolve's own id: two proxies for one timeline are not the same object."""
    first, second = getattr(one, "GetUniqueId", None), getattr(other, "GetUniqueId", None)
    if callable(first) and callable(second):
        return bool(first() == second())
    return bool(timeline_read.name_of(one) == timeline_read.name_of(other))


def _clear(timeline: Timeline, track: OwnedTrack) -> int:
    """Empty the owned track, and read it back — a stale title left there would double up."""
    standing = list(timeline.GetItemListInTrack(VIDEO, track.index) or [])
    if not standing:
        return 0
    delete = getattr(timeline, "DeleteClips", None)
    if not callable(delete):
        raise TitlesApplyFailedError(
            cause=f"This Resolve build has no DeleteClips, so the {len(standing)} title(s) "
            f"already on {track.name!r} cannot be cleared and nothing was applied.",
            fix="Delete the clips on the Titles track by hand and apply again.",
            detail=track.detail(standing=len(standing)),
        )
    # Never a ripple delete: the Titles track is the only one this tool owns, and a ripple
    # would pull the cut on V1 along with it.
    delete(standing, False)
    left = list(timeline.GetItemListInTrack(VIDEO, track.index) or [])
    if left:
        raise TitlesApplyFailedError(
            cause=f"{len(left)} of {len(standing)} title(s) are still on {track.name!r} "
            f"after the clear, so nothing new was placed.",
            detail=track.detail(remaining=len(left)),
        )
    log.info("Cleared %d title(s) off %s of %r", len(standing), track.name, track.timeline)
    return len(standing)


def _place(
    pool: Pool,
    events: list[Event],
    templates: dict[str, media.LocatedClip],
    track: OwnedTrack,
) -> None:
    """One call for the whole file. Its return value is counted, never believed."""
    if not events:
        return
    placements = [
        {
            "mediaPoolItem": templates[event.template].clip,
            "startFrame": TEMPLATE_SOURCE_IN,
            "endFrame": event.duration,
            "mediaType": MEDIA_TYPE_VIDEO,
            "trackIndex": track.index,
            "recordFrame": event.record_in,
        }
        for event in events
    ]
    returned = list(pool.AppendToTimeline(placements) or [])
    if len(returned) != len(events):
        raise TitlesApplyFailedError(
            cause=f"Asked Resolve for {len(events)} title(s) on {track.timeline!r} and got "
            f"back {len(returned)}.",
            fix="A template shorter than the span asked for is refused rather than trimmed "
            "— shorten the longest event, or lengthen the template in the Resolve GUI and "
            "export its bin again.",
            detail=track.detail(asked=len(events), returned=len(returned)),
        )
    log.info("Appended %d title(s) to %r", len(events), track.timeline)


def _verify(timeline: Timeline, events: list[Event], track: OwnedTrack) -> list[Item]:
    """Read the track back and pair each event with the clip that landed for it.

    Position is the pairing key because the events cannot overlap (T8), so a record frame
    identifies exactly one of them — and a title that did not land on its own frame is
    the failure this read exists to catch.
    """
    landed = {
        int(item.GetStart()): item
        for item in timeline.GetItemListInTrack(VIDEO, track.index) or []
    }
    adrift = [
        event
        for event in events
        if event.record_in not in landed
        or int(landed[event.record_in].GetDuration()) != event.duration
    ]
    if adrift:
        raise TitlesApplyFailedError(
            cause=f"{len(adrift)} of {len(events)} title(s) did not land where the file puts "
            f"them on {track.name!r} of {track.timeline!r}.",
            fix="Resolve slides an append that overlaps existing media and drops one onto a "
            "track it cannot reach, both while reporting success. Check the Titles track is "
            "empty of anything this tool did not place, and apply again.",
            detail=track.detail(
                adrift=[
                    {"id": event.id, "record_frame": event.record_in, "duration": event.duration}
                    for event in adrift
                ]
            ),
        )
    return [landed[event.record_in] for event in events]


def _write_titles(
    events: list[Event],
    items: list[Item],
) -> list[tuple[Event, fusion.TitleNode, fusion.Fade]]:
    """Set every title's text and fade, then read every text back off the placed clips.

    Every write happens before any read on purpose (#41): reading a title straight after
    writing it would pass whether or not the instances share one Fusion comp, because the
    write that overwrites an earlier title has not happened yet. For the same reason the
    read-back walks to the node again from the timeline item rather than reusing the node
    it wrote through — a handle can go on answering for a comp the timeline has replaced.
    """
    written: list[tuple[Event, fusion.TitleNode, fusion.Fade]] = []
    for event, item in zip(events, items, strict=True):
        node = fusion.title_node(item, f"title {event.id!r}")
        fusion.set_text(node, event.text)
        fade = fusion.write_fade(
            node,
            duration=event.duration,
            fade_in=event.fade_in,
            fade_out=event.fade_out,
        )
        written.append((event, node, fade))

    strayed: list[tuple[Event, str | None]] = []
    for (event, _, _), item in zip(written, items, strict=True):
        read = fusion.read_text(fusion.title_node(item, f"title {event.id!r}"))
        if read != event.text:
            strayed.append((event, read))
    if strayed:
        first, read = strayed[0]
        raise TitlesApplyFailedError(
            cause=f"{len(strayed)} title(s) do not read back as written: {first.id!r} was "
            f"given {first.text!r} and reads {read!r}.",
            fix="If a title reads back as another title's text, the placed instances share "
            "one Fusion comp and this template cannot carry per-instance titles — author a "
            "fresh Text+ template in the GUI and export its bin again.",
            detail={"strayed": [{"id": event.id, "read_back": text} for event, text in strayed]},
        )
    return written


__all__ = ["OwnedTrack", "apply_titles"]
