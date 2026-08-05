"""Media pool wrappers: import, list, inspect, metadata, bins, relink.

Thin, testable, and MCP-free like the rest of this layer. Four things here are decisions
rather than API calls, and are the reason this file exists at all:

* **Bin paths are slash-separated from the media pool root** (``"Concert/Angles"``), the
  root's own name optional as a first segment. Resolve's API has no bin path — only
  ``GetSubFolderList()`` walks — and "current folder" state that every import depends on.
* **Offline means the file is gone**, not that Resolve says so: there is no offline flag in
  the scripting API. A clip is offline when it has a ``File Path`` that is not on disk. A
  clip with no path at all (multicam, compound) is not offline, it is pathless.
* **Metadata fields route by what the clip itself reports.** The clip property key names are
  undocumented, so nothing is hard-coded: each clip is enumerated once with
  ``GetClipProperty()`` and a field whose key is in that dict is written as a clip property,
  everything else as metadata. The route taken comes back in the result; on real media that
  dict is large enough that the property branch takes nearly everything — see ``_set_field``.
* **Image media gets the still-duration workaround at import.** A one-time
  ``SetClipProperty("Out", …)`` is what makes ``endFrame`` respected on stills later; doing
  it at import means no timeline code ever has to remember.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NamedTuple

from ..config import Config, get_config
from ..errors import (
    AmbiguousClipError,
    BinNotFoundError,
    ClipNotFoundError,
    ImportFailedError,
    InvalidRequestError,
    MediaOperationError,
    MediaPoolUnavailableError,
    NoProjectOpenError,
    RelinkFailedError,
)
from ..logging_config import get_logger
from ..spill import spill
from ..timing import dual_time
from .connection import ResolveConnection

log = get_logger("media")

BIN_SEPARATOR = "/"
DEFAULT_LIST_LIMIT = 200
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx", ".tga"})
SEQUENCE_TOKEN = "%"

FILE_PATH = "File Path"
FRAMES = "Frames"
FPS = "FPS"
START = "Start"
END = "End"
OUT = "Out"
TYPE = "Type"
RESOLUTION = "Resolution"

Clip = Any
Folder = Any
Pool = Any


class LocatedClip(NamedTuple):
    """A clip and the bin it was found in — the pair every lookup hands back."""

    bin_path: str
    clip: Clip


class LocatedBin(NamedTuple):
    """A bin, the path walked to reach it, and whether that walk had to create it.

    The path travels with the folder because it cannot be recovered from one: Resolve
    hands out a fresh proxy object per call, so a folder cannot be recognised by identity
    on a later walk, and the API has no path getter.
    """

    path: str
    folder: Folder
    created: bool


# --- reaching the pool ------------------------------------------------------------------


def media_pool(connection: ResolveConnection) -> Pool:
    """The open project's media pool. Raises rather than returning a useless ``None``."""
    manager = connection.handle().GetProjectManager()
    project = manager.GetCurrentProject() if manager is not None else None
    if project is None:
        raise NoProjectOpenError(cause="No project is open, so there is no media pool to work in.")
    pool = project.GetMediaPool()
    if pool is None:
        raise MediaPoolUnavailableError(
            cause=f"Resolve returned no media pool for the open project {project.GetName()!r}.",
        )
    return pool


# --- bins -------------------------------------------------------------------------------


def _segments(path: str | None, root: Folder) -> list[str]:
    """Split a bin path into folder names, dropping a leading root name if it is one.

    Resolve calls the root "Master" and the agent may write it or not. A real bin called
    Master would otherwise become unaddressable, so the leading name is only treated as the
    root when the root has no child by that name.
    """
    parts = [part.strip() for part in (path or "").split(BIN_SEPARATOR)]
    parts = [part for part in parts if part]
    root_name = str(root.GetName() or "")
    if parts and root_name and parts[0] == root_name:
        children = {str(sub.GetName() or "") for sub in _subfolders(root)}
        if root_name not in children:
            parts = parts[1:]
    return parts


def _root(pool: Pool) -> Folder:
    root = pool.GetRootFolder()
    if root is None:
        raise MediaPoolUnavailableError(cause="Resolve returned no media pool root folder.")
    return root


def _subfolders(folder: Folder) -> list[Folder]:
    return list(folder.GetSubFolderList() or [])


