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

from ..cut.document import LoadedCut, read_cut_file
from ..cut.schema import ANNOTATED_EXAMPLE, SCHEMA_DOC, SCHEMA_VERSION
from ..cut.validate import (
    DEFAULT_MIN_SEGMENT_FRAMES,
    RULE_DESCRIPTIONS,
    ClipFacts,
    Finding,
    resolve_aliases,
    severity_of,
    total_frames,
    validate_project,
    validate_structure,
)
from ..logging_config import get_logger
from ..timing import dual_time
from . import media
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


class Preflight(NamedTuple):
    """One pass of the rules, and the pool reading they were judged against.

    The build needs both halves: the findings to decide whether to start at all, and the
    clips themselves to place. Handing them back together is what keeps the build from
    reading the pool a second time and resolving an alias to a different clip than the one
    the rules passed.
    """

    loaded: LoadedCut
    findings: list[Finding]
    clips: list[media.LocatedClip]
    facts: list[ClipFacts]

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]


def preflight(
    connection: ResolveConnection,
    cut_file: str,
    min_segment_frames: int = MIN_SEGMENT_FRAMES,
) -> Preflight:
    """Read ``cut_file`` and run every rule on it. The dry run and the build share this."""
    loaded = read_cut_file(cut_file)
    if loaded.parse_error is not None:
        return Preflight(loaded, [loaded.parse_error], [], [])

    findings = validate_structure(loaded.doc, min_segment_frames=min_segment_frames)
    if any(finding.rule == "E1" for finding in findings):
        log.info("Cut file %s is not schema-valid; the media pool was not read", loaded.path)
        return Preflight(loaded, findings, [], [])

    located = _located(connection, loaded.doc)
    facts = [_facts(found.bin_path, found.clip) for found in located]
    return Preflight(loaded, [*findings, *validate_project(loaded.doc, facts)], located, facts)


def validate_cut(
    connection: ResolveConnection,
    cut_file: str,
    min_segment_frames: int = MIN_SEGMENT_FRAMES,
) -> dict[str, Any]:
    """Dry-run ``cut_file``: every rule, every failure, before anything is built."""
    return _report(preflight(connection, cut_file, min_segment_frames))


def clip_facts(connection: ResolveConnection, doc: dict[str, Any]) -> list[ClipFacts]:
    """Read the media pool for the clips this cut names, and nothing else."""
    return [_facts(found.bin_path, found.clip) for found in _located(connection, doc)]


def clips_by_alias(preflighted: Preflight) -> dict[str, media.LocatedClip]:
    """Alias -> the pool clip the rules resolved it to, ready to append.

    The pairing runs through the facts rather than a second lookup, so the clip that is
    placed is the clip E4-E7 passed. Two clips that are alike in every fact are the same
    clip as far as every rule is concerned, so collapsing them here changes nothing.
    """
    doc = preflighted.loaded.doc
    resolved, _ = resolve_aliases(doc, preflighted.facts)
    handles = dict(zip(preflighted.facts, preflighted.clips, strict=True))
    return {alias: handles[fact] for alias, fact in resolved.items()}


def _located(connection: ResolveConnection, doc: dict[str, Any]) -> list[media.LocatedClip]:
    """The pool clips this cut names, and nothing else.

    Only the aliased names are looked up: a concert pool holds thousands of clips, and a
    cut references a handful, so properties are read for the handful.
    """
    pool = media.media_pool(connection)
    wanted = {str(source["clip"]) for source in doc["sources"].values()}
    located = media.clips_named(pool, wanted)
    log.info("Cut validation resolved %d of %d aliased clip names", len(located), len(wanted))
    return located


def _facts(bin_path: str, clip: Clip) -> ClipFacts:
    name = str(clip.GetName() or "")
    reported = media.properties(clip)
    start, out = media.frame_bounds(reported)
    channels = media.audio_channels(reported)
    if channels is None:
        # E7's has-audio leg reads an undocumented property key. If Resolve renames it
        # the rule silently passes everything, and no fake can catch that — so say so
        # here, where a live session's log is the only place it can be noticed.
        log.info("No usable %r on %s; E7 cannot check for audio", media.AUDIO_CHANNELS, name)
    return ClipFacts(
        name=name,
        bin_path=bin_path,
        start=start if start is not None else 0,
        end_exclusive=out if out is not None else 0,
        fps=media.frame_rate(reported),
        # An unreported channel count must not fail a cut that is fine: E7 blocks the
        # build, and "Resolve did not say" is not evidence of silence.
        has_audio=channels is None or channels > 0,
        is_still=media.is_still(reported),
    )


def _report(checked: Preflight) -> dict[str, Any]:
    return {
        "cut_file": str(checked.loaded.path),
        "content_hash": checked.loaded.content_hash,
        "valid": not checked.errors,
        "errors": [finding.as_dict() for finding in checked.errors],
        "warnings": [finding.as_dict() for finding in checked.warnings],
        "cut": _summary(readable_doc(checked)),
    }


def readable_doc(checked: Preflight) -> dict[str, Any] | None:
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
        "segments": len(doc["segments"]),
        "overlays": len(doc.get("overlays") or []),
        "duration": dual_time(total_frames(doc), fps),
    }


__all__ = [
    "MIN_SEGMENT_FRAMES",
    "Preflight",
    "clip_facts",
    "clips_by_alias",
    "get_cut_schema",
    "preflight",
    "readable_doc",
    "validate_cut",
]
