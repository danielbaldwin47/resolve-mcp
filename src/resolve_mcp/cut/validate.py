"""The cut-file validation rules: 11 hard errors, 3 warnings, one implementation.

The list is identical in the ``validate_cut`` dry run and in ``build_timeline``'s
pre-flight, so it lives here once and both call it. A failing file must abort before
Resolve is touched — a half-built timeline is the outcome this file exists to prevent.

Two passes, because they need different things:

* :func:`validate_structure` reads the document alone — no project, no connection. It
  answers everything about shape, ids, ranges, takes and overlay anchoring (E1-E4 for
  undeclared aliases, E7 for an undeclared audio alias, E8-E10, W1-W2, W8). W3-W7 are
  ``virtual_transcript``'s over this same document — one file, one numbering.
* :func:`validate_project` takes clip facts already gathered from the media pool and
  answers everything about the media behind the aliases (E4-E7). It is a pure function
  over those facts, so every rule in this file is unit-testable without Resolve.

E11 is the build-time rule — a locked target track fails *silently* in the Resolve API,
so the build checks it and reports it in the same shape as everything else.

Every finding is ``{rule, id, message, fix_hint}``: the rule so the agent can look it up,
the id so it knows which segment to edit, and a fix hint so it does not have to guess.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Final, TypeGuard

from ..findings import Finding, ordered
from ..logging_config import get_logger
from ..timing import duration_frames, ranges_overlap
from .schema import SCHEMA_VERSION

log = get_logger("cut")

DEFAULT_MIN_SEGMENT_FRAMES: Final = 12
"""W1's flash-frame guard. A warning, never a block — the creative call stays Claude's."""

FPS_TOLERANCE: Final = 0.01
"""Reported rates carry float noise; real rate differences (23.976 vs 24) are far wider."""

FIRST_OVERLAY_TRACK: Final = 2
"""V1 is the sequential cut's own track, so the lowest an overlay can claim is V2."""

MAX_OVERLAY_TRACK: Final = 8
"""A ceiling on ``track``, because the build adds every track below the one it is given.

Not a Resolve limit — a typo guard. ``"track": 99`` is an author slip, and without this it
would grow a 99-track timeline and report success. Eight layers is far past what the
pillar has ever used; a cut that genuinely needs more is a conversation, not a typo.
"""

RULE_DESCRIPTIONS: Final[dict[str, str]] = {
    "E1": "JSON parses; schema-valid; schema version supported",
    "E2": "ids unique across segments, gaps and overlays (one namespace)",
    "E3": "in < out everywhere; a gap runs at least one frame",
    "E4": "every alias resolves to exactly one media-pool clip",
    "E5": "in/out inside clip media bounds",
    "E6": "source fps matches timeline fps (stills exempt)",
    "E7": "audio block resolves, has audio, bounds valid",
    "E8": "alternate duration equals main duration",
    "E9": "overlay anchor exists; offset inside anchor; span inside the total V1",
    "E10": "overlays on one track do not overlap each other",
    "E11": "build-time: target tracks unlocked; connection and creation failures",
    "W1": "segment or gap shorter than min_segment_frames (flash-frame guard)",
    "W2": "V1 total does not match the master-audio span",
    "W8": "the cut ends on black that nothing runs under, so it materialises as nothing",
}

_FIX_HINTS: Final[dict[str, str]] = {
    "E1": "Call get_cut_schema and match the annotated example exactly.",
    "E2": "Give every segment, gap and overlay its own id — they share one namespace.",
    "E3": "Ranges are half-open [in, out); make out strictly greater than in. A gap's "
    "frame count is the black itself, so it must be at least 1.",
    "E4": "Fix the alias in sources, or import the clip and re-run validate_cut.",
    "E5": "Call inspect_clip for the clip's media bounds and pull the range inside them.",
    "E6": "Conform the source first, or set timeline.fps to the rate the sources are at.",
    "E7": "Point audio.source at a clip that has audio and keep its range inside the media.",
    "E8": "Alternates must match the main take frame for frame — swap_take cannot ripple.",
    "E9": "Anchor the overlay to a segment that exists, at an offset inside it.",
    "E10": "Move one overlay, shorten it, or put it on its own 'track' — one track holds "
    "one clip per frame.",
    "E11": "Unlock the track in Resolve's timeline header and build again.",
    "W1": "Lengthen the segment or gap, or keep it if the flash is deliberate.",
    "W2": "Expected when the cut opens cold or runs past the mix; otherwise check the ends.",
    "W8": "Anchor an overlay over the trailing gap, or let the master mix run past the "
    "last picture — either makes the black real. Otherwise drop the gap.",
}


