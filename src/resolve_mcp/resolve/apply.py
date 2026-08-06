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
* **Two routes, one append.** A Text+ instance and a designed PNG card are different media
  and different fades, but they are the same placement: one clip, one record frame, one
  duration. So the routes part only where they must — where the source clip comes from,
  and whether a fade has to be written or arrived baked — and every event of the file goes
  onto the track in a single call, in document order. Cards are imported *before* the
  track is cleared, so a card Resolve refuses costs nothing that was already there.
* **Resolve's answer is never the evidence.** ``AppendToTimeline`` writes to the *current*
  timeline (so the switch is made and verified first), reports success on a locked track,
  and slides an append that overlaps. Every placement is read back off the track, and
  every title is read back after *all* the writes — an instance that shares its comp with
  another only shows up once a later write has had the chance to overwrite it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, NamedTuple

from ..errors import TitlesApplyFailedError, TitlesInvalidError
from ..logging_config import get_logger
from ..timing import dual_time
from ..titles.assets import Asset
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

SOURCE_IN: Final = 0
"""Both routes are placed from their source's first frame — a template has no other
content, and a card is the whole event with its ramps already in it."""


class Titled(NamedTuple):
    """One placed event and how it fades: through a Text+ node, or baked into a card."""

    event: Event
    node: fusion.TitleNode | None
    fade: fusion.Fade


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
    # The switch comes first: ``GetIsTrackLocked`` answers ``False`` for every track of a
    # timeline that is not current (#84), so the lock guard below would wave through a
    # locked track and Resolve would then report titles as placed and place none — the
    # exact failure the guard exists to catch. Nothing has been written at this point, so
    # a refusal after the switch still applies nothing.
    _target(project, timeline, track.timeline)
    _refuse_locked(timeline, track)
    # Cards come into the pool before the clear, never after: an import Resolve refuses
    # must not cost the titles that were standing on the track.
    cards = _import_cards(pool, checked.assets, track)
    cleared = _clear(timeline, track)
    _place(pool, checked.events, checked.templates, cards, track)
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
        "placed": [_placed(one, checked.fps, checked.assets.get(one.event.id)) for one in titled],
        "warnings": [finding.as_dict() for finding in checked.warnings],
    }


def _placed(titled: Titled, fps: float | None, card: Asset | None) -> dict[str, Any]:
    """One placed title, reported in the terms of the route that placed it.

    ``route`` comes first and the route-specific keys are simply absent on the other one:
    a ``template`` on a PNG entry or an ``asset`` on a Text+ entry would be a field the
    reader has to know is meaningless, and this report is read by an agent deciding what
    to fix.
    """
    event = titled.event
    common = {
        "id": event.id,
        "song": event.song,
        "kind": event.kind,
        "route": event.route,
        "record": dual_time(event.record_in, fps),
        "duration": dual_time(event.duration, fps),
        "fade": titled.fade.as_dict(),
        "note": event.note,
    }
    if event.is_png:
        return {
            **common,
            "asset": event.asset,
            "bin": None if card is None else card.bin_path,
            "frames": None if card is None else card.frames,
        }
    node = titled.node
    return {
        **common,
        "template": event.template,
        "text": event.text,
        "node": (
            None if node is None else {"name": node.name, "text_plus_in_comp": node.of_how_many}
        ),
    }


def _own_track(timeline: Timeline, name: str) -> OwnedTrack:
    """The topmost video track called ``Titles``, created and named if there is none.

    Topmost rather than first: a title belongs over the cut, and a project template that
    already carries a ``Titles`` track lower down is the operator's business. A track that
    is added but cannot be *named* is refused, because the next apply would not recognise
    it and would stack a second one on top.
    """
    standing = find_track(timeline, name)
    if standing is not None:
        return standing

    if not timeline.AddTrack(VIDEO):
        raise TitlesApplyFailedError(
            cause=f"Resolve refused to add a {TRACK_NAME!r} track to {name!r}, so nothing "
            f"was applied.",
            detail={"timeline": name, "track": TRACK_NAME},
        )
    added = OwnedTrack(track_count(timeline), name, created=True)
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


def find_track(timeline: Timeline, name: str) -> OwnedTrack | None:
    """The topmost video track called ``Titles``, or ``None`` when the timeline has none.

    Split out from :func:`_own_track` because only an *apply* may create the track: a tool
    that edits what is already there has nothing to put on a track it just made, and a
    freshly created empty one would be a worse answer than saying there are no titles.
    """
    matching = [
        index
        for index in range(1, track_count(timeline) + 1)
        if str(timeline.GetTrackName(VIDEO, index) or "") == TRACK_NAME
    ]
    return OwnedTrack(max(matching), name, created=False) if matching else None