def bin_paths(pool: Pool) -> list[str]:
    """Every bin path in the pool, root excluded — what to offer when one is missing."""
    found: list[str] = []
    for path, _folder in _walk_bins(_root(pool)):
        if path:
            found.append(path)
    return found


def _walk_bins(folder: Folder, prefix: str = "") -> Iterator[tuple[str, Folder]]:
    yield prefix, folder
    for sub in _subfolders(folder):
        name = str(sub.GetName() or "")
        yield from _walk_bins(sub, f"{prefix}{BIN_SEPARATOR}{name}" if prefix else name)


def find_bin(pool: Pool, path: str | None) -> LocatedBin:
    """The bin at ``path``. ``None`` or ``""`` is the root."""
    root = _root(pool)
    folder = root
    walked: list[str] = []
    for segment in _segments(path, root):
        matches = [sub for sub in _subfolders(folder) if str(sub.GetName() or "") == segment]
        if not matches:
            raise BinNotFoundError(str(path), bin_paths(pool))
        folder = matches[0]
        walked.append(segment)
    return LocatedBin(BIN_SEPARATOR.join(walked), folder, created=False)


def ensure_bin(pool: Pool, path: str | None) -> LocatedBin:
    """The bin at ``path``, creating it and any missing parents."""
    root = _root(pool)
    folder = root
    walked: list[str] = []
    created = False
    for segment in _segments(path, root):
        matches = [sub for sub in _subfolders(folder) if str(sub.GetName() or "") == segment]
        if matches:
            folder = matches[0]
        else:
            made = pool.AddSubFolder(folder, segment)
            if made is None:
                raise MediaOperationError(cause=f"Resolve refused to create the bin {segment!r}.")
            log.info("Created bin %s", segment)
            folder = made
            created = True
        walked.append(segment)
    return LocatedBin(BIN_SEPARATOR.join(walked), folder, created)


# --- clips ------------------------------------------------------------------------------


def _clips_under(where: LocatedBin, recursive: bool) -> Iterator[LocatedClip]:
    base = where.path
    if not recursive:
        for clip in where.folder.GetClipList() or []:
            yield LocatedClip(base, clip)
        return
    for suffix, sub in _walk_bins(where.folder):
        path = f"{base}{BIN_SEPARATOR}{suffix}" if base and suffix else (base or suffix)
        for clip in sub.GetClipList() or []:
            yield LocatedClip(path, clip)


def find_clip(pool: Pool, name: str, bin_path: str | None = None) -> LocatedClip:
    """One clip by exact name, searched under ``bin_path`` (the whole pool by default)."""
    searched = list(_clips_under(find_bin(pool, bin_path), recursive=True))
    matches = [found for found in searched if str(found.clip.GetName() or "") == name]
    if not matches:
        where = f"the bin {bin_path!r}" if bin_path else "the media pool"
        available = sorted({str(found.clip.GetName() or "") for found in searched})
        raise ClipNotFoundError(name, where, available)
    if len(matches) > 1:
        raise AmbiguousClipError(name, [found.bin_path for found in matches])
    return matches[0]


_logged_property_keys = False


def properties(clip: Clip) -> dict[str, str]:
    """Every clip property Resolve reports, enumerated — key names are never hard-coded.

    The key names are undocumented, so the first enumeration of a session is logged: when a
    reading comes back empty on a live machine, that line is what says whether the key was
    renamed or the value really was blank.
    """
    reported = clip.GetClipProperty()
    if not isinstance(reported, dict):
        return {}
    global _logged_property_keys
    if not _logged_property_keys:
        log.info("Clip properties Resolve reports: %s", sorted(str(key) for key in reported))
        _logged_property_keys = True
    return {str(key): "" if value is None else str(value) for key, value in reported.items()}


def _number(reported: dict[str, str], key: str) -> int | None:
    try:
        return int(float(reported.get(key, "")))
    except (TypeError, ValueError):
        return None


def _rate(reported: dict[str, str]) -> float | None:
    try:
        return float(reported.get(FPS, ""))
    except (TypeError, ValueError):
        return None


def is_offline(file_path: str) -> bool:
    """Whether the media behind a clip has moved away.

    Resolve exposes no offline flag, so this is a disk check. A clip with no path at all
    (multicam, compound) is pathless rather than offline. A sequence pattern is judged by
    its folder, since the pattern itself is never a file on disk.
    """
    if not file_path:
        return False
    path = Path(file_path)
    if SEQUENCE_TOKEN in path.name:
        return not path.parent.exists()
    return not path.exists()


