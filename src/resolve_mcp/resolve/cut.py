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

from typing import Any

from ..cut.document import LoadedCut, read_cut_file
from ..cut.schema import ANNOTATED_EXAMPLE, SCHEMA_DOC, SCHEMA_VERSION
from ..cut.validate import (
    DEFAULT_MIN_SEGMENT_FRAMES,
    RULE_DESCRIPTIONS,
    ClipFacts,
    Finding,
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


def get_cut_schema() -> dict[str, Any]:
    """The cut-file contract: the schema document, its annotated example, and the rules."""
    return {
        "schema": SCHEMA_VERSION,
        "document": SCHEMA_DOC,
        "annotated_example": ANNOTATED_EXAMPLE,
        "rules": [
            {
                "rule": rule,
                "severity": "warning" if rule.startswith("W") else "error",
                "description": description,
            }
            for rule, description in RULE_DESCRIPTIONS.items()
        ],
    }


def validate_cut(
    connection: ResolveConnection,
    cut_file: str,
    min_segment_frames: int = DEFAULT_MIN_SEGMENT_FRAMES,
) -> dict[str, Any]:
    """Dry-run ``cut_file``: every rule, every failure, before anything is built."""
    loaded = read_cut_file(cut_file)
    if loaded.parse_error is not None:
        return _report(loaded, None, [loaded.parse_error])

    findings = validate_structure(loaded.doc, min_segment_frames=min_segment_frames)
    if any(finding.rule == "E1" for finding in findings):
        log.info("Cut file %s is not schema-valid; the media pool was not read", loaded.path)
        return _report(loaded, None, findings)

    findings = [*findings, *validate_project(loaded.doc, clip_facts(connection, loaded.doc))]
    return _report(loaded, loaded.doc, findings)


def clip_facts(connection: ResolveConnection, doc: dict[str, Any]) -> list[ClipFacts]:
    """Read the media pool for the clips this cut names, and nothing else.

    Only the aliased names are looked up: a concert pool holds thousands of clips, and a
    cut references a handful, so properties are read for the handful.
    """
    pool = media.media_pool(connection)
    wanted = {str(source["clip"]) for source in doc["sources"].values()}
    located = media.clips_named(pool, wanted)
    log.info("Cut validation resolved %d of %d aliased clip names", len(located), len(wanted))
    return [_facts(found.bin_path, found.clip) for found in located]


def _facts(bin_path: str, clip: Clip) -> ClipFacts:
    reported = media.properties(clip)
    start, out = media.frame_bounds(reported)
    channels = media.audio_channels(reported)
    return ClipFacts(
        name=str(clip.GetName() or ""),
        bin_path=bin_path,
        start=start if start is not None else 0,
        end_exclusive=out if out is not None else 0,
        fps=media.frame_rate(reported),
        # An unreported channel count must not fail a cut that is fine: E7 blocks the
        # build, and "Resolve did not say" is not evidence of silence.
        has_audio=channels is None or channels > 0,
        is_still=media.is_still(reported),
    )


def _report(
    loaded: LoadedCut,
    doc: dict[str, Any] | None,
    findings: list[Finding],
) -> dict[str, Any]:
    errors = [finding for finding in findings if finding.severity == "error"]
    warnings = [finding for finding in findings if finding.severity == "warning"]
    return {
        "cut_file": str(loaded.path),
        "content_hash": loaded.content_hash,
        "valid": not errors,
        "errors": [finding.as_dict() for finding in errors],
        "warnings": [finding.as_dict() for finding in warnings],
        "cut": _summary(doc),
    }


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


__all__ = ["clip_facts", "get_cut_schema", "validate_cut"]