def track_count(timeline: Timeline) -> int:
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
    return timeline_read.same_timeline(one, other)


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


def _import_cards(
    pool: Pool,
    cards: dict[str, Asset],
    track: OwnedTrack,
) -> dict[str, media.LocatedClip]:
    """Every PNG card in the pool exactly once, with its duration unlocked.

    Deduplicated by the bin and the card's first frame: two events sharing one card share
    one clip, and a card already sitting in that bin from an earlier apply is *found*
    rather than imported again. That second half is what keeps the file re-runnable — an
    apply that imported afresh every time would grow the media pool by a copy of every
    card on every run, and the pool is the operator's, not this tool's.
    """
    located: dict[str, media.LocatedClip] = {}
    already: dict[tuple[str, str], media.LocatedClip] = {}
    for event_id, card in cards.items():
        key = (card.bin_path, card.first_frame())
        if key not in already:
            already[key] = _card_clip(pool, card, track)
        located[event_id] = already[key]
    if located:
        log.info("%d png card(s) ready in the pool for %r", len(already), track.timeline)
    return located


def _card_clip(pool: Pool, card: Asset, track: OwnedTrack) -> media.LocatedClip:
    """The pool clip for one card: the one already there, or a fresh import of it."""
    target = media.ensure_bin(pool, card.bin_path)
    standing = media.clip_at_path(pool, target.path, card.first_frame())
    if standing is None:
        imported = media.import_into(pool, [card.request()], target)
        if not imported:
            raise TitlesApplyFailedError(
                cause=f"Resolve imported nothing for the card {card.declared!r} of "
                f"{card.event!r}, so nothing was applied and the {track.name!r} track was "
                f"not touched.",
                fix="The frames are on disk — check Resolve can read this image format and "
                "that the sequence is numbered without gaps, then apply again.",
                detail=track.detail(id=card.event, asset=card.declared, bin=target.path),
            )
        standing = media.LocatedClip(target.path, imported[0])
        log.info("Imported the card %s into %r", card.declared, target.path or "the root")
    # The out point is written on every apply, not only on the import: a card put into the
    # pool by hand has never had one, and without it Resolve ignores ``endFrame`` outright
    # and lands the card at the project's default still duration instead of the event's.
    media.apply_still_workaround(standing.clip, media.properties(standing.clip))
    return standing


def _place(
    pool: Pool,
    events: list[Event],
    templates: dict[str, media.LocatedClip],
    cards: dict[str, media.LocatedClip],
    track: OwnedTrack,
) -> None:
    """One call for the whole file, both routes in it. Its return value is counted,
    never believed."""
    if not events:
        return
    placements = [
        {
            "mediaPoolItem": (
                cards[event.id].clip if event.is_png else templates[event.template].clip
            ),
            "startFrame": SOURCE_IN,
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
            fix="A source shorter than the span asked for is refused rather than trimmed "
            "— shorten the longest event, lengthen the template in the Resolve GUI and "
            "export its bin again, or re-bake the card to the frames the event asks for.",
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


def _write_titles(events: list[Event], items: list[Item]) -> list[Titled]:
    """Set every Text+ title's text and fade, then read every text back off the placed clips.

    Every write happens before any read on purpose (#41): reading a title straight after
    writing it would pass whether or not the instances share one Fusion comp, because the
    write that overwrites an earlier title has not happened yet. For the same reason the
    read-back walks to the node again from the timeline item rather than reusing the node
    it wrote through — a handle can go on answering for a comp the timeline has replaced.

    A PNG card is skipped by both halves. It carries no Fusion comp to write into and its
    words and its ramps were exported into the pixels, so the placement *is* the title —
    there is nothing left to say to it, and asking it for a Text+ node would fail.
    """
    written: list[Titled] = []
    for event, item in zip(events, items, strict=True):
        if event.is_png:
            written.append(Titled(event, None, fusion.baked_fade(event.fade_in, event.fade_out)))
            continue
        node = fusion.title_node(item, f"title {event.id!r}")
        fusion.set_text(node, event.text)
        fade = fusion.write_fade(
            node,
            duration=event.duration,
            fade_in=event.fade_in,
            fade_out=event.fade_out,
        )
        written.append(Titled(event, node, fade))

    strayed: list[tuple[Event, str | None]] = []
    for titled, item in zip(written, items, strict=True):
        event = titled.event
        if event.is_png:
            continue
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


__all__ = ["VIDEO", "OwnedTrack", "Titled", "apply_titles", "find_track", "track_count"]
