"""Timeline interchange: a cut out to a file, a file back in as a new cut.

This is the structural escape hatch. The scripting API cannot cut a transition, so the
route around that wall is: export the cut, hand-edit a dissolve into the document, import
it back. Everything here exists to make that round trip safe to take.

Three things are decisions rather than API calls:

* **A format name is not an export constant.** ``Timeline.Export`` takes a number that
  lives on the Resolve app object, and which numbers exist depends on the build — FCPXML
  alone has eight versions. So a format maps to an ordered list of constant names and the
  newest one this build has wins; a build that has none of them fails naming what it looked
  for, rather than throwing ``AttributeError`` from inside a wrapper. The lookup tests
  against ``None`` and never against truthiness, because the first constant Resolve defines
  has the value 0.
* **The export is confirmed on disk, not by the return value.** ``Export`` answers a bool,
  and a path in a successful reply that has nothing behind it is worse than a failure: the
  agent's next move is to open that file.
* **An import is given a name no timeline answers to.** ``ImportTimelineFromFile`` takes a
  ``timelineName`` and nothing on record says what it does with one already in use — and
  the outcome that cannot be undone is it landing on the cut that is already there. So the
  free name is computed first, from the project's own ``<base> v<N>`` convention, and the
  timeline that comes back is checked against the names that existed before the call. The
  reply says what was asked for and what was made, so a rename is never silent.
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
from ..naming import timestamped_name
from .connection import ResolveConnection
from .media import media_pool
from .timeline import (
    Reader,
    current_name,
    find_timeline,
    name_of,
    project_of,
    summarise,
    timeline_names,
    version_of,
)

log = get_logger("interchange")

Timeline = Any


class Format(NamedTuple):
    """One interchange format: the file it writes, and the constants that ask for it."""

    suffix: str
    export_types: tuple[str, ...]


#: OTIO first: it is the one the transition-injection route runs through. FCPXML is listed
#: newest version first — a newer document carries more of the cut, so the newest the build
#: offers is the one to write.
FORMATS: dict[str, Format] = {
    "otio": Format(".otio", ("EXPORT_OTIO",)),
    "fcpxml": Format(
        ".fcpxml",
        (
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
    "drt": Format(".drt", ("EXPORT_DRT",)),
}

DEFAULT_FORMAT = "otio"


# --- export ------------------------------------------------------------------------------


def export_timeline(
    connection: ResolveConnection,
    name: str | None = None,
    export_format: str = DEFAULT_FORMAT,
    path: str | Path | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """Write one timeline (the open one by default) out as an interchange file."""
    spec, key = _format(export_format)
    resolve = connection.handle()
    project = project_of(connection)
    timeline = find_timeline(project, name)
    timeline_name = name_of(timeline)

    export_type, constant = _export_type(resolve, spec, key)
    target = _target(path, timeline_name, spec, config or get_config())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise TimelineExportFailedError(cause=f"Could not create {target.parent}: {exc}") from exc

    # Two arguments, not three: the subtype is Resolve's own optional argument and none of
    # these three formats has one. Passing a subtype constant borrowed from AAF or EDL
    # would be a guess at what the export means.
    if not timeline.Export(str(target), export_type):
        raise TimelineExportFailedError(
            cause=f"Resolve refused to export {timeline_name!r} as {key} to {target}.",
            detail={"timeline": timeline_name, "format": key, "export_type": constant},
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
            detail={"timeline": timeline_name, "format": key, "path": str(target)},
        )

    log.info("Exported %r as %s to %s (%d bytes)", timeline_name, constant, target, written)
    return {
        "timeline": timeline_name,
        "format": key,
        "export_type": constant,
        "path": str(target),
        "bytes": written,
    }


def _format(export_format: str) -> tuple[Format, str]:
    key = str(export_format).strip().lower()
    spec = FORMATS.get(key)
    if spec is None:
        raise InvalidRequestError(
            cause=f"{export_format!r} is not an interchange format this server writes.",
            fix=(
                "Use otio for a round trip (the route for hand-injected transitions), "
                "fcpxml to hand a cut to another NLE, or drt for a Resolve-native timeline."
            ),
            detail={"requested": export_format, "available": list(FORMATS)},
        )
    return spec, key


def _export_type(resolve: Any, spec: Format, key: str) -> tuple[Any, str]:
    """The newest constant for this format that the attached Resolve actually defines."""
    for constant in spec.export_types:
        value = getattr(resolve, constant, None)
        if value is not None:
            return value, constant
    raise TimelineExportFailedError(
        cause=f"This Resolve build defines no export type for {key}.",
        fix=(
            "Update Resolve, or export in another format — otio and drt are supported on "
            "every build this server attaches to."
        ),
        detail={"format": key, "tried": list(spec.export_types)},
    )


def _target(path: str | Path | None, timeline_name: str, spec: Format, config: Config) -> Path:
    """Where the file goes: the cache's interchange folder, unless a path was given.

    An explicit path keeps its folder and stem but not a suffix that disagrees with the
    format — a ``.xml`` holding OTIO would be opened by the wrong thing later.
    """
    if path is None:
        return config.interchange_dir / timestamped_name(timeline_name, spec.suffix, "timeline")
    target = Path(path)
    if target.suffix.lower() != spec.suffix:
        target = target.with_suffix(spec.suffix)
    return target


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
    pool = media_pool(connection)
    existing = set(timeline_names(project))
    requested = name or source.stem
    free = _free_name(requested, existing)

    options: dict[str, Any] = {
        "timelineName": free,
        "importSourceClips": bool(import_source_clips),
    }
    if source_media_path is not None:
        options["sourceClipsPath"] = str(source_media_path)

    imported = pool.ImportTimelineFromFile(str(source), options)
    if imported is None:
        raise TimelineImportFailedError(
            cause=f"Resolve materialised no timeline from {source}.",
            detail={"path": str(source), "requested_name": requested, "asked_for": free},
        )

    landed = name_of(imported)
    if landed in existing:
        raise TimelineImportFailedError(
            cause=(
                f"Resolve handed back {landed!r}, a timeline the project already had, "
                f"rather than a new one."
            ),
            fix=(
                "Nothing was imported under a new name. Snapshot the project, then check "
                f"whether {landed!r} still holds the cut it did before importing again."
            ),
            detail={"path": str(source), "timeline": landed, "asked_for": free},
        )

    reader = Reader(connection)
    heading = summarise(reader, imported, project, current_name(project))
    log.info("Imported %s as %r (asked for %r)", source, landed, requested)
    return {
        "path": str(source),
        "requested_name": requested,
        "renamed": landed != requested,
        "timeline": heading,
    }


def _free_name(requested: str, existing: set[str]) -> str:
    """A name no timeline in the project answers to, following ``<base> v<N>``.

    The project's own convention for a new cut from an old one is the next version number,
    so a collision walks that sequence rather than inventing a suffix of its own — an
    unversioned name starts the sequence at v2, which reads as what it is: the second thing
    to carry that name.
    """
    if requested not in existing:
        return requested
    base, version = version_of(requested)
    number = (version or 1) + 1
    while f"{base} v{number}" in existing:
        number += 1
    return f"{base} v{number}"
