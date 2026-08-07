"""The titles-file validation rules: 11 hard errors, 2 warnings, one implementation.

The list is identical in the ``validate_titles`` dry run and in ``apply_titles``'
pre-flight, so it lives here once and both call it. A failing file must abort before the
Titles track is cleared — a track emptied for titles that were never placed is the
outcome this file exists to prevent, and it is worse than the cut's equivalent because
the thing destroyed is the *previous* good state.

Three passes, because they need different things:

* :func:`validate_structure` reads the document alone — no project, no connection, no
  disk. Shape, ids, ranges, fades and route coherence (T1-T4, T6, W1).
* :func:`validate_assets` reads the disk, and nothing else: the PNG cards an event points
  at, and whether they carry the frames it asks for (T10, T11). It runs before Resolve is
  opened, because a card that was never exported is worth saying so without a connection.
* :func:`validate_project` takes the song anchors and template facts already read off the
  timeline and the media pool, and answers everything that depends on them (T5, T7-T9,
  W2). It is a pure function over those facts, so every rule here is unit-testable
  without Resolve.

:func:`plan` is the bridge to the apply: the same offsets the rules judged, turned into
the absolute record frames Resolve needs. Positions are computed once, in one place, so
a title can never be validated at one frame and placed at another.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, TypeGuard

from ..findings import Finding, ordered
from ..logging_config import get_logger
from ..timing import ranges_overlap
from .assets import Asset, resolve_asset
from .schema import DEFAULT_ROUTE, KINDS, ROUTES, SCHEMA_VERSION

log = get_logger("titles")

PNG: Final = "png"
TEXTPLUS: Final = "textplus"

RULE_DESCRIPTIONS: Final[dict[str, str]] = {
    "T1": "JSON parses; schema-valid; schema version supported",
    "T2": "event ids unique across the file; song keys unique",
    "T3": "in < out on every event",
    "T4": "fade in + fade out fit inside the event's duration",
    "T5": "every template resolves to exactly one media-pool clip",
    "T6": "every event carries the fields its route needs (text, or asset)",
    "T7": "every song key names exactly one blue marker on the timeline",
    "T8": "events do not overlap — one Titles track shows one title at a time",
    "T9": "every event lands inside the timeline",
    "T10": "every png asset is on disk",
    "T11": "every png asset carries the frames its event asks for",
    "W1": "a song with no events",
    "W2": "a blue marker with no song in the file",
}

_FIX_HINTS: Final[dict[str, str]] = {
    "T1": "Call get_titles_schema and match the annotated example exactly.",
    "T2": "Give every event its own id and every song its own key.",
    "T3": "Ranges are half-open [in, out); make out strictly greater than in.",
    "T4": "Shorten the fades or lengthen the event — a fade cannot run past the title.",
    "T5": "Import the template bin into the media pool, or fix templates.<name>.clip/.bin.",
    "T6": "A textplus event needs 'text' and a template; a png event needs 'asset'.",
    "T7": "Name a blue marker exactly this key with set_markers, or fix the key.",
    "T8": "Stagger the events — one Titles track can only show one title at a time.",
    "T9": "Pull the event inside the timeline, or re-mark the song on the version you title.",
    "T10": "Export the card to that path, or fix the event's 'asset' — paths are relative "
    "to the titles file.",
    "T11": "Re-bake the sequence to out - in frames, or retime the event to the frames it "
    "has; a one-image card is frozen to length and can carry no fade.",
    "W1": "Add events to the song, or drop it from the file.",
    "W2": "Expected while titling a set a song at a time; otherwise add the song.",
}

ANCHOR_COLOR: Final = "Blue"
"""The reserved song-start colour (#14 §1). Every other marker colour is the director's."""


@dataclass(frozen=True)
class TemplateFacts:
    """What the rules need to know about one declared template.

    Gathered once from the media pool and then handed to a pure function, so the rules
    never hold a Resolve handle. ``matches`` is a count rather than a handle because a
    name that resolves to two clips is as unplaceable as one that resolves to none, and
    both have to be said in the same sentence.
    """

    name: str
    clip: str
    bin_path: str | None
    matches: int
    found_in: tuple[str, ...] = ()


@dataclass(frozen=True)
class Event:
    """One title, positioned absolutely and ready to place.

    ``record_in`` is an absolute record frame — the timeline's own clock, the same one
    ``recordFrame`` counts in — because the song anchor it came from was read in that
    clock too. Nothing downstream re-derives it.
    """

    id: str
    song: str
    kind: str
    route: str
    template: str
    text: str
    asset: str
    record_in: int
    duration: int
    fade_in: int
    fade_out: int
    note: str

    @property
    def is_png(self) -> bool:
        """Which of the two routes places this event: a designed card, or a Text+ instance."""
        return self.route == PNG

    @property
    def record_out(self) -> int:
        """Half-open, and exactly what Resolve's ``endFrame`` measures against."""
        return self.record_in + self.duration


def _finding(rule: str, id: str | None, message: str, fix_hint: str | None = None) -> Finding:
    return Finding(rule=rule, id=id, message=message, fix_hint=fix_hint or _FIX_HINTS[rule])


def parse_failure_finding(detail: str) -> Finding:
    """T1: the file is on disk but is not JSON, so no other rule can be answered."""
    return _finding("T1", None, f"The titles file is not valid JSON: {detail}.")


# --- shape (T1) ---------------------------------------------------------------------------


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _kind(value: Any) -> str:
    return type(value).__name__


def _shape_errors(doc: Any) -> Iterator[Finding]:
    """Everything that makes the document unreadable, reported as T1."""
    if not isinstance(doc, dict):
        yield _finding("T1", None, f"The titles file must be a JSON object, got {_kind(doc)}.")
        return

    yield from _schema_version_errors(doc)
    yield from _target_errors(doc)
    yield from _templates_errors(doc)
    yield from _songs_errors(doc)


def _schema_version_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    version = doc.get("schema")
    if version is None:
        yield _finding("T1", None, "The titles file has no 'schema' version field.")
    elif not _is_int(version):
        yield _finding("T1", None, f"'schema' must be an integer, got {_kind(version)}.")
    elif version != SCHEMA_VERSION:
        yield _finding(
            "T1",
            None,
            f"Titles-file schema {version} is not supported; this server serves "
            f"schema {SCHEMA_VERSION}.",
        )


def _target_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    if "timeline" in doc and not (isinstance(doc["timeline"], str) and doc["timeline"].strip()):
        yield _finding("T1", None, "'timeline' must be a non-empty timeline name when present.")


def _templates_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    """The block is optional — a file whose events are all PNG declares no templates.

    A missing one is not reported here even when a Text+ event needs it: T5 already names
    the template that could not be found, against the event that wanted it, which is the
    finding that says what to do about it.
    """
    templates = doc.get("templates", {})
    if not isinstance(templates, dict):
        yield _finding(
            "T1",
            None,
            "'templates' must be an object of name -> {clip, bin} when present.",
        )
        return
    for name, template in templates.items():
        where = f"templates.{name}"
        if not isinstance(template, dict):
            yield _finding("T1", name, f"'{where}' must be an object with a 'clip' name.")
            continue
        if not isinstance(template.get("clip"), str) or not template["clip"]:
            yield _finding("T1", name, f"'{where}.clip' must be a non-empty clip name.")
        if "bin" in template and not isinstance(template["bin"], str):
            yield _finding("T1", name, f"'{where}.bin' must be a string when present.")


def _songs_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    songs = doc.get("songs")
    if not isinstance(songs, list) or not songs:
        yield _finding("T1", None, "'songs' must be a non-empty array; a titles file needs one.")
        return
    for index, song in enumerate(songs):
        where = f"songs[{index}]"
        if not isinstance(song, dict):
            yield _finding("T1", None, f"'{where}' must be an object.")
            continue
        key = song.get("key") if isinstance(song.get("key"), str) else None
        if not key:
            yield _finding("T1", None, f"'{where}.key' must be a non-empty marker name.")
        events = song.get("events")
        if not isinstance(events, list):
            yield _finding("T1", key, f"'{where}.events' must be an array.")
            continue
        for position, event in enumerate(events):
            yield from _event_errors(event, f"{where}.events[{position}]", key)


def _event_errors(event: Any, where: str, song: str | None) -> Iterator[Finding]:
    if not isinstance(event, dict):
        yield _finding("T1", song, f"'{where}' must be an object.")
        return
    id = event.get("id") if isinstance(event.get("id"), str) else None
    if not id:
        yield _finding("T1", song, f"'{where}.id' must be a non-empty string.")
    if event.get("kind") not in KINDS:
        yield _finding("T1", id, f"'{where}.kind' must be one of: {', '.join(KINDS)}.")
    if "route" in event and event["route"] not in ROUTES:
        yield _finding("T1", id, f"'{where}.route' must be one of: {', '.join(ROUTES)}.")
    if "template" in event and not isinstance(event["template"], str):
        yield _finding("T1", id, f"'{where}.template' must be a template name.")
    if "text" in event and not (isinstance(event["text"], str) and event["text"].strip()):
        yield _finding(
            "T1",
            id,
            f"'{where}.text' must be the non-empty string to show; the server never "
            f"formats prose, so pass the final text.",
        )
    if "asset" in event and not (isinstance(event["asset"], str) and event["asset"].strip()):
        yield _finding(
            "T1",
            id,
            f"'{where}.asset' must be a non-empty path to the exported card.",
        )
    if "bin" in event and not isinstance(event["bin"], str):
        yield _finding("T1", id, f"'{where}.bin' must be a media-pool bin path when present.")
    for edge in ("in", "out"):
        if not _is_int(event.get(edge)):
            yield _finding(
                "T1",
                id,
                f"'{where}.{edge}' must be an integer frame offset from the song's marker, "
                f"got {event.get(edge)!r}.",
            )
    if "note" in event and not isinstance(event["note"], str):
        yield _finding("T1", id, f"'{where}.note' must be a string.")
    yield from _fade_errors(event, where, id)


def _fade_errors(event: dict[str, Any], where: str, id: str | None) -> Iterator[Finding]:
    if "fade" not in event:
        return
    fade = event["fade"]
    if not isinstance(fade, dict):
        yield _finding("T1", id, f"'{where}.fade' must be an object: {{\"in\": 24, \"out\": 24}}.")
        return
    for edge in ("in", "out"):
        if edge in fade and not (_is_int(fade[edge]) and fade[edge] >= 0):
            yield _finding(
                "T1",
                id,
                f"'{where}.fade.{edge}' must be a frame count of zero or more, "
                f"got {fade[edge]!r}.",
            )


# --- the structural pass ------------------------------------------------------------------


def validate_structure(doc: Any) -> list[Finding]:
    """Every rule answerable from the document alone. Never mutates it.

    A document that fails T1 is returned with T1 findings only: the later rules read
    fields whose types they can no longer trust, so running them would report noise.
    """
    shape = list(_shape_errors(doc))
    if shape:
        return ordered(shape)

    findings: list[Finding] = []
    findings += _duplicate_errors(doc)
    findings += _range_errors(doc)
    findings += _fade_fit_errors(doc)
    findings += _route_errors(doc)
    findings += _declared_template_errors(doc)
    findings += _empty_song_warnings(doc)
    return ordered(findings)


def _songs(doc: dict[str, Any]) -> list[dict[str, Any]]:
    songs: list[dict[str, Any]] = doc["songs"]
    return songs


def _events(doc: dict[str, Any]) -> Iterator[tuple[dict[str, Any], dict[str, Any]]]:
    """Every event with the song it belongs to — the pair almost every rule needs."""
    for song in _songs(doc):
        for event in song["events"]:
            yield song, event


def _duplicate_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    """T2: two events with one id, or two songs with one key.

    Both are reported rather than merged. A duplicate id makes the apply report
    ambiguous, and a duplicate key would silently place one song's titles twice.
    """
    yield from _repeats([str(song["key"]) for song in _songs(doc)], "song key")
    yield from _repeats([str(event["id"]) for _, event in _events(doc)], "event id")


def _repeats(values: Sequence[str], what: str) -> Iterator[Finding]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            yield _finding("T2", value, f"Duplicate {what} {value!r}.")
        seen.add(value)


def _range_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    """T3: half-open ranges, so an event with no frames in it is an error, not a no-op."""
    for _, event in _events(doc):
        if int(event["out"]) <= int(event["in"]):
            yield _finding(
                "T3",
                str(event["id"]),
                f"in {event['in']} is not before out {event['out']}, so the title would "
                f"have no frames.",
            )


def _fade_fit_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    """T4: a fade longer than the title it rides on never reaches full opacity."""
    for _, event in _events(doc):
        duration = int(event["out"]) - int(event["in"])
        if duration <= 0:
            continue  # T3 already said so; a second finding on the same event is noise
        fade_in, fade_out = fades(event)
        if fade_in + fade_out > duration:
            yield _finding(
                "T4",
                str(event["id"]),
                f"Fades of {fade_in} in and {fade_out} out do not fit in {duration} frames.",
            )


def fades(event: Mapping[str, Any]) -> tuple[int, int]:
    """An event's fade lengths in frames; a missing ``fade`` block means a hard cut."""
    fade = event.get("fade")
    if not isinstance(fade, Mapping):
        return (0, 0)
    return (int(fade.get("in", 0)), int(fade.get("out", 0)))


def _route_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    """T6: the two routes take different fields, and neither ignores the other's.

    A ``text`` on a PNG event is the mistake worth catching loudest: the words are baked
    into the card, so the file would read as if it said them while the placed title showed
    whatever was exported — a disagreement nothing downstream can see.
    """
    for _, event in _events(doc):
        id = str(event["id"])
        route = route_of(event)
        wanted, unwanted = (
            ("asset", ("text", "template")) if route == PNG else ("text", ("asset", "bin"))
        )
        if not event.get(wanted):
            yield _finding(
                "T6",
                id,
                f"A {route} event needs {wanted!r}, and this one has none.",
            )
        for field in unwanted:
            if field in event:
                yield _finding(
                    "T6",
                    id,
                    f"{field!r} means nothing on a {route} event; it is a "
                    f"{TEXTPLUS if route == PNG else PNG} field.",
                )


def route_of(event: Mapping[str, Any]) -> str:
    return str(event.get("route", DEFAULT_ROUTE))


def template_of(event: Mapping[str, Any]) -> str:
    """The template an event is placed from: its own, or the one its kind implies."""
    return str(event.get("template", event["kind"]))


def _declared_template_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    """T5's first leg: a template the file never declared cannot be looked up at all."""
    declared = set(doc.get("templates", {}))
    names = ", ".join(sorted(declared)) if declared else "none"
    for _, event in _events(doc):
        if route_of(event) == PNG:
            continue  # a PNG card is its own artwork; there is no template to resolve
        wanted = template_of(event)
        if wanted not in declared:
            yield _finding(
                "T5",
                str(event["id"]),
                f"No template called {wanted!r} is declared; the file declares {names}.",
            )


def _empty_song_warnings(doc: dict[str, Any]) -> Iterator[Finding]:
    for song in _songs(doc):
        if not song["events"]:
            yield _finding("W1", str(song["key"]), "This song has no title events.")


# --- the asset pass -----------------------------------------------------------------------


def validate_assets(
    doc: dict[str, Any],
    *,
    base: Path,
) -> tuple[list[Finding], dict[str, Asset]]:
    """T10-T11: every PNG card, counted off disk, judged against the event that wants it.

    The resolved cards come back beside the findings for the same reason the templates do:
    the apply imports the very files the rules counted, so a card cannot be judged at one
    length and placed at another. Events on the Text+ route are not represented at all, and
    neither is a PNG event with no ``asset``: T6 has already said that is what is wrong with
    it, and resolving an empty path would answer "nothing on disk" about the wrong thing.
    """
    findings: list[Finding] = []
    resolved: dict[str, Asset] = {}
    for song, event in _events(doc):
        if route_of(event) != PNG or not event.get("asset"):
            continue
        asset = resolve_asset(event, str(song["key"]), base=base)
        resolved[asset.event] = asset
        findings += _asset_errors(asset, int(event["out"]) - int(event["in"]), fades(event))
    return ordered(findings), resolved


def _asset_errors(asset: Asset, duration: int, fade: tuple[int, int]) -> Iterator[Finding]:
    """One card's own rules: it is there (T10), and it is the right length (T11)."""
    if asset.missing:
        what = "sequence" if asset.is_sequence else "image"
        yield _finding(
            "T10",
            asset.event,
            f"No {what} stands behind {asset.declared!r} (looked at {asset.path}).",
        )
        return
    if duration <= 0:
        return  # T3 already said the event has no frames; a length check would repeat it
    if asset.is_sequence and asset.frames != duration:
        yield _finding(
            "T11",
            asset.event,
            f"{asset.declared!r} is {asset.frames} frame(s) and the event asks for "
            f"{duration}. A sequence is placed whole — the fade-out is in its last frames, "
            f"so it is neither trimmed nor stretched.",
        )
    if not asset.is_sequence and any(fade):
        yield _finding(
            "T11",
            asset.event,
            f"{asset.declared!r} is one image, held by freezing it, so the "
            f"{fade[0]}-in/{fade[1]}-out fade this event asks for cannot be shown.",
        )


# --- the project pass ---------------------------------------------------------------------


def validate_project(
    doc: dict[str, Any],
    *,
    anchors: Mapping[str, Sequence[int]],
    templates: Sequence[TemplateFacts],
    span: tuple[int, int],
) -> list[Finding]:
    """Every rule that needs the timeline and the pool, over facts already read from them.

    ``anchors`` maps a marker name to every blue-marker record frame carrying it, so
    "no such song" and "that song is marked twice" are one rule with two messages.
    ``span`` is the timeline's own ``[start, end)`` in record frames.
    """
    findings: list[Finding] = []
    findings += _template_resolution_errors(doc, templates)
    findings += _anchor_errors(doc, anchors)
    placed = plan(doc, anchors)
    findings += _bounds_errors(placed, span)
    findings += _overlap_errors(placed)
    findings += _unused_anchor_warnings(doc, anchors)
    return ordered(findings)


def _template_resolution_errors(
    doc: dict[str, Any],
    templates: Sequence[TemplateFacts],
) -> Iterator[Finding]:
    """T5's second leg: the declared template must be exactly one clip in the pool."""
    used = {template_of(event) for _, event in _events(doc)}
    for facts in templates:
        if facts.name not in used or facts.matches == 1:
            continue
        where = f" in {facts.bin_path!r}" if facts.bin_path else ""
        if facts.matches == 0:
            yield _finding(
                "T5",
                facts.name,
                f"No clip called {facts.clip!r}{where} is in the media pool.",
            )
        else:
            yield _finding(
                "T5",
                facts.name,
                f"{facts.matches} clips are called {facts.clip!r}{where} "
                f"(in {', '.join(facts.found_in)}); name the bin to pick one.",
            )


def _anchor_errors(
    doc: dict[str, Any],
    anchors: Mapping[str, Sequence[int]],
) -> Iterator[Finding]:
    """T7: the join to the timeline. Both failure modes place titles on the wrong music."""
    for song in _songs(doc):
        key = str(song["key"])
        found = anchors.get(key, ())
        if len(found) == 1:
            continue
        if not found:
            known = ", ".join(sorted(anchors)) or "<none>"
            yield _finding(
                "T7",
                key,
                f"No {ANCHOR_COLOR.lower()} marker on the timeline is named {key!r}. "
                f"It carries: {known}.",
            )
        else:
            yield _finding(
                "T7",
                key,
                f"{len(found)} {ANCHOR_COLOR.lower()} markers are named {key!r} "
                f"(at record {', '.join(str(frame) for frame in sorted(found))}); "
                f"a song key must name exactly one.",
            )


def plan(doc: dict[str, Any], anchors: Mapping[str, Sequence[int]]) -> list[Event]:
    """Every event the file asks for, positioned absolutely, in document order.

    Songs whose key does not resolve to exactly one marker are left out: T7 has already
    said so, and a made-up anchor would produce findings about a position no one asked
    for. The rules that follow therefore judge only what could actually be placed.
    """
    planned: list[Event] = []
    for song in _songs(doc):
        found = anchors.get(str(song["key"]), ())
        if len(found) != 1:
            continue
        anchor = found[0]
        for event in song["events"]:
            fade_in, fade_out = fades(event)
            png = route_of(event) == PNG
            planned.append(
                Event(
                    id=str(event["id"]),
                    song=str(song["key"]),
                    kind=str(event["kind"]),
                    route=route_of(event),
                    template="" if png else template_of(event),
                    text="" if png else str(event.get("text", "")),
                    asset=str(event.get("asset", "")) if png else "",
                    record_in=anchor + int(event["in"]),
                    duration=int(event["out"]) - int(event["in"]),
                    fade_in=fade_in,
                    fade_out=fade_out,
                    note=str(event.get("note", "")),
                )
            )
    return planned


def _bounds_errors(planned: Sequence[Event], span: tuple[int, int]) -> Iterator[Finding]:
    """T9: a record frame outside the timeline is not clamped — it is placed nowhere."""
    start, end = span
    for event in planned:
        if event.record_in < start or event.record_out > end:
            yield _finding(
                "T9",
                event.id,
                f"Record {event.record_in}-{event.record_out} falls outside the timeline's "
                f"{start}-{end}.",
            )


def _overlap_errors(planned: Sequence[Event]) -> Iterator[Finding]:
    """T8: one track, so two titles over one frame cannot both be placed.

    Resolve would not refuse it — it slides the second append clear of the first and
    reports success, which puts a title somewhere nobody chose. Reported against the
    later event, since that is the one that would move.
    """
    for position, event in enumerate(planned):
        for earlier in planned[:position]:
            if ranges_overlap(
                earlier.record_in, earlier.record_out, event.record_in, event.record_out
            ):
                yield _finding(
                    "T8",
                    event.id,
                    f"Overlaps {earlier.id!r} — {event.record_in}-{event.record_out} against "
                    f"{earlier.record_in}-{earlier.record_out} in record frames.",
                )


def _unused_anchor_warnings(
    doc: dict[str, Any],
    anchors: Mapping[str, Sequence[int]],
) -> Iterator[Finding]:
    titled = {str(song["key"]) for song in _songs(doc)}
    for key in sorted(anchors):
        if key not in titled:
            yield _finding("W2", key, f"The song marked {key!r} has no entry in the titles file.")


__all__ = [
    "ANCHOR_COLOR",
    "PNG",
    "RULE_DESCRIPTIONS",
    "TEXTPLUS",
    "Event",
    "TemplateFacts",
    "fades",
    "parse_failure_finding",
    "plan",
    "route_of",
    "template_of",
    "validate_assets",
    "validate_project",
    "validate_structure",
]
