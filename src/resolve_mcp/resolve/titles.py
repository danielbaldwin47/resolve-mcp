"""Applying a titles file: one owned track, cleared and re-placed from the file every time.

``apply_titles`` is declarative in the same way ``build_timeline`` is, but it works on a
timeline someone else built, so the safety it needs is different: a build makes a *new*
version and can afford to fail half-done, while an apply edits the version under review
and must never destroy the good state it is replacing.

That gives the order everything here follows:

* **Validate everything, then touch nothing until it all passes.** The rules run over the
  file, the timeline's markers and the media pool before the Titles track is looked at, so
  a refused apply leaves the previous titles exactly where they were.
* **Own one track, completely.** The topmost video track named ``Titles`` belongs to this
  tool: it is created if absent, cleared whole, and re-placed from the file. Nothing else
  on the timeline is read or written — the cut on V1 is untouched, which is what makes
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

from dataclasses import dataclass, field
from typing import Any, Final

from ..errors import TitlesApplyFailedError, TitlesInvalidError
from ..findings import Finding, severity_of
from ..logging_config import get_logger
from ..timing import dual_time
from ..titles.document import LoadedTitles, read_titles_file
from ..titles.schema import (
    ANNOTATED_EXAMPLE,
    SCHEMA_DOC,
    SCHEMA_VERSION,
    TRACK_NAME,
)
from ..titles.validate import (
    ANCHOR_COLOR,
    RULE_DESCRIPTIONS,
    Event,
    TemplateFacts,
    plan,
    template_of,
    validate_project,
    validate_structure,
)
from . import fusion, markers, media
from . import timeline as timeline_read
from .connection import ResolveConnection
from .session import frame_rate

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


def get_titles_schema() -> dict[str, Any]:
    """The titles-file contract: the schema document, its annotated example, the rules."""
    return {
        "schema": SCHEMA_VERSION,
        "document": SCHEMA_DOC,
        "annotated_example": ANNOTATED_EXAMPLE,
        "rules": [
            {"rule": rule, "severity": severity_of(rule), "description": description}
            for rule, description in RULE_DESCRIPTIONS.items()
        ],
    }


@dataclass(frozen=True)
class Preflight:
    """One pass of the rules, and everything the apply would need if they pass.

    The timeline, the templates and the events travel with the findings so the apply
    never looks anything up twice — a file validated against one clip and applied with
    another is the failure this pairing makes impossible.
    """

    loaded: LoadedTitles
    findings: list[Finding]
    project: Project | None = None
    timeline: Timeline | None = None
    fps: float | None = None
    templates: dict[str, media.LocatedClip] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]


def preflight(connection: ResolveConnection, titles_file: str) -> Preflight:
    """Read ``titles_file`` and run every rule on it. The dry run and the apply share this."""
    loaded = read_titles_file(titles_file)
    if loaded.parse_error is not None:
        return Preflight(loaded, [loaded.parse_error])

    findings = validate_structure(loaded.doc)
    if any(finding.rule == "T1" for finding in findings):
        log.info("Titles file %s is not schema-valid; Resolve was not read", loaded.path)
        return Preflight(loaded, findings)

    doc: dict[str, Any] = loaded.doc
    project = timeline_read.open_project(connection)
    timeline = timeline_read.find_timeline(project, doc.get("timeline"))
    fps = frame_rate(project, timeline)
    pool = media.media_pool(connection)

    facts, located = _templates(pool, doc)
    anchors = markers.markers_by_name(connection, timeline, fps, ANCHOR_COLOR)
    log.info(
        "Titling %r: %d %s marker(s), %d template(s) declared",
        timeline_read.name_of(timeline),
        len(anchors),
        ANCHOR_COLOR.lower(),
        len(facts),
    )
    findings = [
        *findings,
        *validate_project(doc, anchors=anchors, templates=facts, span=_span(timeline)),
    ]
    return Preflight(loaded, findings, project, timeline, fps, located, plan(doc, anchors))


def _span(timeline: Timeline) -> tuple[int, int]:
    """The timeline's own ``[start, end)`` in record frames, as T9 judges against it."""
    start = timeline_read.read_frames(timeline.GetStartFrame())
    end = timeline_read.read_frames(timeline.GetEndFrame())
    if start is None or end is None:
        log.warning("Timeline reported an unreadable span; T9 cannot check placement bounds")
        return (0, 0) if start is None and end is None else (start or 0, end or 0)
    return (start, end)


