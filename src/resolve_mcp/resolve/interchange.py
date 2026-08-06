"""Timeline interchange: a cut out to a file, a file back in as a new cut.

This is the structural escape hatch. The scripting API cannot cut a transition, so the
route around that wall is: export the cut, hand-edit a dissolve into the document, import
it back. Everything here exists to make that round trip safe to take.

Four things are decisions rather than API calls:

* **A format name is not an export constant.** ``Timeline.Export`` takes a number that
  lives on the Resolve app object, and which numbers exist depends on the build — FCPXML
  has gained a constant per version of the format. So a format maps to an ordered list of
  constant names and the newest one this build has wins; a build with none of them fails
  naming what it looked for, not with an ``AttributeError`` from inside a wrapper. The lookup
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

import shutil
import tempfile
from contextlib import suppress
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
    open_project,
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
    project = open_project(connection)
    timeline = find_timeline(project, name)
    timeline_name = name_of(timeline)

    export_type, constant = _export_type(resolve, spec, timeline)
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


def _export_types(resolve: Any, spec: Format) -> list[tuple[Any, str]]:
    """Every constant for this format the attached Resolve defines, newest first."""
    defined = [
        (getattr(resolve, constant, None), constant)
        for constant in spec.export_types
        if getattr(resolve, constant, None) is not None
    ]
    if not defined:
        raise TimelineExportFailedError(
            cause=f"This Resolve build defines no export type for {spec.key}.",
            fix=(
                "Update Resolve, or export in another format — otio and drt are supported on "
                "every build this server attaches to."
            ),
            detail={"format": spec.key, "tried": list(spec.export_types)},
        )
    return defined


def _export_type(resolve: Any, spec: Format, timeline: Any) -> tuple[Any, str]:
    """The newest constant for this format that this build can actually write through.

    Defining a constant is not the same as exporting through it. Resolve 21.0.3 defines
    EXPORT_FCPXML_1_10, answers True for it, and writes a zero-byte file — and then keeps
    the handle, so that path can never be exported to or deleted again (#26, live). A
    ladder that picks on definedness alone therefore both fails and takes the caller's
    target down with it.

    So a format with more than one candidate is settled on a scratch file first, and only
    the winner is ever pointed at the target. That costs one extra export, on fcpxml alone
    — otio and drt have a single candidate each and go straight out. Nothing is cached
    between calls: a memo keyed on the build would be one more thing to be wrong about
    after a reconnect, and the probe is cheap next to the render it usually precedes.
    """
    candidates = _export_types(resolve, spec)
    if len(candidates) == 1:
        return candidates[0]

    winner, attempts = _probe(timeline, spec, candidates)
    if winner is None:
        raise TimelineExportFailedError(
            cause=f"No export type this Resolve defines for {spec.key} writes a file.",
            fix=(
                "Export in another format — otio and drt are written by every build this "
                "server attaches to — or update Resolve."
            ),
            detail={"format": spec.key, "attempts": attempts},
        )
    return winner


def _probe(
    timeline: Any, spec: Format, candidates: list[tuple[Any, str]]
) -> tuple[tuple[Any, str] | None, list[dict[str, Any]]]:
    """Export to a throwaway file per candidate, newest first, and report the first that lands.

    One file per candidate, never reused: a constant that fails leaves its path unusable.
    The directory is left to the OS to clean — the files Resolve holds open cannot be
    deleted from here, and they are a few KB of XML.
    """
    attempts: list[dict[str, Any]] = []
    scratch = Path(tempfile.mkdtemp(prefix="resolve-mcp-export-probe-"))
    for export_type, constant in candidates:
        target = scratch / f"{constant}{spec.suffix}"
        returned = bool(timeline.Export(str(target), export_type))
        written = _written_bytes(target)
        if returned and written:
            log.info("This build writes %s through %s", spec.key, constant)
            _discard(scratch)
            return (export_type, constant), attempts
        attempts.append({"export_type": constant, "returned": returned, "bytes": written})
        log.warning("This build defines %s but writes nothing through it", constant)
    _discard(scratch)
    return None, attempts


def _discard(scratch: Path) -> None:
    """Best effort: Resolve keeps a handle on what it exported, and that is not an error."""
    with suppress(OSError):
        shutil.rmtree(scratch, ignore_errors=True)


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
    import_source_clips: bool | None = None,
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

    project = open_project(connection)
    pool = pool_of(project)
    existing = set(timeline_names(project))
    native = source.suffix.lower() == NATIVE_SUFFIX
    requested, options = _import_options(
        source, native, name, import_source_clips, source_media_path, existing
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
            fix=_collision_fix(native, landed, options),
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
    native: bool,
    name: str | None,
    import_source_clips: bool | None,
    source_media_path: str | Path | None,
    existing: set[str],
) -> tuple[str | None, dict[str, Any]]:
    """The options to send, and the name asked for — ``None`` when the file chooses it.

    A ``.drt`` is sent no options because Resolve honours none of them. Every option is
    therefore ``None`` by default and refused when given: a request that was made and
    dropped is worse than one that was refused, because the reply then quietly disagrees
    with what was asked for and the caller has no reason to look.
    """
    if native:
        refused = {
            "name": name,
            "import_source_clips": import_source_clips,
            "source_media_path": source_media_path,
        }
        given = [key for key, value in refused.items() if value is not None]
        if given:
            raise InvalidRequestError(
                cause=(
                    f"A .drt carries its own timeline name and media links, so "
                    f"{', '.join(given)} cannot be honoured for one."
                ),
                fix=(
                    "Import the .drt as it is — the reply names the timeline Resolve made — "
                    "or use an .otio or .fcpxml export, where all three are honoured."
                ),
                detail={"path": str(source), "ignored": given},
            )
        return None, {}

    requested = name or source.stem
    options: dict[str, Any] = {
        "timelineName": next_free_name(requested, existing),
        "importSourceClips": True if import_source_clips is None else bool(import_source_clips),
    }
    if source_media_path is not None:
        options["sourceClipsPath"] = str(source_media_path)
    return requested, options


def _collision_fix(native: bool, landed: str, options: dict[str, Any]) -> str:
    """What to do about an import that came back as a timeline the project already had.

    The two routes leave the project in different states and must not be given the same
    advice. On the ``.drt`` route Resolve chose the name, so both outcomes are open — it
    may have replaced that cut or made a second one carrying the name. On the others a free
    name was handed over and came back changed, which is Resolve disagreeing with a request
    this server did make.
    """
    if native:
        return (
            f"A .drt names its own timeline, so Resolve chose {landed!r} — it may have "
            f"replaced that cut or made a second one under the same name. Snapshot the "
            f"project, check {landed!r} against the cut it held, and re-export as .otio to "
            "choose the name here."
        )
    asked = options.get("timelineName")
    return (
        f"Resolve was asked for {asked!r} and answered with {landed!r} instead. Snapshot "
        f"the project, check {landed!r} against the cut it held, and retry naming the "
        "import explicitly."
    )