@dataclass(frozen=True)
class ClipFacts:
    """What the rules need to know about one media-pool clip.

    Gathered once from Resolve and then handed to pure functions, so the rules never
    hold a Resolve handle. Bounds are half-open — ``end_exclusive`` is Resolve's last
    frame plus one — matching the cut file's own convention. A bound is ``None`` when
    Resolve reported nothing and nothing could derive it: that means "cannot verify",
    never "0-0", so the range rules fail open on it (#46).
    """

    name: str
    bin_path: str | None
    start: int | None
    end_exclusive: int | None
    fps: float | None
    has_audio: bool = False
    is_still: bool = False


def _finding(rule: str, id: str | None, message: str, fix_hint: str | None = None) -> Finding:
    return Finding(rule=rule, id=id, message=message, fix_hint=fix_hint or _FIX_HINTS[rule])


def parse_failure_finding(detail: str) -> Finding:
    """E1: the file is on disk but is not JSON, so no other rule can be answered."""
    return _finding("E1", None, f"The cut file is not valid JSON: {detail}.")


def locked_track_finding(track: str) -> Finding:
    """E11: Resolve accepts an append onto a locked track and silently drops it."""
    return _finding(
        "E11",
        track,
        f"Track {track} is locked; Resolve would report the append as successful and "
        f"place nothing.",
    )


# --- shape (E1) -------------------------------------------------------------------------


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> TypeGuard[int | float]:
    return _is_int(value) or isinstance(value, float)


def _shape_errors(doc: Any) -> Iterator[Finding]:
    """Everything that makes the document unreadable, reported as E1."""
    if not isinstance(doc, dict):
        yield _finding("E1", None, f"The cut file must be a JSON object, got {_kind(doc)}.")
        return

    yield from _schema_version_errors(doc)
    yield from _timeline_errors(doc)
    yield from _sources_errors(doc)
    yield from _audio_shape_errors(doc)
    yield from _segments_errors(doc)
    yield from _overlays_errors(doc)


def _kind(value: Any) -> str:
    return type(value).__name__


def _schema_version_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    version = doc.get("schema")
    if version is None:
        yield _finding("E1", None, "The cut file has no 'schema' version field.")
    elif not _is_int(version):
        yield _finding("E1", None, f"'schema' must be an integer, got {_kind(version)}.")
    elif version != SCHEMA_VERSION:
        yield _finding(
            "E1",
            None,
            f"Cut-file schema {version} is not supported; this server serves "
            f"schema {SCHEMA_VERSION}.",
        )


def _timeline_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    timeline = doc.get("timeline")
    if not isinstance(timeline, dict):
        yield _finding("E1", None, "'timeline' must be an object with 'name' and 'fps'.")
        return
    if not isinstance(timeline.get("name"), str) or not timeline["name"].strip():
        yield _finding("E1", None, "'timeline.name' must be a non-empty string.")
    fps = timeline.get("fps")
    if not _is_number(fps) or fps <= 0:
        yield _finding("E1", None, f"'timeline.fps' must be a positive number, got {fps!r}.")
    if "bin" in timeline and not isinstance(timeline["bin"], str):
        yield _finding("E1", None, "'timeline.bin' must be a string when present.")


def _sources_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    sources = doc.get("sources")
    if not isinstance(sources, dict) or not sources:
        yield _finding("E1", None, "'sources' must be a non-empty object of alias -> clip.")
        return
    for alias, source in sources.items():
        where = f"sources.{alias}"
        if not isinstance(source, dict):
            yield _finding("E1", alias, f"'{where}' must be an object with a 'clip' name.")
            continue
        if not isinstance(source.get("clip"), str) or not source["clip"]:
            yield _finding("E1", alias, f"'{where}.clip' must be a non-empty clip name.")
        if "bin" in source and not isinstance(source["bin"], str):
            yield _finding("E1", alias, f"'{where}.bin' must be a string when present.")
        if "sync_offset" in source and not _is_int(source["sync_offset"]):
            yield _finding("E1", alias, f"'{where}.sync_offset' must be an integer frame count.")


def _audio_shape_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    if "audio" not in doc:
        return
    audio = doc["audio"]
    if not isinstance(audio, dict):
        yield _finding("E1", None, "'audio' must be an object with 'source', 'in' and 'out'.")
        return
    if not isinstance(audio.get("source"), str):
        yield _finding("E1", None, "'audio.source' must be a source alias.")
    yield from _range_shape_errors(audio, "audio", None)


