"""Timeline interchange: a cut out to a file, a file back in as a new cut.

This is the structural escape hatch. The scripting API cannot cut a transition, so the
route around that wall is: export the cut, hand-edit a dissolve into the document, import
it back. Everything here exists to make that round trip safe to take.

Four things are decisions rather than API calls:

* **A format name is not an export constant.** ``Timeline.Export`` takes a number that
  lives on the Resolve app object, and which numbers exist depends on the build — FCPXML
  alone has had eight versions. So a format maps to an ordered list of constant names and
  the newest one this build has wins; a build that has none of them fails naming what it
  looked for, rather than throwing ``AttributeError`` from inside a wrapper. The lookup
  tests against ``None`` and never against truthiness, because the first constant Resolve
  defines has the value 0.
* **The export is confirmed on disk, not by the return value.** ``Export`` answers a bool,
  and a path in a successful reply that has nothing behind it is worse than a failure: the
  agent's next move is to open that file.
* **An import is given a name no timeline answers to.** ``ImportTimelineFromFile`` takes a
  ``timelineName`` and nothing on record says what it does with one already in use — and
  the outcome that cannot be undone is it landing on the cut that is already there. So the
  free name is computed first, from the project's own ``<base> v<N>`` convention, and the
  timeline that comes back is checked against the names that existed before the call. The
  reply says what was asked for and what was made, so a rename is never silent.
* **A ``.drt`` is Resolve's own document and takes no import options at all.** Not the
  name, not the source-clip handling — the API marks every one of them invalid for DRT, so
  a ``.drt`` names its own timeline and this server cannot choose for it. Options are not
  sent where they would be ignored, a caller who asked for one is told plainly it cannot be
  honoured, and the check on the way out — the timeline that came back is not one that
  existed before — is what holds the no-overwrite guarantee on that route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

from ..config import Config, get_config
from ..errors import (
    InvalidRequestError,
    TimelineExportFailedError,
    TimelineImportFailedError,
)
from ..logging_config import get_logger
from ..naming import write_target
from .connection import ResolveConnection
from .media import pool_of
from .timeline import (
    Reader,
    current_name,
    find_timeline,
    name_of,
    next_free_name,
    project_of,
    summarise,
    timeline_names,
)

log = get_logger("interchange")


class Format(NamedTuple):
    """One interchange format: what it is called, what it writes, what asks for it."""

    key: str
    suffix: str
    export_types: tuple[str, ...]


#: FCPXML is listed newest version first — a newer document carries more of the cut, so the
#: newest the build offers is the one to write. Extend the head of that tuple when Resolve
#: adds a version; a name this build does not define costs nothing and is skipped.
FORMATS: dict[str, Format] = {
    "otio": Format("otio", ".otio", ("EXPORT_OTIO",)),
    "fcpxml": Format(
        "fcpxml",
        ".fcpxml",
        (
            "EXPORT_FCPXML_1_11",
            "EXPORT_FCPXML_1_10",
            "EXPORT_FCPXML_1_9",
            "EXPORT_FCPXML_1_8",
            "EXPORT_FCPXML_1_7",
            "EXPORT_FCPXML_1_6",
            "EXPORT_FCPXML_1_5",
            "EXPORT_FCPXML_1_4",
            "EXPORT_FCPXML_1_3",
        ),
    ),
    "drt": Format("drt", ".drt", ("EXPORT_DRT",)),
}

DEFAULT_FORMAT = "otio"
#: Resolve's own timeline document. Every import option is documented as invalid for it.
NATIVE_SUFFIX = ".drt"


# --- export ------------------------------------------------------------------------------


def export_timeline(
    connection: ResolveConnection,
    name: str | None = None,
    export_format: str = DEFAULT_FORMAT,
    path: str | Path | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Write one timeline (the open one by default) out as an interchange file."""
    spec = _format(export_format)
    resolve = connection.handle()
    project = project_of(connection)
    timeline = find_timeline(project, name)
    timeline_name = name_of(timeline)

    export_type, constant = _export_type(resolve, spec)
    target = write_target(
        path, timeline_name, spec.suffix, (config or get_config()).interchange_dir, "timeline"
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TimelineExportFailedError(cause=f"Could not create {target.parent}: {exc}") from exc

    # Two arguments, not three: the subtype is Resolve's own optional argument and none of
    # these three formats has one. Passing a subtype constant borrowed from AAF or EDL
    # would be a guess at what the export means.
    if not timeline.Export(str(target), export_type):
        raise TimelineExportFailedError(
            cause=f"Resolve refused to export {timeline_name!r} as {spec.key} to {target}.",
            detail={"timeline": timeline_name, "format": spec.key, "export_type": constant},
        )

    written = _written_bytes(target)
    if not written:
        raise TimelineExportFailedError(
            cause=(
                f"Resolve reported the export of {timeline_name!r} succeeded but wrote "
                f"nothing to {target}."
            ),
            fix=(
                "Check the path is writable from the machine Resolve runs on, and that no "
                "dialog in the Resolve GUI is holding the export. Then retry."
            ),
            detail={"timeline": timeline_name, "format": spec.key, "path": str(target)},
        )

    log.info("Exported %r as %s to %s (%d bytes)", timeline_name, constant, target, written)
    return {
        "timeline": timeline_name,
        "format": spec.key,
        "export_type": constant,
        "path": str(target),
        "bytes": written,
    }


def _format(export_format: str) -> Format:
    spec = FORMATS.get(str(export_format).strip().lower())
    if spec is None:
        raise InvalidRequestError(
            cause=f"{export_format!r} is not an interchange format this server writes.",
            fix=(
                "Use otio for a round trip (the route for hand-injected transitions), "
                "fcpxml to hand a cut to another NLE, or drt for a Resolve-native timeline."
            ),
            detail={"requested": export_format, "available": list(FORMATS)},
        )
    return spec


def _export_type(resolve: Any, spec: Format) -> tuple[Any, str]:
    """The newest constant for this format that the attached Resolve actually defines."""
    for constant in spec.export_types:
        value = getattr(resolve, constant, None)
        if value is not None:
            return value, constant
    raise TimelineExportFailedError(
        cause=f"This Resolve build defines no export type for {spec.key}.",
        fix=(
            "Update Resolve, or export in another format — otio and drt are supported on "
            "every build this server attaches to."
        ),
        detail={"format": spec.key, "tried": list(spec.export_types)},
    )


def _written_bytes(target: Path) -> int:
    try:
        return target.stat().st_size
    except OSError:
        return 0


# --- import ------------------------------------------------------------------------------


def import_timeline(
    connection: ResolveConnection,
    path: str | Path,
    name: str | None = None,
    import_source_clips: bool = True,
    source_media_path: str | Path | None = None,
) -> dict[str, Any]:
    """Materialise a new timeline from an interchange file. Never lands on an existing one."""
    source = Path(path)
    if not source.is_file():
        raise InvalidRequestError(
            cause=f"There is no file at {source}.",
            fix=(
                "Give the path to an .otio, .fcpxml or .drt file as it is seen from the "
                "machine Resolve runs on. export_timeline reports the path it wrote."
            ),
            detail={"requested": str(source)},
        )

    project = project_of(connection)
    pool = pool_of(project)
    existing = set(timeline_names(project))
    requested, options = _import_options(
        source, name, import_source_clips, source_media_path, existing
    )

    imported = pool.ImportTimelineFromFile(str(source), options)
    if imported is None:
        raise TimelineImportFailedError(
            cause=f"Resolve materialised no timeline from {source}.",
            detail={"path": str(source), "requested_name": requested, "options": options},
        )

    landed = name_of(imported)
    if landed in existing:
        raise TimelineImportFailedError(
            cause=(
                f"Resolve handed back {landed!r}, a timeline the project already had, "
                f"rather than a new one."
            ),
            fix=(
                f"Snapshot the project and check whether {landed!r} still holds the cut it "
                "did before. A .drt names its own timeline — Resolve chose that name, not "
                "this server — so re-export the cut as .otio to choose it here."
            ),
            detail={"path": str(source), "timeline": landed, "options": options},
        )

    reader = Reader(connection)
    heading = summarise(reader, imported, project, current_name(project))
    log.info("Imported %s as %r (asked for %r)", source, landed, requested)
    return {
        "path": str(source),
        "requested_name": requested,
        "renamed": requested is not None and landed != requested,
        "timeline": heading,
    }


def _import_options(
    source: Path,
    name: str | None,
    import_source_clips: bool,
    source_media_path: str | Path | None,
    existing: set[str],
) -> tuple[str | None, dict[str, Any]]:
    """The options to send, and the name asked for — ``None`` when the file chooses it.

    A ``.drt`` is sent no options because Resolve honours none of them; a caller who
    explicitly asked for one is told rather than left to discover it from a reply that
    quietly disagrees with the request. ``import_source_clips`` is not that kind of ask —
    it has a default, so a ``.drt`` import silently leaves it out rather than refusing a
    value the caller may never have chosen.
    """
    if source.suffix.lower() == NATIVE_SUFFIX:
        refused = {"name": name, "source_media_path": source_media_path}
        given = [key for key, value in refused.items() if value is not None]
        if given:
            raise InvalidRequestError(
                cause=(
                    f"A .drt carries its own timeline name and media links, so "
                    f"{' and '.join(given)} cannot be honoured for one."
                ),
                fix=(
                    "Import the .drt as it is — the reply names the timeline Resolve made — "
                    "or use an .otio or .fcpxml export, where both are honoured."
                ),
                detail={"path": str(source), "ignored": given},
            )
        return None, {}

    requested = name or source.stem
    options: dict[str, Any] = {
        "timelineName": next_free_name(requested, existing),
        "importSourceClips": bool(import_source_clips),
    }
    if source_media_path is not None:
        options["sourceClipsPath"] = str(source_media_path)
    return requested, options
