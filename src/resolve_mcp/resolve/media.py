"""Media pool wrappers: import, list, inspect, metadata, bins, relink.

Thin, testable, and MCP-free like the rest of this layer. Five things here are decisions
rather than API calls, and are the reason this file exists at all:

* **Bin paths are slash-separated from the media pool root** (``"Concert/Angles"``), the
  root's own name optional as a first segment. Resolve's API has no bin path — only
  ``GetSubFolderList()`` walks — and "current folder" state that every import depends on.
* **Offline means the media is gone**, not that Resolve says so: there is no offline flag in
  the scripting API. A clip is offline when nothing on disk stands behind its ``File Path``
  — the path itself for ordinary media, the first frame for a sequence, whose path is only
  a label (``shot_[0001-0024].png``, #85). A clip with no path at all (multicam, compound)
  is not offline, it is pathless.
* **Metadata fields route by what the clip itself reports.** The clip property key names are
  undocumented, so nothing is hard-coded: each clip is enumerated once with
  ``GetClipProperty()`` and a field whose key is in that dict is written as a clip property,
  everything else as metadata. The route taken comes back in the result; on real media that
  dict is large enough that the property branch takes nearly everything — see ``_set_field``.
* **Image media gets the still-duration workaround at import.** A one-time
  ``SetClipProperty("Out", …)`` is what makes ``endFrame`` respected on stills later; doing
  it at import means no timeline code ever has to remember.
* **A no-bin import gets a suggested bin, never an enforced one** (#57). The layout:
  ``[<Gig>/]01_Timelines``, ``02_Footage/<Camera>`` (B-Roll alongside; angle-description
  bins allowed when camera metadata is unreadable), ``03_Audio``, ``04_Assets`` (with
  ``Text/<Song>`` for title assets). The ``<Gig>`` level exists only when a project holds
  more than one gig, and only the agent can know the gig, so that prefix arrives as an
  explicit bin. Numbered categories use underscores; gig and song bins use human
  formatting; clips live in leaf bins. Angle identity is two-layer: bins encode source
  role (camera, roll class) while the angle sidecars (#13) stay canonical for descriptive
  semantics — a bin move is an address change (cut files update per the alternates spec)
  and sidecars keyed on clip name persist. Existing projects are adaptive: the agent reads
  and follows the structure it finds; the template is for empty ground; nothing is ever
  reorganized unasked.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
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
from .camera_sidecar import camera_model as recorded_camera_model
from .connection import ResolveConnection

log = get_logger("media")

BIN_SEPARATOR = "/"
DEFAULT_LIST_LIMIT = 200
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx", ".tga"})
AUDIO_SUFFIXES = frozenset({".aac", ".aif", ".aiff", ".flac", ".m4a", ".mp3", ".ogg", ".wav"})
GRAPHIC_SUFFIXES = frozenset({".ai", ".eps", ".psd", ".svg"})
FOOTAGE_BIN = "02_Footage"
AUDIO_BIN = "03_Audio"
ASSETS_BIN = "04_Assets"
# Camera fields in priority order, each consulted in the clip's properties then its
# metadata. Live-verified on real FX6 XAVC (2026-08-07, Resolve Studio, #94): the model
# lives in "Camera TC Type" ("ILME-FX6V") while "Camera Type" holds the manufacturer
# ("Sony"), so the model key must win or every camera bins as its make. Media that reports
# no camera key at all — A7-series MP4 on an M4ROOT card — gets a second look in the
# sidecar beside the clip (see :mod:`.sidecar`); only a camera unreadable both ways falls
# back to the bare footage bin rather than guessing.
CAMERA_KEYS = ("Camera TC Type", "Camera Type")
SEQUENCE_TOKEN = "%"
# Resolve paths an imported image sequence by folding the index range into the name —
# shot_[0001-0024].png — a label, not a file (#85, Resolve Studio 21.0.3.7).
SEQUENCE_RANGE = re.compile(r"\[(\d+)-\d+\]")

FILE_PATH = "File Path"
FRAMES = "Frames"
FPS = "FPS"
START = "Start"
END = "End"
OUT = "Out"
TYPE = "Type"
RESOLUTION = "Resolution"
AUDIO_CHANNELS = "Audio Ch"

Clip = Any
Folder = Any
Pool = Any
Project = Any


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
    return pool_of(project)


def pool_of(project: Project) -> Pool:
    """The pool of a project already in hand — the same walk, without repeating it.

    A caller that has the project must not reach for it a second time: two walks are two
    chances to land on different projects if the GUI switches between them, and the pool
    would then not belong to the project the rest of the call is reading.
    """
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


def clip_at_path(pool: Pool, bin_path: str, file_path: str) -> LocatedClip | None:
    """The clip in ``bin_path`` that already stands for ``file_path``, if one does.

    Identity is the first frame on disk rather than the reported path or the clip name,
    because Resolve renames and re-paths an imported sequence into a folded label
    (:func:`first_frame_of`). ``None`` — rather than a raise — because "not imported yet"
    is the ordinary answer on a first run, not a failure.
    """
    wanted = Path(first_frame_of(file_path))
    for found in _clips_under(find_bin(pool, bin_path), recursive=True):
        standing = properties(found.clip).get(FILE_PATH, "")
        if standing and Path(first_frame_of(standing)) == wanted:
            return found
    return None


def clips_named(pool: Pool, names: Iterable[str]) -> list[LocatedClip]:
    """Every clip anywhere in the pool whose name is one of ``names``.

    Unlike :func:`find_clip` this reports the duplicates instead of raising on them: a
    validation pass has to name every ambiguity at once, not stop at the first.
    """
    wanted = set(names)
    return [
        found
        for found in _clips_under(find_bin(pool, None), recursive=True)
        if str(found.clip.GetName() or "") in wanted
    ]


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


def frame_rate(reported: dict[str, str]) -> float | None:
    """The clip's frame rate, or ``None`` when Resolve does not report one (audio, stills)."""
    try:
        return float(reported.get(FPS, ""))
    except (TypeError, ValueError):
        return None