def _range_shape_errors(item: dict[str, Any], where: str, id: str | None) -> Iterator[Finding]:
    for edge in ("in", "out"):
        if not _is_int(item.get(edge)):
            yield _finding(
                "E1",
                id,
                f"'{where}.{edge}' must be an integer frame number, got {item.get(edge)!r}.",
            )


def _segments_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    segments = doc.get("segments")
    if not isinstance(segments, list) or not segments:
        yield _finding("E1", None, "'segments' must be a non-empty array; a cut needs content.")
        return
    picture = 0
    black = 0
    for index, segment in enumerate(segments):
        where = f"segments[{index}]"
        if not isinstance(segment, dict):
            yield _finding("E1", None, f"'{where}' must be an object.")
            continue
        id = segment.get("id") if isinstance(segment.get("id"), str) else None
        if not id:
            yield _finding("E1", None, f"'{where}.id' must be a non-empty string.")
        if is_gap(segment):
            black += 1
            yield from _gap_shape_errors(segment, where, id)
            continue
        picture += 1
        if not isinstance(segment.get("source"), str):
            yield _finding("E1", id, f"'{where}.source' must be a source alias.")
        yield from _range_shape_errors(segment, where, id)
        if "audio" in segment and not isinstance(segment["audio"], bool):
            yield _finding("E1", id, f"'{where}.audio' must be true or false.")
        if "note" in segment and not isinstance(segment["note"], str):
            yield _finding("E1", id, f"'{where}.note' must be a string.")
        yield from _alternates_errors(segment, where, id)
    if black and not picture:
        yield _finding(
            "E1",
            None,
            "'segments' is nothing but gaps; a cut needs at least one picture segment for "
            "the black to be an absence of.",
        )


_GAP_FORBIDS: Final = ("source", "in", "out", "audio", "alternates")
"""Everything a gap cannot carry, because black plays no clip and holds no take."""


def _gap_shape_errors(segment: dict[str, Any], where: str, id: str | None) -> Iterator[Finding]:
    """A gap is an id, a frame count, and nothing else — a half-gap is an author mid-edit."""
    if not _is_int(segment["gap"]):
        yield _finding(
            "E1",
            id,
            f"'{where}.gap' must be an integer frame count, got {segment['gap']!r}.",
        )
    for field in _GAP_FORBIDS:
        if field in segment:
            yield _finding(
                "E1",
                id,
                f"'{where}' is a gap, so it must not carry '{field}': black plays no clip. "
                f"Drop '{field}', or drop 'gap' and make it a segment.",
            )
    if "note" in segment and not isinstance(segment["note"], str):
        yield _finding("E1", id, f"'{where}.note' must be a string.")


def _alternates_errors(segment: dict[str, Any], where: str, id: str | None) -> Iterator[Finding]:
    if "alternates" not in segment:
        return
    alternates = segment["alternates"]
    if not isinstance(alternates, list):
        yield _finding("E1", id, f"'{where}.alternates' must be an array.")
        return
    for index, alternate in enumerate(alternates):
        at = f"{where}.alternates[{index}]"
        if not isinstance(alternate, dict):
            yield _finding("E1", id, f"'{at}' must be an object.")
            continue
        if not isinstance(alternate.get("source"), str):
            yield _finding("E1", id, f"'{at}.source' must be a source alias.")
        yield from _range_shape_errors(alternate, at, id)


def _overlays_errors(doc: dict[str, Any]) -> Iterator[Finding]:
    if "overlays" not in doc:
        return
    overlays = doc["overlays"]
    if not isinstance(overlays, list):
        yield _finding("E1", None, "'overlays' must be an array.")
        return
    for index, overlay in enumerate(overlays):
        where = f"overlays[{index}]"
        if not isinstance(overlay, dict):
            yield _finding("E1", None, f"'{where}' must be an object.")
            continue
        id = overlay.get("id") if isinstance(overlay.get("id"), str) else None
        if not id:
            yield _finding("E1", None, f"'{where}.id' must be a non-empty string.")
        if not isinstance(overlay.get("source"), str):
            yield _finding("E1", id, f"'{where}.source' must be a source alias.")
        yield from _range_shape_errors(overlay, where, id)
        yield from _anchor_shape_errors(overlay, where, id)
        yield from _track_shape_errors(overlay, where, id)


