"""Reading a titles file against a project: the contract, and the dry run that holds it.

The rules that judge a titles file need three readings Resolve alone can give — the
timeline it names, the blue markers that anchor its songs, and the media-pool clips its
templates resolve to. Gathering those and running the rules over them happens here, once,
so :func:`validate_titles` and :mod:`resolve_mcp.resolve.apply` cannot drift apart: the
dry run and the apply are the same pass, and only one of them goes on to write.

Everything Resolve reports is gathered as *facts* rather than handles wherever a rule
touches it, so the rules stay pure and unit-testable — while the handles the apply will
need travel alongside them, which is what stops a file being validated against one clip
and applied with another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..document import LoadedDocument
from ..findings import Finding, severity_of
from ..logging_config import get_logger
from ..titles.assets import Asset
from ..titles.document import read_titles_file
from ..titles.schema import ANNOTATED_EXAMPLE, SCHEMA_DOC, SCHEMA_VERSION
from ..titles.validate import (
    ANCHOR_COLOR,
    PNG,
    RULE_DESCRIPTIONS,
    Event,
    TemplateFacts,
    plan,
    route_of,
    template_of,
    validate_assets,
    validate_project,
    validate_structure,
)
from . import markers, media
from . import timeline as timeline_read
from .connection import ResolveConnection
from .session import frame_rate

log = get_logger("titles")

Pool = Any
Project = Any
Timeline = Any


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

    loaded: LoadedDocument
    findings: list[Finding]
    project: Project | None = None
    timeline: Timeline | None = None
    fps: float | None = None
    templates: dict[str, media.LocatedClip] = field(default_factory=dict)
    assets: dict[str, Asset] = field(default_factory=dict)
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
    # The cards are judged off disk alone — no project, no pool — so a card that was never
    # exported is named in the same pass as a malformed one, and named before anything is
    # looked up in Resolve rather than after a lookup that would have to succeed first.
    asset_findings, assets = validate_assets(doc, base=loaded.path.parent)
    findings = [*findings, *asset_findings]

    project = timeline_read.open_project(connection)
    timeline = timeline_read.find_timeline(project, doc.get("timeline"))
    fps = frame_rate(project, timeline)
    pool = media.media_pool(connection)

    facts, located = _templates(pool, doc)
    anchors = markers.markers_by_name(connection, timeline, fps, ANCHOR_COLOR)
    log.info(
        "Titling %r: %d %s marker(s), %d template(s) declared, %d png card(s)",
        timeline_read.name_of(timeline),
        len(anchors),
        ANCHOR_COLOR.lower(),
        len(facts),
        len(assets),
    )
    findings = [
        *findings,
        *validate_project(doc, anchors=anchors, templates=facts, span=_span(timeline)),
    ]
    return Preflight(
        loaded,
        findings,
        project=project,
        timeline=timeline,
        fps=fps,
        templates=located,
        assets=assets,
        events=plan(doc, anchors),
    )


def _span(timeline: Timeline) -> tuple[int, int]:
    """The timeline's own ``[start, end)`` in record frames, as T9 judges against it."""
    start = timeline_read.read_frames(timeline.GetStartFrame())
    end = timeline_read.read_frames(timeline.GetEndFrame())
    if start is None or end is None:
        log.warning("Timeline reported an unreadable span; T9 cannot check placement bounds")
        return (start or 0, end or 0)
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
    used = {
        template_of(event)
        for song in doc["songs"]
        for event in song["events"]
        if route_of(event) != PNG
    }
    declared = {name: one for name, one in doc.get("templates", {}).items() if name in used}
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
    """Whether a clip's bin is the declared one or nested inside it, as find_clip searches.

    ``declared`` is compared as written, so the one spelling this does not share with
    :func:`media.find_clip` is the root's own name: ``Master`` reads as a bin called
    Master here, where find_clip reads it as the root.
    """
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


__all__ = ["Preflight", "get_titles_schema", "preflight", "validate_titles"]