def _templates(
    pool: Pool,
    doc: dict[str, Any],
) -> tuple[list[TemplateFacts], dict[str, media.LocatedClip]]:
    """Every declared template the file actually uses, resolved against the pool.

    Reported as facts rather than raised on, because T5 has to name every unusable
    template at once — and the resolved handles come back beside them so the apply places
    the very clips the rules were judged against.
    """
    used = {template_of(event) for song in doc["songs"] for event in song["events"]}
    declared = {
        name: template for name, template in doc["templates"].items() if name in used
    }
    found = media.clips_named(pool, {str(one["clip"]) for one in declared.values()})

    facts: list[TemplateFacts] = []
    located: dict[str, media.LocatedClip] = {}
    for name, template in declared.items():
        clip = str(template["clip"])
        bin_path = template.get("bin")
        matches = [one for one in found if str(one.clip.GetName() or "") == clip]
        if bin_path is not None:
            matches = [one for one in matches if _under(one.bin_path, str(bin_path))]
        facts.append(
            TemplateFacts(
                name=name,
                clip=clip,
                bin_path=None if bin_path is None else str(bin_path),
                matches=len(matches),
                found_in=tuple(one.bin_path or "<root>" for one in matches),
            )
        )
        if len(matches) == 1:
            located[name] = matches[0]
    return facts, located


def _under(bin_path: str | None, declared: str) -> bool:
    """Whether a clip's bin is the declared one or nested inside it, as find_clip searches."""
    where = bin_path or ""
    return where == declared or where.startswith(f"{declared}{media.BIN_SEPARATOR}")


def validate_titles(connection: ResolveConnection, titles_file: str) -> dict[str, Any]:
    """Dry-run ``titles_file``: every rule, every failure, before anything is applied."""
    return _report(preflight(connection, titles_file))


def _report(checked: Preflight) -> dict[str, Any]:
    return {
        "titles_file": str(checked.loaded.path),
        "content_hash": checked.loaded.content_hash,
        "valid": not checked.errors,
        "errors": [finding.as_dict() for finding in checked.errors],
        "warnings": [finding.as_dict() for finding in checked.warnings],
        "timeline": (
            None if checked.timeline is None else timeline_read.name_of(checked.timeline)
        ),
        "events": len(checked.events),
    }


# --- the apply ------------------------------------------------------------------------------