def _track_shape_errors(overlay: dict[str, Any], where: str, id: str | None) -> Iterator[Finding]:
    """The layer an overlay rides on: optional, defaults to V2, never V1.

    The bounds are shape rather than a rule of their own because they are answerable from
    the one value — nothing else in the document has to be consulted to know that V1 is the
    segments' track and that V99 is a typo.
    """
    if "track" not in overlay:
        return
    track = overlay["track"]
    if not _is_int(track):
        yield _finding("E1", id, f"'{where}.track' must be an integer track index.")
        return
    if not FIRST_OVERLAY_TRACK <= track <= MAX_OVERLAY_TRACK:
        yield _finding(
            "E1",
            id,
            f"'{where}.track' is {track}; overlays ride above the cut, so the index must be "
            f"between {FIRST_OVERLAY_TRACK} (V{FIRST_OVERLAY_TRACK}, the default) and "
            f"{MAX_OVERLAY_TRACK}. V1 belongs to the segments.",
        )


def _anchor_shape_errors(overlay: dict[str, Any], where: str, id: str | None) -> Iterator[Finding]:
    over = overlay.get("over")
    if not isinstance(over, dict):
        yield _finding(
            "E1",
            id,
            f"'{where}.over' must be an anchor object: "
            '{"segment": "<segment id>", "offset": <frames>}.',
        )
        return
    if not isinstance(over.get("segment"), str):
        yield _finding("E1", id, f"'{where}.over.segment' must be a segment id.")
    if not _is_int(over.get("offset")):
        yield _finding("E1", id, f"'{where}.over.offset' must be an integer frame count.")


# --- the structural pass ----------------------------------------------------------------


def validate_structure(
    doc: Any,
    *,
    min_segment_frames: int = DEFAULT_MIN_SEGMENT_FRAMES,
) -> list[Finding]:
    """Every rule answerable from the document alone. Never mutates it.

    A document that fails E1 is returned with E1 findings only: the later rules read
    fields whose types they can no longer trust, so running them would report noise.
    """
    shape = list(_shape_errors(doc))
    if shape:
        return ordered(shape)

    findings: list[Finding] = []
    findings += _id_errors(doc)
    findings += _range_errors(doc)
    findings += _declared_alias_errors(doc)
    findings += _alternate_duration_errors(doc)
    findings += _overlay_errors(doc)
    findings += _segment_length_warnings(doc, min_segment_frames)
    findings += _audio_span_warnings(doc)
    findings += _trailing_black_warnings(doc)
    return ordered(findings)


def is_gap(entry: Any) -> bool:
    """Whether a ``segments`` entry is black rather than a shot.

    Public because the build, the swap and the read-back all walk the same array and each
    has to answer this the same way: a second definition of what black looks like is how a
    gap ends up placed on one side of the seam and ignored on the other.
    """
    return isinstance(entry, dict) and "gap" in entry


def entry_duration(entry: dict[str, Any]) -> int:
    """How much record time one ``segments`` entry takes — a gap's is the black itself."""
    if is_gap(entry):
        return int(entry["gap"])
    return duration_frames(entry["in"], entry["out"])


def overlay_track(overlay: dict[str, Any]) -> int:
    """Which video track an overlay rides on. Absent means V2, the layer above the cut."""
    track = overlay.get("track")
    return int(track) if _is_int(track) else FIRST_OVERLAY_TRACK


def _entries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """``segments`` as authored: picture and black, in the order that lays out the V1."""
    entries: list[dict[str, Any]] = doc["segments"]
    return entries


