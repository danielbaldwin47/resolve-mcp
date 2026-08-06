"""The cut-file validation rules: 11 hard errors, 2 warnings, one implementation.

The list is identical in the ``validate_cut`` dry run and in ``build_timeline``'s
pre-flight, so it lives here once and both call it. A failing file must abort before
Resolve is touched — a half-built timeline is the outcome this file exists to prevent.

Two passes, because they need different things:

* :func:`validate_structure` reads the document alone — no project, no connection. It
  answers everything about shape, ids, ranges, takes and overlay anchoring (E1-E4 for
  undeclared aliases, E7 for an undeclared audio alias, E8-E10, W1-W2).
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

from ..logging_config import get_logger
from ..timing import duration_frames, ranges_overlap
from .schema import SCHEMA_VERSION

log = get_logger("cut")

DEFAULT_MIN_SEGMENT_FRAMES: Final = 12
"""W1's flash-frame guard. A warning, never a block — the creative call stays Claude's."""

FPS_TOLERANCE: Final = 0.01
"""Reported rates carry float noise; real rate differences (23.976 vs 24) are far wider."""

RULE_DESCRIPTIONS: Final[dict[str, str]] = {
    "E1": "JSON parses; schema-valid; schema version supported",
    "E2": "ids unique across segments and overlays (one namespace)",
    "E3": "in < out everywhere",
    "E4": "every alias resolves to exactly one media-pool clip",
    "E5": "in/out inside clip media bounds",
    "E6": "source fps matches timeline fps (stills exempt)",
    "E7": "audio block resolves, has audio, bounds valid",
    "E8": "alternate duration equals main duration",
    "E9": "overlay anchor exists; offset inside anchor; span inside the total V1",
    "E10": "overlays do not overlap each other",
    "E11": "build-time: target tracks unlocked; connection and creation failures",
    "W1": "segment shorter than min_segment_frames (flash-frame guard)",
    "W2": "V1 total does not match the master-audio span",
}

_FIX_HINTS: Final[dict[str, str]] = {
    "E1": "Call get_cut_schema and match the annotated example exactly.",
    "E2": "Give every segment and overlay its own id — they share one namespace.",
    "E3": "Ranges are half-open [in, out); make out strictly greater than in.",
    "E4": "Fix the alias in sources, or import the clip and re-run validate_cut.",
    "E5": "Call inspect_clip for the clip's media bounds and pull the range inside them.",
    "E6": "Conform the source first, or set timeline.fps to the rate the sources are at.",
    "E7": "Point audio.source at a clip that has audio and keep its range inside the media.",
    "E8": "Alternates must match the main take frame for frame — swap_take cannot ripple.",
    "E9": "Anchor the overlay to a segment that exists, at an offset inside it.",
    "E10": "Move one overlay or shorten it — only one overlay may cover a frame.",
    "E11": "Unlock the track in Resolve's timeline header and build again.",
    "W1": "Lengthen the segment, or keep it if the flash is deliberate.",
    "W2": "Expected when the cut opens cold or runs past the mix; otherwise check the ends.",
}


def severity_of(rule: str) -> str:
    """``error`` blocks the build; ``warning`` is reported and never blocks."""
    return "warning" if rule.startswith("W") else "error"


@dataclass(frozen=True)
class Finding:
    """One rule firing on one thing, in the shape the agent reads."""

    rule: str
    id: str | None
    message: str
    fix_hint: str

    @property
    def severity(self) -> str:
        """``error`` blocks the build; ``warning`` is reported and never blocks."""
        return severity_of(self.rule)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "rule": self.rule,
            "id": self.id,
            "message": self.message,
            "fix_hint": self.fix_hint,
        }


@dataclass(frozen=True)
class ClipFacts:
    """What the rules need to know about one media-pool clip.

    Gathered once from Resolve and then handed to pure functions, so the rules never
    hold a Resolve handle. Bounds are half-open — ``end_exclusive`` is Resolve's last
    frame plus one — matching the cut file's own convention.
    """

    name: str
    bin_path: str | None
    start: int
    end_exclusive: int
    fps: float | None
    has_audio: bool = False
    is_still: bool = False


def _finding(rule: str, id: str | None, message: str, fix_hint: str | None = None) -> Finding:
    return Finding(rule=rule, id=id, message=message, fix_hint=fix_hint or _FIX_HINTS[rule])


def _order(findings: list[Finding]) -> list[Finding]:
    """Errors before warnings, rule number ascending, document order within a rule."""

    def key(numbered: tuple[int, Finding]) -> tuple[int, int, int]:
        position, finding = numbered
        return (0 if finding.severity == "error" else 1, int(finding.rule[1:]), position)

    return [finding for _, finding in sorted(enumerate(findings), key=key)]


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
    for index, segment in enumerate(segments):
        where = f"segments[{index}]"
        if not isinstance(segment, dict):
            yield _finding("E1", None, f"'{where}' must be an object.")
            continue
        id = segment.get("id") if isinstance(segment.get("id"), str) else None
        if not id:
            yield _finding("E1", None, f"'{where}.id' must be a non-empty string.")
        if not isinstance(segment.get("source"), str):
            yield _finding("E1", id, f"'{where}.source' must be a source alias.")
        yield from _range_shape_errors(segment, where, id)
        if "audio" in segment and not isinstance(segment["audio"], bool):
            yield _finding("E1", id, f"'{where}.audio' must be true or false.")
        if "note" in segment and not isinstance(segment["note"], str):
            yield _finding("E1", id, f"'{where}.note' must be a string.")
        yield from _alternates_errors(segment, where, id)


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
        return _order(shape)

    findings: list[Finding] = []
    findings += _id_errors(doc)
    findings += _range_errors(doc)
    findings += _declared_alias_errors(doc)
    findings += _alternate_duration_errors(doc)
    findings += _overlay_errors(doc)
    findings += _segment_length_warnings(doc, min_segment_frames)
    findings += _audio_span_warnings(doc)
    return _order(findings)


def _segments(doc: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = doc["segments"]
    return segments


def _overlays(doc: dict[str, Any]) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = doc.get("overlays") or []
    return overlays


def _id_errors(doc: dict[str, Any]) -> list[Finding]:
    """E2: segments and overlays share one id namespace."""
    seen: set[str] = set()
    findings: list[Finding] = []
    for item in (*_segments(doc), *_overlays(doc)):
        id = str(item["id"])
        if id in seen:
            findings.append(_finding("E2", id, f"The id {id!r} is used more than once."))
        seen.add(id)
    return findings


def _range_errors(doc: dict[str, Any]) -> list[Finding]:
    """E3: half-open ranges need in < out, or they name no frames at all."""
    findings: list[Finding] = []
    for segment in _segments(doc):
        findings += _range_error(segment, str(segment["id"]), "Segment")
        for index, alternate in enumerate(segment.get("alternates") or []):
            findings += _range_error(
                alternate, str(segment["id"]), f"Alternate {index} of segment"
            )
    for overlay in _overlays(doc):
        findings += _range_error(overlay, str(overlay["id"]), "Overlay")
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
    for segment in _segments(doc):
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
    for segment in _segments(doc):
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
    """Each segment id to its computed ``(start, duration)`` — sequential V1, butt-joined.

    The overlay rules and the build both place against these numbers, and they have to be
    the same numbers: an offset validated against one layout and built against another
    would put the overlay somewhere nobody checked.
    """
    placed: dict[str, tuple[int, int]] = {}
    at = 0
    for segment in _segments(doc):
        duration = duration_frames(segment["in"], segment["out"])
        placed[str(segment["id"])] = (at, duration)
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
    """The V1 span: sequential segments, so the sum of their durations."""
    return sum(duration_frames(s["in"], s["out"]) for s in _segments(doc))


def _overlay_errors(doc: dict[str, Any]) -> list[Finding]:
    """E9 and E10, both judged on absolute positions resolved from the anchors."""
    placed = positions(doc)
    total = total_frames(doc)
    findings: list[Finding] = []
    spans: list[tuple[str, int, int]] = []

    for overlay in _overlays(doc):
        id = str(overlay["id"])
        anchor = str(overlay["over"]["segment"])
        offset = int(overlay["over"]["offset"])
        if anchor not in placed:
            findings.append(
                _finding(
                    "E9",
                    id,
                    f"Overlay {id!r} is anchored to segment {anchor!r}, which does not exist.",
                )
            )
            continue
        start, anchor_duration = placed[anchor]
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
        duration = duration_frames(overlay["in"], overlay["out"])
        at = start + offset
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
        spans.append((id, at, at + duration))

    findings += _overlap_errors(spans)
    return findings


def _overlap_errors(spans: list[tuple[str, int, int]]) -> list[Finding]:
    """E10: overlays share one V2 track, and Resolve will not overwrite on overlap.

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
                    f"({span[1]}-{span[2]}) cover the same frames.",
                )
            )
        if furthest is None or span[2] > furthest[2]:
            furthest = span
    return findings


def _segment_length_warnings(doc: dict[str, Any], minimum: int) -> list[Finding]:
    """W1: a flash frame is usually a typo, occasionally the point. Never a block."""
    findings: list[Finding] = []
    for segment in _segments(doc):
        duration = duration_frames(segment["in"], segment["out"])
        if duration < minimum:
            findings.append(
                _finding(
                    "W1",
                    str(segment["id"]),
                    f"Segment {segment['id']!r} runs {duration} frames, under the "
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
    return _order(findings)


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
    for segment in _segments(doc):
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
    """Whether a half-open range asks for frames the clip's media does not have."""
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
    "RULE_DESCRIPTIONS",
    "ClipFacts",
    "Finding",
    "locked_track_finding",
    "parse_failure_finding",
    "placements",
    "positions",
    "resolve_aliases",
    "severity_of",
    "total_frames",
    "validate_project",
    "validate_structure",
]