def apply_titles(connection: ResolveConnection, titles_file: str) -> dict[str, Any]:
    """Clear the Titles track and re-place every event in ``titles_file`` onto it."""
    checked = preflight(connection, titles_file)
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
    doc: dict[str, Any] = checked.loaded.doc
    project, timeline = checked.project, checked.timeline
    name = timeline_read.name_of(timeline)
    track_name = str(doc.get("track", TRACK_NAME))
    pool = media.media_pool(connection)

    track, created = _own_track(timeline, track_name, name)
    _refuse_locked(timeline, track, track_name, name)
    _target(project, timeline, name)
    cleared = _clear(timeline, track, track_name, name)
    _place(pool, checked.events, checked.templates, track, name)
    items = _verify(timeline, checked.events, track, track_name, name)
    titled = _write_titles(checked.events, items)

    log.info("Applied %d title(s) to %s of %r", len(titled), track_name, name)
    return {
        "titles_file": str(checked.loaded.path),
        "content_hash": checked.loaded.content_hash,
        "timeline": timeline_read.summarise(
            timeline_read.Reader(connection),
            timeline,
            project,
            timeline_read.current_name(project),
        ),
        "track": {"index": track, "name": track_name, "created": created},
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


def _own_track(timeline: Timeline, track_name: str, name: str) -> tuple[int, bool]:
    """The topmost video track called ``track_name``, created and named if there is none.

    Topmost rather than first: a title belongs over the cut, and a project template that
    already carries a ``Titles`` track lower down is the operator's business. A track that
    is added but cannot be *named* is refused, because the next apply would not recognise
    it and would stack a second one on top.
    """
    matching = [
        index
        for index in range(1, _track_count(timeline) + 1)
        if str(timeline.GetTrackName(VIDEO, index) or "") == track_name
    ]
    if matching:
        return (max(matching), False)

    if not timeline.AddTrack(VIDEO):
        raise TitlesApplyFailedError(
            cause=f"Resolve refused to add a {track_name!r} track to {name!r}, so nothing "
            f"was applied.",
            detail={"timeline": name, "track": track_name},
        )
    index = _track_count(timeline)
    rename = getattr(timeline, "SetTrackName", None)
    if not (callable(rename) and rename(VIDEO, index, track_name)):
        raise TitlesApplyFailedError(
            cause=f"Resolve added video track {index} to {name!r} but would not name it "
            f"{track_name!r}, so nothing was applied.",
            fix=f"Delete the empty video track {index}, or rename it {track_name!r} by hand, "
            f"and apply again — an unnamed track would be left behind on the next apply.",
            detail={"timeline": name, "track": track_name, "track_index": index},
        )
    log.info("Added the %s track to %r as video %d", track_name, name, index)
    return (index, True)


def _track_count(timeline: Timeline) -> int:
    try:
        return int(timeline.GetTrackCount(VIDEO) or 0)
    except (TypeError, ValueError):
        log.warning("Resolve gave an unreadable video track count")
        return 0


def _refuse_locked(timeline: Timeline, track: int, track_name: str, name: str) -> None:
    """A locked track takes the append, reports items and places nothing — and clearing
    it would not work either, so this is checked before anything is deleted."""
    if not timeline.GetIsTrackLocked(VIDEO, track):
        return
    raise TitlesApplyFailedError(
        cause=f"The {track_name!r} track of {name!r} is locked; Resolve would report the "
        f"titles as placed and place none, so nothing was applied.",
        fix="Unlock the track in the timeline header and apply again.",
        detail={"timeline": name, "track": track_name, "track_index": track},
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


def _clear(timeline: Timeline, track: int, track_name: str, name: str) -> int:
    """Empty the owned track, and read it back — a stale title left there would double up."""
    standing = list(timeline.GetItemListInTrack(VIDEO, track) or [])
    if not standing:
        return 0
    delete = getattr(timeline, "DeleteClips", None)
    if not callable(delete):
        raise TitlesApplyFailedError(
            cause=f"This Resolve build has no DeleteClips, so the {len(standing)} title(s) "
            f"already on {track_name!r} cannot be cleared and nothing was applied.",
            fix="Delete the clips on the Titles track by hand and apply again.",
            detail={"timeline": name, "track": track_name, "standing": len(standing)},
        )
    # Never a ripple delete: the Titles track is the only one this tool owns, and a ripple
    # would pull the cut on V1 along with it.
    delete(standing, False)
    left = list(timeline.GetItemListInTrack(VIDEO, track) or [])
    if left:
        raise TitlesApplyFailedError(
            cause=f"{len(left)} of {len(standing)} title(s) are still on {track_name!r} "
            f"after the clear, so nothing new was placed.",
            detail={"timeline": name, "track": track_name, "remaining": len(left)},
        )
    log.info("Cleared %d title(s) off %s of %r", len(standing), track_name, name)
    return len(standing)


def _place(
    pool: Pool,
    events: list[Event],
    templates: dict[str, media.LocatedClip],
    track: int,
    name: str,
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
            "trackIndex": track,
            "recordFrame": event.record_in,
        }
        for event in events
    ]
    returned = list(pool.AppendToTimeline(placements) or [])
    if len(returned) != len(events):
        raise TitlesApplyFailedError(
            cause=f"Asked Resolve for {len(events)} title(s) on {name!r} and got back "
            f"{len(returned)}.",
            fix="A template shorter than the span asked for is refused rather than trimmed "
            "— shorten the longest event, or lengthen the template in the Resolve GUI and "
            "export its bin again.",
            detail={"timeline": name, "asked": len(events), "returned": len(returned)},
        )
    log.info("Appended %d title(s) to %r", len(events), name)


def _verify(
    timeline: Timeline,
    events: list[Event],
    track: int,
    track_name: str,
    name: str,
) -> list[Item]:
    """Read the track back and pair each event with the clip that landed for it.

    Position is the pairing key because the events cannot overlap (T8), so a record frame
    identifies exactly one of them — and a title that did not land on its own frame is
    the failure this read exists to catch.
    """
    landed = {
        int(item.GetStart()): item for item in timeline.GetItemListInTrack(VIDEO, track) or []
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
            f"them on {track_name!r} of {name!r}.",
            fix="Resolve slides an append that overlaps existing media and drops one onto a "
            "track it cannot reach, both while reporting success. Check the Titles track is "
            "empty of anything this tool did not place, and apply again.",
            detail={
                "timeline": name,
                "track": track_name,
                "adrift": [
                    {"id": event.id, "record_frame": event.record_in, "duration": event.duration}
                    for event in adrift
                ],
            },
        )
    return [landed[event.record_in] for event in events]


def _write_titles(
    events: list[Event],
    items: list[Item],
) -> list[tuple[Event, fusion.TitleNode, fusion.Fade]]:
    """Set every title's text and fade, then read every text back off the placed clips.

    Every write happens before any read on purpose (#41): reading a title straight after
    writing it would pass whether or not the instances share one Fusion comp, because the
    write that overwrites an earlier title has not happened yet.
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


__all__ = ["apply_titles", "get_titles_schema", "preflight", "validate_titles"]
