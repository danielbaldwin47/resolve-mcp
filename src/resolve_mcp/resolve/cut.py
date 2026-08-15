"""Serving the cut-file contract and enforcing it.

Two jobs, one file, because they are two halves of the same promise: the agent is told
exactly what the format is, and is then held to it before Resolve is touched.

* :func:`get_cut_schema` needs no project and no connection — the contract is a constant.
* :func:`validate_cut` is the dry run. It reads the file, runs the structural rules, and
  only then reaches into the media pool for the rules that need real clips. Which is also
  the order that keeps a malformed file from costing a round trip to Resolve.

``build_timeline`` runs the same :func:`validate_cut` pre-flight, so a file that passes
here is a file that will not abort a build on validation.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ..cut.document import read_cut_file
from ..cut.schema import ANNOTATED_EXAMPLE, SCHEMA_DOC, SCHEMA_VERSION
from ..cut.validate import (
    DEFAULT_MIN_SEGMENT_FRAMES,
    RULE_DESCRIPTIONS,
    ClipFacts,
    gaps,
    resolve_aliases,
    shots,
    total_frames,
    validate_project,
    validate_structure,
)
from ..document import LoadedDocument
from ..findings import Finding, severity_of
from ..logging_config import get_logger
from ..timing import dual_time
from . import pool as mediapool
from .connection import ResolveConnection

log = get_logger("cut")

Clip = Any
Pool = Any

MIN_SEGMENT_FRAMES = DEFAULT_MIN_SEGMENT_FRAMES
"""Re-exported so the tool layer takes its default from the wrapper it calls."""


def get_cut_schema() -> dict[str, Any]:
    """The cut-file contract: the schema document, its annotated example, and the rules."""
    return {
        "schema": SCHEMA_VERSION,
        "document": SCHEMA_DOC,
        "annotated_example": ANNOTATED_EXAMPLE,
        "rules": [
            {
                "rule": rule,
                "severity": severity_of(rule),
                "description": description,
            }
            for rule, description in RULE_DESCRIPTIONS.items()
        ],
    }


class Source(NamedTuple):
    """One pool clip, as the rules see it and as the build has to place it.

    The two travel as a pair rather than as two lists: the rules are judged on the facts
    and the append is made with the handle, and a cut validated against one clip and built
    from another is the failure this pairing makes impossible.
    """

    facts: ClipFacts
    located: mediapool.LocatedClip


class Preflight(NamedTuple):
    """One pass of the rules, and the pool reading they were judged against."""

    loaded: LoadedDocument
    findings: list[Finding]
    sources: list[Source]

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]

    @property
    def facts(self) -> list[ClipFacts]:
        return [source.facts for source in self.sources]


def preflight(
    connection: ResolveConnection,
    cut_file: str,
    min_segment_frames: int = MIN_SEGMENT_FRAMES,
) -> Preflight:
    """Read ``cut_file`` and run every rule on it. The dry run and the build share this."""
    loaded = read_cut_file(cut_file)
    if loaded.parse_error is not None:
        return Preflight(loaded, [loaded.parse_error], [])

    findings = validate_structure(loaded.doc, min_segment_frames=min_segment_frames)
    if any(finding.rule == "E1" for finding in findings):
        log.info("Cut file %s is not schema-valid; the media pool was not read", loaded.path)
        return Preflight(loaded, findings, [])

    timeline_fps = float(loaded.doc["timeline"]["fps"])
    sources = [
        Source(_facts(found.bin_path, found.clip, timeline_fps), found)
        for found in _located(connection, loaded.doc)
    ]
    facts = [source.facts for source in sources]
    return Preflight(loaded, [*findings, *validate_project(loaded.doc, facts)], sources)


def validate_cut(
    connection: ResolveConnection,
    cut_file: str,
    min_segment_frames: int = MIN_SEGMENT_FRAMES,
) -> dict[str, Any]:
    """Dry-run ``cut_file``: every rule, every failure, before anything is built."""
    return _report(preflight(connection, cut_file, min_segment_frames))


def clips_by_alias(checked: Preflight) -> dict[str, mediapool.LocatedClip]:
    """Alias -> the pool clip the rules resolved it to, ready to append.

    The alias is resolved by the same E4 rule the dry run uses, and the clip that comes
    back is the very object those facts were read from — not a second lookup that could
    land on a different one.
    """
    resolved, _ = resolve_aliases(checked.loaded.doc, checked.facts)
    handles = {id(source.facts): source.located for source in checked.sources}
    return {alias: handles[id(facts)] for alias, facts in resolved.items()}


def _located(connection: ResolveConnection, doc: dict[str, Any]) -> list[mediapool.LocatedClip]:
    """The pool clips this cut names, and nothing else.

    Only the aliased names are looked up: a concert pool holds thousands of clips, and a
    cut references a handful, so properties are read for the handful.
    """
    pool = mediapool.media_pool(connection)
    wanted = {str(source["clip"]) for source in doc["sources"].values()}
    located = mediapool.clips_named(pool, wanted)
    log.info("Cut validation resolved %d of %d aliased clip names", len(located), len(wanted))
    return located


def _facts(bin_path: str, clip: Clip, timeline_fps: float) -> ClipFacts:
    name = str(clip.GetName() or "")
    reported = mediapool.properties(clip)
    # The timeline's rate is what the Duration fallback counts at: an audio-only clip
    # reports no Start/End/Frames and no rate of its own (#46), only a Duration timecode.
    start, out = mediapool.frame_bounds(reported, fps=timeline_fps)
    if start is None or out is None:
        # The condition W9 reports to the agent, said once here as well: the warning rides
        # in a result the agent may or may not read back, and a build that went wrong over a
        # range nothing checked is diagnosed from the log or not at all (#186).
        log.info("No usable media bounds on %s (%s-%s); E5 and E7 cannot check a range "
                 "against them", name, start, out)
    channels = mediapool.audio_channels(reported)
    if channels is None:
        # E7's has-audio leg reads an undocumented property key. If Resolve renames it
        # the rule silently passes everything, and no fake can catch that — so say so
        # here, where a live session's log is the only place it can be noticed.
        log.info("No usable %r on %s; E7 cannot check for audio", mediapool.AUDIO_CHANNELS, name)
    return ClipFacts(
        name=name,
        bin_path=bin_path,
        # Bounds nothing could derive stay None — "cannot verify", so the range legs of
        # E5/E7 skip the clip rather than fail every range against fictitious 0-0 media
        # (the same fail-open stance has_audio takes below).
        start=start,
        end_exclusive=out,
        fps=mediapool.frame_rate(reported),
        # An unreported channel count must not fail a cut that is fine: E7 blocks the
        # build, and "Resolve did not say" is not evidence of silence.
        has_audio=channels is None or channels > 0,
        is_still=mediapool.is_still(reported),
    )


def _report(checked: Preflight) -> dict[str, Any]:
    return {
        "cut_file": str(checked.loaded.path),
        "content_hash": checked.loaded.content_hash,
        "valid": not checked.errors,
        "errors": [finding.as_dict() for finding in checked.errors],
        "warnings": [finding.as_dict() for finding in checked.warnings],
        "cut": _summary(_readable_doc(checked)),
    }


def _readable_doc(checked: Preflight) -> dict[str, Any] | None:
    """The document, or ``None`` when E1 fired and nothing about it can be trusted."""
    if any(finding.rule == "E1" for finding in checked.findings):
        return None
    doc: dict[str, Any] = checked.loaded.doc
    return doc


def _summary(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the cut says it is — only ever built from a document that passed E1."""
    if doc is None:
        return None
    fps = float(doc["timeline"]["fps"])
    return {
        "timeline": doc["timeline"]["name"],
        "segments": len(shots(doc)),
        "gaps": len(gaps(doc)),
        "overlays": len(doc.get("overlays") or []),
        "duration": dual_time(total_frames(doc), fps),
    }


__all__ = [
    "MIN_SEGMENT_FRAMES",
    "Preflight",
    "Source",
    "clips_by_alias",
    "get_cut_schema",
    "preflight",
    "validate_cut",
]