def frame_bounds(reported: dict[str, str]) -> tuple[int | None, int | None]:
    """Media bounds as half-open ``[start, out)``.

    Resolve reports ``End`` as the last frame; every range in this server is half-open, so
    the out point is that frame plus one. Frame count is the fallback when ``End`` is
    missing. The rule lives here so bounds mean the same thing to a listing and to a cut.
    """
    start = _number(reported, START)
    end = _number(reported, END)
    frames = _number(reported, FRAMES)
    out = end + 1 if end is not None else (start + frames if start is not None and frames else None)
    return start, out


def audio_channels(reported: dict[str, str]) -> int | None:
    """How many audio channels the clip carries, or ``None`` when Resolve does not say."""
    return _number(reported, AUDIO_CHANNELS)


def is_still(reported: dict[str, str]) -> bool:
    """Whether the clip is an image rather than moving footage.

    Judged by file suffix: the ``Type`` property does not separate a still from a
    sequence, and the suffix is what the still-duration workaround already keys off.
    """
    return _looks_like_image(reported.get(FILE_PATH, ""))


def first_frame_of(file_path: str) -> str:
    """The one file a clip's path really stands for, sequence labels unfolded.

    Resolve reports an imported sequence as ``shot_[0001-0024].png`` (#85) — a label, not
    a path — so the first frame is the only form of a sequence's address that is a real
    file both before the import (where the caller has a ``%0Nd`` pattern) and after it.
    That makes it the identity a re-run can recognise an already-imported card by.
    Anything that is not a folded range comes back unchanged.
    """
    folded = SEQUENCE_RANGE.search(Path(file_path).name)
    if folded is None:
        return file_path
    path = Path(file_path)
    return str(path.with_name(SEQUENCE_RANGE.sub(folded.group(1), path.name, count=1)))


def is_offline(file_path: str) -> bool:
    """Whether the media behind a clip has moved away.

    Resolve exposes no offline flag, so this is a disk check. A clip with no path at all
    (multicam, compound) is pathless rather than offline. Sequence paths are never files on
    disk, so they get judged by what stands behind them: a ``%0Nd`` pattern by its folder
    (the range is unknown), a bracketed range — the form Resolve reports for an imported
    sequence (#85) — by its first frame, which the range makes constructible.
    """
    if not file_path:
        return False
    path = Path(file_path)
    if path.exists():
        return False
    if SEQUENCE_TOKEN in path.name:
        return not path.parent.exists()
    first = first_frame_of(file_path)
    if first != file_path:
        return not Path(first).exists()
    return True


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
        "fps": frame_rate(reported),
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
    if not is_still(reported):
        return False
    end = _number(reported, END)
    if end is None:
        frames = _number(reported, FRAMES)
        end = max((frames or 1) - 1, 0)
    if not clip.SetClipProperty(OUT, str(end)):
        log.warning("Still-duration workaround refused on %s", clip.GetName())
        return False
    return True