def summarise(bin_path: str, clip: Clip, reported: dict[str, str] | None = None) -> dict[str, Any]:
    """The one-line view of a clip that list and import both return."""
    reported = reported if reported is not None else properties(clip)
    file_path = reported.get(FILE_PATH, "")
    return {
        "name": str(clip.GetName() or ""),
        "bin": bin_path,
        "file_path": file_path,
        "type": reported.get(TYPE, ""),
        "frames": _number(reported, FRAMES),
        "fps": _rate(reported),
        "resolution": reported.get(RESOLUTION, ""),
        "offline": is_offline(file_path),
    }


# --- import -----------------------------------------------------------------------------


def _requests(
    paths: list[str] | None,
    sequences: list[dict[str, Any]] | None,
) -> list[str | dict[str, Any]]:
    items: list[str | dict[str, Any]] = [str(path) for path in (paths or [])]
    for sequence in sequences or []:
        pattern = sequence.get("path")
        if not pattern:
            raise InvalidRequestError(
                cause="A sequence needs a path.",
                fix='Each sequence is {"path": "titles/song_%04d.png", '
                '"start_index": 1, "end_index": 96}.',
            )
        try:
            start = int(sequence.get("start_index", 0))
            end = int(sequence.get("end_index", 0))
        except (TypeError, ValueError) as exc:
            raise InvalidRequestError(
                cause=f"start_index and end_index must be whole frame numbers: {exc}.",
                fix='Example: {"path": "titles/song_%04d.png", "start_index": 1, "end_index": 96}.',
            ) from exc
        items.append({"FilePath": str(pattern), "StartIndex": start, "EndIndex": end})
    if not items:
        raise InvalidRequestError(
            cause="Nothing to import: both paths and sequences were empty.",
            fix="Pass paths for ordinary media, sequences for %0Nd image sequences.",
        )
    return items


def _requested_path(item: str | dict[str, Any]) -> str:
    return str(item["FilePath"]) if isinstance(item, dict) else str(item)


def _looks_like_image(file_path: str) -> bool:
    return Path(file_path).suffix.lower() in IMAGE_SUFFIXES


def apply_still_workaround(clip: Clip, reported: dict[str, str]) -> bool:
    """Write the out point once, so later ``endFrame`` values are honoured exactly.

    Resolve ignores ``endFrame`` when appending a freshly imported still — every append
    lands at the default still duration — until any out point has been written to the clip.
    The value does not matter, only that the write happened.
    """
    if not _looks_like_image(reported.get(FILE_PATH, "")):
        return False
    end = _number(reported, END)
    if end is None:
        frames = _number(reported, FRAMES)
        end = max((frames or 1) - 1, 0)
    if not clip.SetClipProperty(OUT, str(end)):
        log.warning("Still-duration workaround refused on %s", clip.GetName())
        return False
    return True