def shots(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the entries that place a clip — *not* the same list as ``doc["segments"]``.

    Every rule about *media* — aliases, bounds, rates, takes — reads this rather than the
    raw array, so a gap is skipped by all of them at once instead of by a guard each of
    them could be written without. Public alongside :func:`gaps` because the build and the
    summaries need the same two counts, and counting them by hand at each call site is how
    one of them ends up disagreeing about what black is.
    """
    return [entry for entry in _entries(doc) if not is_gap(entry)]


def gaps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the entries that are black."""
    return [entry for entry in _entries(doc) if is_gap(entry)]


def _overlays(doc: dict[str, Any]) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = doc.get("overlays") or []
    return overlays


def _id_errors(doc: dict[str, Any]) -> list[Finding]:
    """E2: segments, gaps and overlays share one id namespace."""
    seen: set[str] = set()
    findings: list[Finding] = []
    for item in (*_entries(doc), *_overlays(doc)):
        id = str(item["id"])
        if id in seen:
            findings.append(_finding("E2", id, f"The id {id!r} is used more than once."))
        seen.add(id)
    return findings


def _range_errors(doc: dict[str, Any]) -> list[Finding]:
    """E3: half-open ranges need in < out, or they name no frames at all."""
    findings: list[Finding] = []
    for segment in shots(doc):
        findings += _range_error(segment, str(segment["id"]), "Segment")
        for index, alternate in enumerate(segment.get("alternates") or []):
            findings += _range_error(
                alternate, str(segment["id"]), f"Alternate {index} of segment"
            )
    for overlay in _overlays(doc):
        findings += _range_error(overlay, str(overlay["id"]), "Overlay")
    for gap in gaps(doc):
        findings += _gap_length_error(gap)
    audio = doc.get("audio")
    if isinstance(audio, dict):
        findings += _range_error(audio, None, "The audio block")
    return findings


def _range_error(item: dict[str, Any], id: str | None, subject: str) -> list[Finding]:
    if item["in"] < item["out"]:
        return []
    named = f"{subject} {id!r}" if id else subject
    return [
        _finding(
            "E3",
            id,
            f"{named} has in={item['in']} and out={item['out']}; a half-open range "
            f"names no frames unless in < out.",
        )
    ]


def _gap_length_error(gap: dict[str, Any]) -> list[Finding]:
    """E3 for black: no frames of it is not a device, it is a line that does nothing."""
    frames = int(gap["gap"])
    if frames >= 1:
        return []
    id = str(gap["id"])
    return [
        _finding(
            "E3",
            id,
            f"Gap {id!r} runs {frames} frames; black has to run at least one frame to be "
            f"anything at all.",
        )
    ]


def _declared_alias_errors(doc: dict[str, Any]) -> list[Finding]:
    """E4/E7: an alias that is not in the sources table resolves to nothing at all."""
    declared = set(doc["sources"])
    findings: list[Finding] = []
    for alias, id in _alias_uses(doc):
        if alias not in declared:
            findings.append(
                _finding(
                    "E4",
                    id,
                    f"The alias {alias!r} is not declared in 'sources'; declared aliases "
                    f"are: {_listed(sorted(declared))}.",
                )
            )
    audio = doc.get("audio")
    if isinstance(audio, dict) and audio["source"] not in declared:
        findings.append(
            _finding(
                "E7",
                None,
                f"The audio alias {audio['source']!r} is not declared in 'sources'.",
            )
        )
    return findings


def _alias_uses(doc: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Every (alias, owning id) the cut references, audio block excluded."""
    for segment in shots(doc):
        yield str(segment["source"]), str(segment["id"])
        for alternate in segment.get("alternates") or []:
            yield str(alternate["source"]), str(segment["id"])
    for overlay in _overlays(doc):
        yield str(overlay["source"]), str(overlay["id"])


def _listed(names: Sequence[str]) -> str:
    return ", ".join(names) if names else "none"


def _alternate_duration_errors(doc: dict[str, Any]) -> list[Finding]:
    """E8: a take selector cannot change length, so an alternate cannot either."""
    findings: list[Finding] = []
    for segment in shots(doc):
        main = duration_frames(segment["in"], segment["out"])
        for index, alternate in enumerate(segment.get("alternates") or []):
            alternate_duration = duration_frames(alternate["in"], alternate["out"])
            if alternate_duration != main:
                findings.append(
                    _finding(
                        "E8",
                        str(segment["id"]),
                        f"Alternate {index} runs {alternate_duration} frames but the main "
                        f"take runs {main}; every alternate must match frame for frame.",
                    )
                )
    return findings


def positions(doc: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Each ``segments`` id to its computed ``(start, duration)`` — sequential V1, in order.

    The overlay rules and the build both place against these numbers, and they have to be
    the same numbers: an offset validated against one layout and built against another
    would put the overlay somewhere nobody checked.

    A gap is here with the rest. It places no clip, but it occupies record time and so it
    moves everything after it — and an overlay may anchor over it, which is what makes a
    V2 bridge across black expressible at all. Positions stay *computed* either way: black
    is a duration in the array, never a frame number an author had to keep up to date.
    """
    placed: dict[str, tuple[int, int]] = {}
    at = 0
    for entry in _entries(doc):
        duration = entry_duration(entry)
        placed[str(entry["id"])] = (at, duration)
        at += duration
    return placed


def placements(doc: dict[str, Any], start: int) -> dict[str, tuple[int, int]]:
    """Each segment id to its ``(record frame, duration)`` on a timeline starting at ``start``.

    The one place a cut's own offsets become absolute frames. A build sends these as
    ``recordFrame`` and a swap finds a shot by them, so a second derivation of the same sum
    somewhere else is a swap that quietly reads the wrong shot — there is only this one.
    """
    return {
        id: (start + offset, duration) for id, (offset, duration) in positions(doc).items()
    }


def total_frames(doc: dict[str, Any]) -> int:
    """The V1 span: sequential entries, so the sum of their durations, black included."""
    return sum(entry_duration(entry) for entry in _entries(doc))


def overlay_positions(doc: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Each overlay id to its computed ``(start, duration)`` on V2 — start measured from
    the cut's first frame, exactly as :func:`positions` measures a segment's.

    An overlay names no absolute frame: its position is its anchor segment's computed
    start plus the offset, which is what makes it ride the content it covers through a
    tightening pass. E9 judges these numbers and the build places against them, from this
    one function — a position validated one way and built another is exactly the drift
    anchoring exists to prevent. An overlay whose anchor does not exist has no position
    and is absent here; E9 has already refused that document.
    """
    return {str(overlay["id"]): at for overlay, at in _anchored(doc) if at is not None}


def _anchored(doc: dict[str, Any]) -> Iterator[tuple[dict[str, Any], tuple[int, int] | None]]:
    """Every overlay with its resolved span, or ``None`` when its anchor is not a segment."""
    placed = positions(doc)
    for overlay in _overlays(doc):
        anchor = placed.get(str(overlay["over"]["segment"]))
        yield (
            overlay,
            None
            if anchor is None
            else (
                anchor[0] + int(overlay["over"]["offset"]),
                duration_frames(overlay["in"], overlay["out"]),
            ),
        )


def _overlay_errors(doc: dict[str, Any]) -> list[Finding]:
    """E9 and E10, both judged on the absolute positions resolved from the anchors."""
    placed = positions(doc)
    total = total_frames(doc)
    findings: list[Finding] = []
    spans: dict[int, list[tuple[str, int, int]]] = {}

    for overlay, resolved in _anchored(doc):
        id = str(overlay["id"])
        anchor = str(overlay["over"]["segment"])
        offset = int(overlay["over"]["offset"])
        if resolved is None:
            findings.append(
                _finding(
                    "E9",
                    id,
                    f"Overlay {id!r} is anchored to segment {anchor!r}, which does not exist.",
                )
            )
            continue
        _, anchor_duration = placed[anchor]
        if not 0 <= offset < anchor_duration:
            findings.append(
                _finding(
                    "E9",
                    id,
                    f"Overlay {id!r} sits at offset {offset} of segment {anchor!r}, which "
                    f"runs {anchor_duration} frames; the offset must land inside it.",
                )
            )
            continue
        at, duration = resolved
        if at + duration > total:
            findings.append(
                _finding(
                    "E9",
                    id,
                    f"Overlay {id!r} covers frames {at}-{at + duration} but the cut is "
                    f"{total} frames long; it must land inside the cut.",
                )
            )
            continue
        spans.setdefault(overlay_track(overlay), []).append((id, at, at + duration))

    for track in sorted(spans):
        findings += _overlap_errors(spans[track], track)
    return findings


def _overlap_errors(spans: list[tuple[str, int, int]], track: int) -> list[Finding]:
    """E10: one track holds one clip per frame, and Resolve will not overwrite on overlap.

    Judged per track, because that is the constraint being modelled: two inserts that
    would collide on V2 are an ordinary stack once one of them names V3.

    Swept against the span reaching furthest right rather than the previous one: a short
    overlay sitting wholly inside a long one is not adjacent to it once sorted, and
    comparing neighbours alone would wave it through.
    """
    findings: list[Finding] = []
    ordered = sorted(spans, key=lambda span: (span[1], span[2], span[0]))
    furthest: tuple[str, int, int] | None = None
    for span in ordered:
        if furthest is not None and ranges_overlap(furthest[1], furthest[2], span[1], span[2]):
            findings.append(
                _finding(
                    "E10",
                    span[0],
                    f"Overlays {furthest[0]!r} ({furthest[1]}-{furthest[2]}) and {span[0]!r} "
                    f"({span[1]}-{span[2]}) cover the same frames of V{track}.",
                )
            )
        if furthest is None or span[2] > furthest[2]:
            furthest = span
    return findings


def _segment_length_warnings(doc: dict[str, Any], minimum: int) -> list[Finding]:
    """W1: a flash frame is usually a typo, occasionally the point. Never a block.

    Black is measured by the same guard as picture: two frames of it reads as a slip in a
    duration far more often than as a device.
    """
    findings: list[Finding] = []
    for entry in _entries(doc):
        duration = entry_duration(entry)
        if duration < minimum:
            subject = "Gap" if is_gap(entry) else "Segment"
            findings.append(
                _finding(
                    "W1",
                    str(entry["id"]),
                    f"{subject} {entry['id']!r} runs {duration} frames, under the "
                    f"{minimum}-frame flash guard.",
                )
            )
    return findings


def _audio_span_warnings(doc: dict[str, Any]) -> list[Finding]:
    """W2: a cold open or a tail is legal, so this reports and never blocks."""
    audio = doc.get("audio")
    if not isinstance(audio, dict):
        return []
    span = duration_frames(audio["in"], audio["out"])
    total = total_frames(doc)
    if span == total:
        return []
    return [
        _finding(
            "W2",
            None,
            f"The cut runs {total} frames over a {span}-frame master-audio span.",
        )
    ]


def _trailing_black_warnings(doc: dict[str, Any]) -> list[Finding]:
    """W8: black at the end of a cut is real only if something else runs under it.

    A gap places nothing, so a trailing one is record time with no clip on V1 — and a
    timeline ends at its last item, on any track. Ending on black therefore takes
    something that outlives the last picture: an overlay over the gap, as #46's ending
    inserts were, or the master mix still playing under it, which is the ordinary concert
    shape. With neither, the built timeline simply stops at the last picture and the
    device silently is not there.

    A warning rather than an error: the tail is the author's call, and a cut that ends a
    little long on purpose should not be blocked over it.
    """
    tail = _trailing_black(doc)
    if tail is None:
        return []
    at, total = tail
    if any(start + length > at for start, length in _appended_spans(doc)):
        return []
    return [
        _finding(
            "W8",
            None,
            f"The cut ends with {total - at} frames of black that nothing runs under; the "
            f"built timeline will end at frame {at}, on the last picture.",
        )
    ]


def _appended_spans(doc: dict[str, Any]) -> Iterator[tuple[int, int]]:
    """Every ``(start, duration)`` the build places off V1, in the cut's own frames.

    Only the tracks that can outlive the picture: overlays above the cut, and the master
    mix, which starts at the cut's first frame and runs its own declared length.
    """
    yield from overlay_positions(doc).values()
    audio = doc.get("audio")
    if isinstance(audio, dict):
        yield (0, duration_frames(audio["in"], audio["out"]))


def _trailing_black(doc: dict[str, Any]) -> tuple[int, int] | None:
    """``(frame the last picture ends on, the cut's total)``, or ``None`` if they are equal."""
    placed = positions(doc)
    ends = [
        start + duration
        for entry in _entries(doc)
        if not is_gap(entry)
        for start, duration in [placed[str(entry["id"])]]
    ]
    total = total_frames(doc)
    last = max(ends, default=0)
    return None if last >= total else (last, total)


# --- the project pass -------------------------------------------------------------------


def validate_project(doc: Any, clips: Sequence[ClipFacts]) -> list[Finding]:
    """Every rule that needs the media pool, over facts already gathered from it.

    ``clips`` is every clip in the pool. Aliases are matched here rather than by
    ``find_clip`` because validation reports *all* the failures at once — the agent
    should not have to fix one alias per round trip.
    """
    if _shape_is_unreadable(doc):
        return []
    resolved, findings = resolve_aliases(doc, clips)
    findings += _bounds_errors(doc, resolved)
    findings += _rate_errors(doc, resolved)
    findings += _audio_errors(doc, resolved)
    return ordered(findings)


def _shape_is_unreadable(doc: Any) -> bool:
    return bool(list(_shape_errors(doc)))


def resolve_aliases(
    doc: dict[str, Any], clips: Sequence[ClipFacts]
) -> tuple[dict[str, ClipFacts], list[Finding]]:
    """E4: alias -> exactly one clip. Ambiguity lists the bins the candidates sit in.

    Public because the build has to place the same clip the rules passed: resolving the
    alias a second way is how a cut gets validated against one clip and built from another.
    """
    resolved: dict[str, ClipFacts] = {}
    findings: list[Finding] = []
    for alias, source in doc["sources"].items():
        name = str(source["clip"])
        declared_bin = source.get("bin")
        matches = [
            clip
            for clip in clips
            if clip.name == name and (declared_bin is None or clip.bin_path == declared_bin)
        ]
        if len(matches) == 1:
            resolved[alias] = matches[0]
        elif not matches:
            where = f" in the bin {declared_bin!r}" if declared_bin else ""
            findings.append(
                _finding(
                    "E4",
                    alias,
                    f"The alias {alias!r} names the clip {name!r}{where}, which is not in "
                    f"the media pool.",
                    fix_hint="Import the clip, or fix the name or bin in 'sources'.",
                )
            )
        else:
            bins = _listed([str(clip.bin_path) for clip in matches])
            findings.append(
                _finding(
                    "E4",
                    alias,
                    f"The alias {alias!r} names {name!r}, which is in {len(matches)} bins: "
                    f"{bins}.",
                    fix_hint="Add a 'bin' to the source alias to say which one you mean.",
                )
            )
    return resolved, findings


def _bounds_errors(doc: dict[str, Any], resolved: dict[str, ClipFacts]) -> list[Finding]:
    """E5: every range has to name frames the media actually has."""
    findings: list[Finding] = []
    for segment in shots(doc):
        id = str(segment["id"])
        findings += _bounds_error(segment, resolved, id, f"Segment {id!r}")
        for index, alternate in enumerate(segment.get("alternates") or []):
            findings += _bounds_error(
                alternate, resolved, id, f"Alternate {index} of segment {id!r}"
            )
    for overlay in _overlays(doc):
        id = str(overlay["id"])
        findings += _bounds_error(overlay, resolved, id, f"Overlay {id!r}")
    return findings


def _outside_media(item: dict[str, Any], clip: ClipFacts) -> bool:
    """Whether a half-open range asks for frames the clip's media does not have.

    Unknown bounds cannot convict: when Resolve never reported the clip's extent the
    check fails open — the same stance E7's has-audio leg takes on an unreported
    channel count, because "Resolve did not say" is not evidence of an overrun.
    """
    if clip.start is None or clip.end_exclusive is None:
        return False
    return bool(item["in"] < clip.start or item["out"] > clip.end_exclusive)


def _overrun_message(item: dict[str, Any], clip: ClipFacts, subject: str) -> str:
    return (
        f"{subject} asks for frames {item['in']}-{item['out']} of {clip.name!r}, whose "
        f"media runs {clip.start}-{clip.end_exclusive}."
    )


def _bounds_error(
    item: dict[str, Any],
    resolved: dict[str, ClipFacts],
    id: str,
    subject: str,
) -> list[Finding]:
    clip = resolved.get(str(item["source"]))
    if clip is None:
        return []
    if clip.is_still:
        # A still has one frame of media and any duration on a timeline — the
        # end-frame workaround is what makes that exact. Bounds do not apply.
        return []
    if not _outside_media(item, clip):
        return []
    return [_finding("E5", id, _overrun_message(item, clip, subject))]


def _rate_errors(doc: dict[str, Any], resolved: dict[str, ClipFacts]) -> list[Finding]:
    """E6: a source at another rate would be silently retimed on the timeline.

    Stills are exempt by rule; a clip whose rate Resolve does not report (audio, and
    anything it declines to answer for) is logged rather than failed — an unknown is not
    a mismatch, and failing on it would block cuts that are fine.
    """
    timeline_fps = float(doc["timeline"]["fps"])
    used = {alias for alias, _ in _alias_uses(doc)}
    findings: list[Finding] = []
    for alias in sorted(used):
        clip = resolved.get(alias)
        if clip is None or clip.is_still:
            continue
        if clip.fps is None:
            log.info("No frame rate reported for %s; E6 not checked", clip.name)
            continue
        if abs(clip.fps - timeline_fps) > FPS_TOLERANCE:
            findings.append(
                _finding(
                    "E6",
                    alias,
                    f"The source {alias!r} ({clip.name}) is {clip.fps} fps but the timeline "
                    f"is {timeline_fps} fps.",
                )
            )
    return findings


def _audio_errors(doc: dict[str, Any], resolved: dict[str, ClipFacts]) -> list[Finding]:
    """E7: the master-audio substrate every segment is laid over."""
    audio = doc.get("audio")
    if not isinstance(audio, dict):
        return []
    alias = str(audio["source"])
    clip = resolved.get(alias)
    if clip is None:
        return []  # already reported as E4 or an undeclared-alias E7
    findings: list[Finding] = []
    if not clip.has_audio:
        findings.append(
            _finding("E7", None, f"The audio source {alias!r} ({clip.name}) has no audio.")
        )
    if _outside_media(audio, clip):
        findings.append(_finding("E7", None, _overrun_message(audio, clip, "The audio block")))
    return findings


__all__ = [
    "DEFAULT_MIN_SEGMENT_FRAMES",
    "FIRST_OVERLAY_TRACK",
    "MAX_OVERLAY_TRACK",
    "RULE_DESCRIPTIONS",
    "ClipFacts",
    "entry_duration",
    "gaps",
    "is_gap",
    "locked_track_finding",
    "overlay_positions",
    "overlay_track",
    "parse_failure_finding",
    "placements",
    "positions",
    "resolve_aliases",
    "shots",
    "total_frames",
    "validate_project",
    "validate_structure",
]