def import_into(pool: Pool, items: list[str | dict[str, Any]], target: LocatedBin) -> list[Clip]:
    """``ImportMedia`` into one bin, and leave the pool where it was found.

    Imports land in the media pool's *current* folder, so the current folder is moved for
    the call and put back afterwards — a tool must not leave the GUI somewhere else. Every
    route that imports goes through here so no route can forget the second half.
    """
    previous = pool.GetCurrentFolder()
    pool.SetCurrentFolder(target.folder)
    try:
        return list(pool.ImportMedia(items) or [])
    finally:
        if previous is not None:
            pool.SetCurrentFolder(previous)


def import_media(
    connection: ResolveConnection,
    paths: list[str] | None = None,
    bin_path: str | None = None,
    sequences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Import media into ``bin_path``, creating the bin if needed.

    With no ``bin_path`` at all, every item gets a bin suggested by media type — the
    module docstring's fifth decision (#57). An explicit bin, the root included, bypasses
    suggestion entirely.
    """
    pool = media_pool(connection)
    items = _requests(paths, sequences)
    if bin_path is None:
        return _import_suggested(pool, items)
    target = ensure_bin(pool, bin_path)
    imported = import_into(pool, items, target)

    summaries: list[dict[str, Any]] = []
    landed: set[str] = set()
    for clip in imported:
        summaries.append(_clip_summary(clip, properties(clip), target.path, "explicit", landed))

    refused = _refused_or_fail(items, imported, landed, summaries)
    log.info("Imported %d clip(s) into %r", len(summaries), target.path or "the root")
    return {"bin": target.path, "imported": summaries, "not_imported": refused}


def _import_suggested(pool: Pool, items: list[str | dict[str, Any]]) -> dict[str, Any]:
    """Import with no bin argument: each item is suggested a bin by media type (#57).

    Suggestion paths are category-from-root; the optional ``<Gig>/`` prefix is the agent's
    to supply via an explicit bin, because the server cannot know the gig. Suggestion is
    never enforcement — nothing here refuses a path for being off-convention; an explicit
    bin simply never reaches this function.
    """
    groups: dict[str, list[str | dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(_suggested_bin(item), []).append(item)

    summaries: list[dict[str, Any]] = []
    imported: list[Clip] = []
    landed: set[str] = set()
    for category, group in groups.items():
        target = ensure_bin(pool, category)
        arrived = import_into(pool, group, target)
        imported.extend(arrived)
        for clip in arrived:
            reported = properties(clip)
            placed = _placed(pool, clip, reported, target)
            summaries.append(_clip_summary(clip, reported, placed.path, placed.source, landed))

    refused = _refused_or_fail(items, imported, landed, summaries)
    bins = sorted({str(summary["bin"]) for summary in summaries})
    log.info("Imported %d clip(s) into suggested bin(s) %s", len(summaries), ", ".join(bins))
    return {"bins": bins, "imported": summaries, "not_imported": refused}


def _suggested_bin(item: str | dict[str, Any]) -> str:
    """Which #57 category an import request belongs to, judged before the import.

    Suffix, not the ``Type`` property, for the same reason as :func:`is_still`: Resolve
    types a sequence and a movie both ``Video``. A sequence request (a dict) is image
    frames by construction.
    """
    if isinstance(item, dict):
        return ASSETS_BIN
    if _looks_like_image(item):
        return ASSETS_BIN
    suffix = Path(item).suffix.lower()
    if suffix in GRAPHIC_SUFFIXES:
        return ASSETS_BIN
    if suffix in AUDIO_SUFFIXES:
        return AUDIO_BIN
    return FOOTAGE_BIN


class CameraModel(NamedTuple):
    """The model that will name the bin, and which reading produced it.

    Built through :meth:`read`, so the one rule about the name — a slash in it would read
    as a bin separator — is applied once however the model was found.
    """

    name: str
    source: str

    @classmethod
    def read(cls, model: str, source: str) -> CameraModel:
        return cls(model.replace(BIN_SEPARATOR, "-"), source)


def _camera_model(clip: Clip, reported: dict[str, str]) -> CameraModel | None:
    """The camera model for this clip, sanitised for bin use, or ``None``.

    Read after import — the model is embedded data Resolve surfaces, nothing the file
    path could carry. Key priority outranks dict priority: a model found anywhere beats a
    manufacturer found anywhere (see ``CAMERA_KEYS``), with the spec's clip properties
    consulted before metadata for each key.

    What Resolve knows always wins. The card's own sidecar is the last look rather than a
    preferred one, so media Resolve reads properly keeps binning exactly as it did before
    this existed, and the envelope says which reading answered.
    """
    metadata = clip.GetMetadata()
    dicts = [reported, metadata if isinstance(metadata, dict) else {}]
    for key in CAMERA_KEYS:
        for source in dicts:
            value = str(source.get(key) or "").strip()
            if value:
                return CameraModel.read(value, "camera_metadata")
    recorded = recorded_camera_model(reported.get(FILE_PATH, ""))
    if recorded:
        return CameraModel.read(recorded, "camera_sidecar")
    return None


class Placement(NamedTuple):
    """Where a suggested-mode clip ended up, and what the suggestion drew on."""

    path: str
    source: str


def _placed(pool: Pool, clip: Clip, reported: dict[str, str], target: LocatedBin) -> Placement:
    """Place one suggested clip: footage gets a second hop into its camera leaf.

    Footage is the one category whose final bin can only be known after import, once the
    clip's properties and metadata are readable.
    """
    if target.path != FOOTAGE_BIN:
        return Placement(target.path, "media_type")
    model = _camera_model(clip, reported)
    if model is None:
        return Placement(target.path, "fallback")
    leaf = ensure_bin(pool, f"{FOOTAGE_BIN}{BIN_SEPARATOR}{model.name}")
    if not pool.MoveClips([clip], leaf.folder):
        name = str(clip.GetName() or "")
        log.warning("Camera bin move refused for %r; leaving it in %r", name, target.path)
        return Placement(target.path, "fallback")
    return Placement(leaf.path, model.source)


def _clip_summary(
    clip: Clip,
    reported: dict[str, str],
    bin_used: str,
    source: str,
    landed: set[str],
) -> dict[str, Any]:
    """One imported clip's envelope line: the summary, the bin, and how the bin was chosen."""
    landed.add(reported.get(FILE_PATH, ""))
    landed.add(str(clip.GetName() or ""))
    summary = summarise(bin_used, clip, reported)
    summary["still_duration_workaround"] = apply_still_workaround(clip, reported)
    summary["bin_source"] = source
    return summary


def _refused_or_fail(
    items: list[str | dict[str, Any]],
    imported: list[Clip],
    landed: set[str],
    summaries: list[dict[str, Any]],
) -> list[str]:
    refused = _not_imported(items, imported, landed)
    if not summaries:
        raise ImportFailedError(
            cause=f"Resolve imported none of the {len(items)} path(s) requested.",
            detail={"not_imported": refused},
        )
    return refused


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


def audio_mapping(clip: Clip) -> dict[str, Any] | None:
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
    fps = frame_rate(reported)
    start, out = frame_bounds(reported)
    duration = (
        out - start if out is not None and start is not None else _number(reported, FRAMES)
    )

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
        "audio_mapping": audio_mapping(clip),
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
    that moved *and* was renamed. ``ReplaceClip`` also renames the pool clip after the new
    file (#85), so ``was_offline`` is captured per position before the call — afterwards
    the clip no longer answers to the name the caller used.
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
    was_offline = [
        is_offline(properties(located.clip).get(FILE_PATH, "")) for located in found
    ]
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
        if not was_offline[0]:
            # Not an error — repointing a healthy clip is a legitimate thing to ask for —
            # but it replaces media that was working, so it says so rather than going quiet.
            log.warning("Replacing the media of %s, which was not offline", name)
        if not found[0].clip.ReplaceClip(str(target)):
            raise RelinkFailedError(
                cause=f"Resolve refused to replace the clip media with {path!r}.",
            )
        log.info("Replaced the media of %s with %s", name, target)

    results = []
    for located, before in zip(found, was_offline, strict=True):
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
                "was_offline": before,
            }
        )
    return {"results": results}