def import_media(
    connection: ResolveConnection,
    paths: list[str] | None = None,
    bin_path: str | None = None,
    sequences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Import media into ``bin_path``, creating the bin if needed.

    Imports land in the media pool's *current* folder, so the current folder is moved for
    the call and put back afterwards — a tool must not leave the GUI somewhere else.
    """
    pool = media_pool(connection)
    items = _requests(paths, sequences)
    target = ensure_bin(pool, bin_path)

    previous = pool.GetCurrentFolder()
    pool.SetCurrentFolder(target.folder)
    try:
        imported = list(pool.ImportMedia(items) or [])
    finally:
        if previous is not None:
            pool.SetCurrentFolder(previous)

    summaries: list[dict[str, Any]] = []
    landed: set[str] = set()
    for clip in imported:
        reported = properties(clip)
        landed.add(reported.get(FILE_PATH, ""))
        landed.add(str(clip.GetName() or ""))
        summary = summarise(target.path, clip, reported)
        summary["still_duration_workaround"] = apply_still_workaround(clip, reported)
        summaries.append(summary)

    refused = _not_imported(items, imported, landed)
    if not summaries:
        raise ImportFailedError(
            cause=f"Resolve imported none of the {len(items)} path(s) requested.",
            detail={"not_imported": refused},
        )
    log.info("Imported %d clip(s) into %r", len(summaries), target.path or "the root")
    return {"bin": target.path, "imported": summaries, "not_imported": refused}


def _not_imported(
    items: list[str | dict[str, Any]],
    imported: list[Clip],
    landed: set[str],
) -> list[str]:
    """Which requested paths produced no clip.

    A sequence is matched by the folder it came from, never by name: Resolve names the
    imported clip itself and nothing on record says what it calls it, so matching the
    ``%0Nd`` pattern against the result would be a guess. When everything asked for landed,
    no matching is needed at all.
    """
    if len(imported) == len(items):
        return []
    folders = {str(Path(path).parent) for path in landed if path}
    refused = []
    for item in items:
        requested = _requested_path(item)
        if isinstance(item, dict):
            if str(Path(requested).parent) in folders:
                continue
        elif requested in landed or Path(requested).name in landed:
            continue
        refused.append(requested)
    return refused


# --- list -------------------------------------------------------------------------------


def list_media(
    connection: ResolveConnection,
    bin_path: str | None = None,
    name_contains: str | None = None,
    offline_only: bool = False,
    recursive: bool = True,
    limit: int = DEFAULT_LIST_LIMIT,
    config: Config | None = None,
) -> dict[str, Any]:
    """Summarise the clips in a bin. Past ``limit`` the full listing spills to disk."""
    pool = media_pool(connection)
    where = find_bin(pool, bin_path)
    needle = (name_contains or "").lower()

    clips: list[dict[str, Any]] = []
    for found in _clips_under(where, recursive):
        summary = summarise(found.bin_path, found.clip)
        if needle and needle not in summary["name"].lower():
            continue
        if offline_only and not summary["offline"]:
            continue
        clips.append(summary)

    cap = max(int(limit), 0)
    truncated = len(clips) > cap
    result: dict[str, Any] = {
        "bin": where.path,
        "count": len(clips),
        "clips": clips[:cap] if truncated else clips,
        "truncated": truncated,
        "spilled_to": None,
    }
    if truncated:
        result["spilled_to"] = spill(
            result["bin"],
            {"bin": result["bin"], "count": len(clips), "clips": clips},
            config or get_config(),
            fallback="media-pool",
        )
    return result


# --- inspect ----------------------------------------------------------------------------


def _markers(clip: Clip) -> list[dict[str, Any]]:
    reported = clip.GetMarkers()
    if not isinstance(reported, dict):
        return []
    markers = []
    for frame, marker in reported.items():
        detail = marker if isinstance(marker, dict) else {}
        markers.append(
            {
                "frame": int(float(frame)),
                "color": str(detail.get("color", "")),
                "duration": detail.get("duration"),
                "name": str(detail.get("name", "")),
                "note": str(detail.get("note", "")),
                "custom_data": str(detail.get("customData", "")),
            }
        )
    return sorted(markers, key=lambda marker: marker["frame"])


def _audio_mapping(clip: Clip) -> dict[str, Any] | None:
    """``GetAudioMapping`` returns a JSON *string* — or nothing, on clips without audio."""
    try:
        reported = clip.GetAudioMapping()
    except Exception:  # noqa: BLE001 - an unmapped clip is not a failed inspection
        log.debug("Could not read the audio mapping", exc_info=True)
        return None
    if not reported:
        return None
    if isinstance(reported, dict):
        return reported
    try:
        parsed = json.loads(str(reported))
    except (TypeError, ValueError):
        log.warning("Unparseable audio mapping: %r", reported)
        return None
    return parsed if isinstance(parsed, dict) else None


def _bounds(clip: Clip, reported: dict[str, str]) -> dict[str, Any]:
    """Media bounds half-open ``[in, out)``, plus whatever mark in/out is set.

    Resolve reports ``End`` as the last frame; the cut file's convention is half-open, so
    the out point is that frame plus one and ``duration = out - in`` everywhere.
    """
    fps = _rate(reported)
    start = _number(reported, START)
    end = _number(reported, END)
    frames = _number(reported, FRAMES)
    out = end + 1 if end is not None else (start + frames if start is not None and frames else None)
    duration = out - start if out is not None and start is not None else frames

    marks: dict[str, Any] = {}
    try:
        reported_marks = clip.GetMarkInOut()
    except Exception:  # noqa: BLE001
        log.debug("Could not read mark in/out", exc_info=True)
        reported_marks = None
    for kind, bounds in (reported_marks or {}).items():
        if not isinstance(bounds, dict):
            continue
        marks[str(kind)] = {
            "in": dual_time(bounds.get("in"), fps),
            "out": dual_time(bounds.get("out"), fps),
        }

    return {
        "media": {
            "in": dual_time(start, fps),
            "out": dual_time(out, fps),
            "duration": dual_time(duration, fps),
        },
        "marks": marks,
    }


def inspect_clip(
    connection: ResolveConnection,
    name: str,
    bin_path: str | None = None,
) -> dict[str, Any]:
    """Everything worth knowing about one clip before cutting with it."""
    pool = media_pool(connection)
    found_in, clip = find_clip(pool, name, bin_path)
    reported = properties(clip)
    metadata = clip.GetMetadata()
    return {
        "clip": summarise(found_in, clip, reported),
        "properties": reported,
        "metadata": (
            {str(key): value for key, value in metadata.items()}
            if isinstance(metadata, dict)
            else {}
        ),
        "audio_mapping": _audio_mapping(clip),
        "markers": _markers(clip),
        "bounds": _bounds(clip, reported),
    }


# --- metadata ---------------------------------------------------------------------------


def _set_field(clip: Clip, key: str, value: Any, reported: dict[str, str]) -> str:
    """Write one field, choosing the route from what the clip itself reports.

    The property branch takes nearly everything. The one clip probed live so far (Resolve
    Studio 21.0.3.7) enumerated around 250 keys — the whole namespace, production metadata
    (``Scene``, ``Take``, ``Director``, ``Keyword``) included — while ``GetMetadata()`` on
    the same clip returned ``{}``. Writing ``Scene`` through ``SetClipProperty`` then read
    back through ``GetMetadata()``, so for that field the two accessors reach the same
    value; whether that holds across the other keys was not tested. The metadata branch is
    kept for a clip that reports less, which no probe has yet found.
    """
    if key in reported:
        if not clip.SetClipProperty(key, str(value)):
            raise MediaOperationError(cause=f"Resolve refused to set the clip property {key!r}.")
        return "clip_property"
    if not clip.SetMetadata(key, str(value)):
        raise MediaOperationError(cause=f"Resolve refused to set the metadata field {key!r}.")
    return "metadata"


def set_clip_metadata(
    connection: ResolveConnection,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply a batch of ``{clip, fields}`` writes. One bad item never sinks the batch."""
    pool = media_pool(connection)
    results: list[dict[str, Any]] = []
    for item in items or []:
        results.append(_apply_fields(pool, item))
    return {"results": results}


def _apply_fields(pool: Pool, item: dict[str, Any]) -> dict[str, Any]:
    name = str(item.get("clip") or "")
    fields = item.get("fields")
    if not name or not isinstance(fields, dict) or not fields:
        return {
            "clip": name,
            "bin": None,
            "ok": False,
            "error": InvalidRequestError(
                cause="Each item needs a clip name and a non-empty fields object.",
                fix='Example: {"clip": "C0012.mp4", "fields": {"Description": "guitar close"}}.',
            ).payload(),
        }
    try:
        located = find_clip(pool, name, item.get("bin"))
    except (ClipNotFoundError, AmbiguousClipError, BinNotFoundError) as exc:
        return {"clip": name, "bin": None, "ok": False, "error": exc.payload()}

    reported = properties(located.clip)
    applied: dict[str, str] = {}
    failed: dict[str, str] = {}
    for key, value in fields.items():
        try:
            applied[str(key)] = _set_field(located.clip, str(key), value, reported)
        except MediaOperationError as exc:
            failed[str(key)] = exc.cause
    return {
        "clip": name,
        "bin": located.bin_path,
        "ok": not failed,
        "applied": applied,
        "failed": failed,
    }


# --- organize ---------------------------------------------------------------------------


def organize_media(
    connection: ResolveConnection,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run a batch of bin operations, reporting each one separately."""
    pool = media_pool(connection)
    results: list[dict[str, Any]] = []
    for operation in operations or []:
        results.append(_run_operation(pool, operation))
    return {"results": results}


def _run_operation(pool: Pool, operation: dict[str, Any]) -> dict[str, Any]:
    name = str(operation.get("op") or "")
    try:
        if name == "create_bin":
            return _create_bin(pool, operation)
        if name == "move_clips":
            return _move_clips(pool, operation)
        raise InvalidRequestError(
            cause=f"Unknown media pool operation {name!r}.",
            fix='Operations are {"op": "create_bin", "bin": "Concert/Angles"} and '
            '{"op": "move_clips", "clips": ["C0012.mp4"], "to_bin": "Concert/Angles"}.',
        )
    except (
        AmbiguousClipError,
        BinNotFoundError,
        ClipNotFoundError,
        InvalidRequestError,
        MediaOperationError,
    ) as exc:
        return {"op": name, "ok": False, "error": exc.payload()}


def _create_bin(pool: Pool, operation: dict[str, Any]) -> dict[str, Any]:
    path = operation.get("bin")
    if not path:
        raise InvalidRequestError(
            cause="create_bin needs a bin path.",
            fix='Example: {"op": "create_bin", "bin": "Concert/Angles"}.',
        )
    made = ensure_bin(pool, str(path))
    return {"op": "create_bin", "ok": True, "bin": made.path, "created": made.created}


def _move_clips(pool: Pool, operation: dict[str, Any]) -> dict[str, Any]:
    names = operation.get("clips")
    target_path = operation.get("to_bin")
    if not isinstance(names, list) or not names or target_path is None:
        raise InvalidRequestError(
            cause="move_clips needs a clips list and a to_bin.",
            fix='Example: {"op": "move_clips", "clips": ["C0012.mp4"], "to_bin": "Angles"}.',
        )
    source = operation.get("from_bin")
    found = [find_clip(pool, str(name), source) for name in names]
    target = ensure_bin(pool, str(target_path))
    if not pool.MoveClips([located.clip for located in found], target.folder):
        raise MediaOperationError(
            cause=f"Resolve refused to move {len(found)} clip(s) into {target_path!r}.",
        )
    moved = [str(located.clip.GetName() or "") for located in found]
    log.info("Moved %d clip(s) into %r", len(moved), target_path)
    return {"op": "move_clips", "ok": True, "moved": moved, "to_bin": target.path}


# --- relink -----------------------------------------------------------------------------


def relink_media(
    connection: ResolveConnection,
    clips: list[str],
    path: str,
    bin_path: str | None = None,
) -> dict[str, Any]:
    """Point clips at media that has moved.

    A folder relinks every named clip through ``RelinkClips`` — Resolve matches by file
    name inside it. A file replaces one clip's media outright, which is the route for media
    that moved *and* was renamed.
    """
    pool = media_pool(connection)
    if not clips:
        raise InvalidRequestError(
            cause="No clips were named to relink.",
            fix="Pass the clip names shown by list_media(offline_only=True).",
        )
    target = Path(path)
    if not target.exists():
        raise RelinkFailedError(cause=f"Nothing exists at {path!r} on this machine.")

    found = [find_clip(pool, str(name), bin_path) for name in clips]
    was_offline = {
        str(located.clip.GetName() or ""): is_offline(properties(located.clip).get(FILE_PATH, ""))
        for located in found
    }
    if target.is_dir():
        relinked = bool(pool.RelinkClips([located.clip for located in found], str(target)))
        log.info("Relinked %d clip(s) against %s (Resolve said %s)", len(found), target, relinked)
    else:
        if len(found) != 1:
            raise InvalidRequestError(
                cause=f"{len(found)} clips were named but {path!r} is a single file.",
                fix="Relink one clip per file, or pass the folder the media moved to.",
            )
        name = str(found[0].clip.GetName() or "")
        if not was_offline.get(name):
            # Not an error — repointing a healthy clip is a legitimate thing to ask for —
            # but it replaces media that was working, so it says so rather than going quiet.
            log.warning("Replacing the media of %s, which was not offline", name)
        if not found[0].clip.ReplaceClip(str(target)):
            raise RelinkFailedError(
                cause=f"Resolve refused to replace the clip media with {path!r}.",
            )
        log.info("Replaced the media of %s with %s", name, target)

    results = []
    for located in found:
        clip_name = str(located.clip.GetName() or "")
        file_path = properties(located.clip).get(FILE_PATH, "")
        offline = is_offline(file_path)
        results.append(
            {
                "clip": clip_name,
                "bin": located.bin_path,
                "ok": not offline,
                "file_path": file_path,
                "offline": offline,
                "was_offline": was_offline.get(clip_name, False),
            }
        )
    return {"results": results}
