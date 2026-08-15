"""Timeline interchange: a cut out to a file, a file back in as a new cut.

This is the structural escape hatch. The scripting API cannot cut a transition, so the
route around that wall is: export the cut, hand-edit a dissolve into the document, import
it back. Everything here exists to make that round trip safe to take.

Five things are decisions rather than API calls:

* **A format name is not an export constant.** ``Timeline.Export`` takes a number that
  lives on the Resolve app object, and which numbers exist depends on the build — FCPXML
  has gained a constant per version of the format. So a format maps to an ordered list of
  constant names, newest first; a build with none of them fails naming what it looked for,
  not with an ``AttributeError`` from inside a wrapper. The lookup tests against ``None``
  and never against truthiness, because the first constant Resolve defines has the value 0.
* **Defined is not the same as writable, so the newest one that *works* wins.** Resolve
  21.0.3 defines ``EXPORT_FCPXML_1_10``, answers True for it, writes a zero-byte file, and
  then holds that file open — so the path it touched can never be exported to or deleted
  again (#26, live). Picking on definedness alone therefore fails *and* destroys the
  caller's target. A format whose ladder holds more than one constant is settled on a
  scratch file first and only the winner is aimed at the target; the answer — including
  "nothing here writes" — is remembered for the life of the attach, because settling walks
  past broken constants and each one it walks past strands a file.
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
from .pool import pool_of
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

    export_type, constant = _export_type(resolve, spec, timeline, connection.build_notes)
    target = write_target(
        path, timeline_name, spec.suffix, (config or get_config()).interchange_dir, "timeline"
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TimelineExportFailedError(cause=f"Could not create {target.parent}: {exc}") from exc

    returned, written = _export_once(timeline, target, export_type)
    if not returned:
        raise TimelineExportFailedError(
            cause=f"Resolve refused to export {timeline_name!r} as {spec.key} to {target}.",
            detail={"timeline": timeline_name, "format": spec.key, "export_type": constant},
        )

    if not written:
        raise TimelineExportFailedError(
            cause=(
                f"Resolve reported the export of {timeline_name!r} succeeded but wrote "
                f"nothing to {target}."
            ),
            fix=(
                "Retry to a different path, not this one: Resolve holds a file it wrote "
                "nothing to open for the life of the process, so this path will keep "
                "failing until Resolve restarts. Check too that no dialog in the Resolve "
                "GUI is holding the export."
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


def _defined_types(resolve: Any, spec: Format) -> list[tuple[Any, str]]:
    """Every constant for this format the attached Resolve defines, newest first."""
    values = ((getattr(resolve, constant, None), constant) for constant in spec.export_types)
    defined = [(value, constant) for value, constant in values if value is not None]
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


class _Probed(NamedTuple):
    """What settling found for one format on one attach.

    ``constant`` is the name of the export type that wrote, or ``None`` when every
    candidate was walked past — a failure worth remembering, because finding it out again
    costs another stranded file per broken constant.
    """

    constant: str | None
    attempts: list[dict[str, Any]]


def _export_type(
    resolve: Any, spec: Format, timeline: Any, notes: dict[str, Any]
) -> tuple[Any, str]:
    """The newest constant for this format that this build can actually write through.

    Defining a constant is not the same as exporting through it. Resolve 21.0.3 defines
    EXPORT_FCPXML_1_10, answers True for it, and writes a zero-byte file — and then keeps
    the handle, so that path can never be exported to or deleted again (#26, live). A
    ladder that picks on definedness alone therefore both fails and takes the caller's
    target down with it.

    So a format whose ladder holds more than one constant is settled on a scratch file
    first, and only the winner is ever pointed at the target. Three consequences worth
    stating:

    * **The test is on the ladder, not on what this build happens to define.** A build
      that defines exactly one FCPXML constant still gets settled, because one candidate
      is no evidence that candidate writes — and fcpxml is the format the destructive
      failure was found on. Only otio and drt skip it: their ladders hold a single
      constant, so there is no choice to make, and a build that cannot write them fails on
      the post-export byte check with the caller's target spent. That is the residual
      exposure, and it is accepted only because no build has ever failed those two.
    * **The answer is remembered for the attach, not the call** — and that includes the
      answer "nothing here writes". Settling walks past broken constants, and each one it
      walks past strands a file Resolve will not release, so a build where every candidate
      is broken would leak a directory per call if only successes were remembered.
      ``notes`` is emptied whenever a handle is attached, so a reconnect settles afresh
      rather than trusting the last build's answer.
    * **A remembered constant is not re-validated.** Notes are cleared on attach, so a
      constant that has stopped existing means the notes outlived their handle; that is a
      bug in the caching, not a build quirk, and it says so rather than quietly settling
      again.
    """
    candidates = _defined_types(resolve, spec)
    if len(spec.export_types) == 1:
        return candidates[0]

    note = f"export_type:{spec.key}"
    settled = notes.get(note)
    if not isinstance(settled, _Probed):
        settled = _first_writable_type(timeline, spec, candidates)
        notes[note] = settled

    if settled.constant is None:
        raise TimelineExportFailedError(
            cause=f"No export type this Resolve defines for {spec.key} writes a file.",
            fix=(
                "Export in another format — otio and drt are written by every build this "
                "server attaches to — or update Resolve."
            ),
            detail={"format": spec.key, "attempts": settled.attempts},
        )

    value = getattr(resolve, settled.constant, None)
    if value is None:
        raise TimelineExportFailedError(
            cause=(
                f"This Resolve no longer defines {settled.constant}, which it wrote "
                f"{spec.key} through earlier on this connection."
            ),
            fix="Reconnect: what a build was found to write through is dropped on attach.",
            detail={"format": spec.key, "export_type": settled.constant},
        )
    return value, settled.constant


def _first_writable_type(
    timeline: Any, spec: Format, candidates: list[tuple[Any, str]]
) -> _Probed:
    """Export to a throwaway file per candidate, newest first, and report the first that lands.

    One file per candidate, never reused: a constant that fails leaves its path unusable,
    so aiming the next candidate at the same name would test the poisoning rather than the
    constant. Removing the directory afterwards is best effort and *expected to fail
    whenever a candidate was walked past* — Resolve holds the zero-byte file it wrote open
    for the life of the process, so that scratch directory outlives the server. What is
    stranded is one empty file per broken constant; the caller remembers the answer, so it
    happens once per attach rather than once per export.

    The directory is logged before anything is written to it. It is the only record of
    where those files went, and on the machine this runs on nobody is watching the screen.
    """
    attempts: list[dict[str, Any]] = []
    try:
        scratch = Path(tempfile.mkdtemp(prefix="resolve-mcp-export-probe-"))
    except OSError as exc:
        raise TimelineExportFailedError(
            cause=f"Could not make a scratch directory to settle {spec.key} on: {exc}",
            fix=(
                "Free space in the system temp directory, or point TEMP at somewhere "
                "writable from the account Resolve runs under."
            ),
            detail={"format": spec.key},
        ) from exc

    log.info("Settling which export type writes %s, on %s", spec.key, scratch)
    try:
        for export_type, constant in candidates:
            scratch_target = scratch / f"{constant}{spec.suffix}"
            returned, written = _export_once(timeline, scratch_target, export_type)
            if returned and written:
                log.info("This build writes %s through %s", spec.key, constant)
                return _Probed(constant, attempts)
            attempts.append({"export_type": constant, "returned": returned, "bytes": written})
            if returned:
                log.warning(
                    "This build defines %s and answers True for it but wrote nothing; "
                    "%s is now held open and cannot be reused",
                    constant,
                    scratch_target,
                )
            else:
                log.warning("This build defines %s but refused to export through it", constant)
    finally:
        _discard(scratch)
    return _Probed(None, attempts)


def _export_once(timeline: Any, target: Path, export_type: Any) -> tuple[bool, int]:
    """One ``Export`` call: what Resolve said, and what actually landed on disk.

    The two are separate answers because Resolve gives a True that means nothing — the
    whole reason this module never trusts the return value alone.
    """
    # Two arguments, not three: the subtype is Resolve's own optional argument and none of
    # these three formats has one. Passing a subtype constant borrowed from AAF or EDL
    # would be a guess at what the export means.
    returned = bool(timeline.Export(str(target), export_type))
    return returned, _written_bytes(target)


def _discard(scratch: Path) -> None:
    """Best effort: Resolve keeps a handle on what it exported, and that is not an error."""
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
