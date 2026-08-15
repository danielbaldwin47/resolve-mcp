"""The media pool adapter: reaching the pool, addressing bins, locating and reading clips.

Thin, testable, and MCP-free like the rest of this layer. This is the half of the old
``media`` module that everything else consumes — the cut contract, audio acquisition,
titles application, video source resolution — and that the six media operations in
:mod:`.media` are themselves callers of. Two things here are decisions rather than API
calls, and are the reason this file exists at all:

* **Bin paths are slash-separated from the media pool root** (``"Concert/Angles"``), the
  root's own name optional as a first segment. Resolve's API has no bin path — only
  ``GetSubFolderList()`` walks — and "current folder" state that every import depends on.
* **Offline means the media is gone**, not that Resolve says so: there is no offline flag in
  the scripting API. A clip is offline when nothing on disk stands behind its ``File Path``
  — the path itself for ordinary media, the first frame for a sequence, whose path is only
  a label (``shot_[0001-0024].png``, #85). A clip with no path at all (multicam, compound)
  is not offline, it is pathless.

:func:`apply_still_workaround` sits here as a primitive rather than a policy: *when* a still
gets its out point written is the import's decision (see :mod:`.media`), but *what* the
write is stays with the clip reading that judges a still in the first place.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, NamedTuple

from ..errors import (
    AmbiguousClipError,
    BinNotFoundError,
    ClipNotFoundError,
    MediaOperationError,
    MediaPoolUnavailableError,
    NoProjectOpenError,
)
from ..logging_config import get_logger
from ..timing import frames_from_timecode
from .connection import ResolveConnection

# A child of the operations module's logger, so anything configured for "media" still
# catches the adapter — but a bin creation or a still-workaround refusal says which half
# of the old module it came from, which is the whole point of the line in a live failure.
log = get_logger("media.pool")

BIN_SEPARATOR = "/"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx", ".tga"})
SEQUENCE_TOKEN = "%"
# Resolve paths an imported image sequence by folding the index range into the name —
# shot_[0001-0024].png — a label, not a file (#85, Resolve Studio 21.0.3.7).
SEQUENCE_RANGE = re.compile(r"\[(\d+)-\d+\]")

FILE_PATH = "File Path"
DURATION = "Duration"
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


def clips_under(where: LocatedBin, recursive: bool) -> Iterator[LocatedClip]:
    """Every clip in a located bin, each paired with the bin path that addresses it."""
    base = where.path
    if not recursive:
        for clip in where.folder.GetClipList() or []:
            yield LocatedClip(base, clip)
        return
    for suffix, sub in _walk_bins(where.folder):
        path = f"{base}{BIN_SEPARATOR}{suffix}" if base and suffix else (base or suffix)
        for clip in sub.GetClipList() or []:
            yield LocatedClip(path, clip)


def _searched_label(bin_path: str | None, where: LocatedBin, deep: bool) -> str:
    """What a clip_not_found says it looked in — the caller's own words for a named bin.

    A shallow search says ``alone`` so the refusal points at the flag: the clip may well
    be one folder down, and dropping ``recursive`` is then the fix, not renaming anything.
    """
    if not where.path:
        return "the media pool" if bin_path is None and deep else "the media pool root"
    return f"the bin {bin_path!r}" if deep else f"the bin {bin_path!r} alone"


def _addressing(bins: list[str]) -> tuple[list[str], list[str]]:
    """Which bins holding a duplicated name reach one clip: passed alone, then passed shallow.

    One entry per matching clip comes in, so a repeat means one bin holds two of the name —
    no lookup singles those out, and neither list offers them. A bin whose subfolder holds
    another copy has no address of its own either, because a named bin is searched right
    through; it does hold exactly one clip itself, so ``recursive=False`` reaches that one
    (#134). The root is never shadowed: ``""`` is the root folder alone (#122).
    """
    counted = Counter(bins)
    alone = {path for path, times in counted.items() if times == 1}
    shadowed = {
        path
        for path in alone
        if path and any(other.startswith(f"{path}{BIN_SEPARATOR}") for other in bins)
    }
    return sorted(alone - shadowed), sorted(shadowed)


def find_clip(
    pool: Pool,
    name: str,
    bin_path: str | None = None,
    recursive: bool | None = None,
) -> LocatedClip:
    """One clip by exact name.

    ``None`` searches the whole pool. A named bin searches that bin and everything nested
    inside it. ``""`` — or the root's own name — is the root folder *alone*, not the pool:
    naming the root recursively would be the whole-pool search again, which left a root
    clip whose name a bin also used with no expressible address at all (#122). The empty
    string is what :func:`summarise` reports for a root clip, so the ``bin`` value a
    listing gives back always reads the clip it described.

    ``recursive=False`` stops the search descending, so the clips a bin holds *itself* are
    all that is looked at — the address of a copy shadowed by another of the name filed
    deeper (#134). With no bin it narrows to the root, the way ``list_media`` does;
    ``bin=""`` and the root's own name are already that search, so they are unaffected.

    ``recursive=None`` is a caller with no such flag of its own to pass on — the video and
    analysis tools, which resolve a clip by name but take no ``recursive``. It searches
    deep like ``True``, and an ambiguity is not offered the shallow form, because a fix
    naming an argument its caller cannot accept is the #122 defect one axis over.
    """
    where = find_bin(pool, bin_path)
    the_root_itself = bin_path is not None and not where.path
    deep = recursive is not False and not the_root_itself
    searched = list(clips_under(where, deep))
    matches = [found for found in searched if str(found.clip.GetName() or "") == name]
    if not matches:
        available = sorted({str(found.clip.GetName() or "") for found in searched})
        raise ClipNotFoundError(name, _searched_label(bin_path, where, deep), available)
    if len(matches) > 1:
        found_in = [found.bin_path for found in matches]
        deep_address, shallow_address = _addressing(found_in)
        raise AmbiguousClipError(
            name,
            found_in,
            deep_address,
            shallow_address if recursive is not None else [],
        )
    return matches[0]


def clip_at_path(pool: Pool, bin_path: str, file_path: str) -> LocatedClip | None:
    """The clip in ``bin_path`` that already stands for ``file_path``, if one does.

    Identity is the first frame on disk rather than the reported path or the clip name,
    because Resolve renames and re-paths an imported sequence into a folded label
    (:func:`first_frame_of`). ``None`` — rather than a raise — because "not imported yet"
    is the ordinary answer on a first run, not a failure.
    """
    wanted = Path(first_frame_of(file_path))
    for found in clips_under(find_bin(pool, bin_path), recursive=True):
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
        for found in clips_under(find_bin(pool, None), recursive=True)
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


def frame_count(reported: dict[str, str]) -> int | None:
    """How many frames Resolve says the clip holds, or ``None`` when it says nothing.

    The raw ``Frames`` reading, unlike :func:`frame_bounds`, which derives bounds when the
    count is missing — the fallback a duration a listing has no rate for cannot take.
    """
    return _number(reported, FRAMES)


def frame_rate(reported: dict[str, str]) -> float | None:
    """The clip's frame rate, or ``None`` when Resolve does not report one (audio, stills)."""
    try:
        return float(reported.get(FPS, ""))
    except (TypeError, ValueError):
        return None


def frame_bounds(
    reported: dict[str, str], fps: float | None = None
) -> tuple[int | None, int | None]:
    """Media bounds as half-open ``[start, out)``.

    Resolve reports ``End`` as the last frame; every range in this server is half-open, so
    the out point is that frame plus one. Frame count is the fallback when ``End`` is
    missing. The rule lives here so bounds mean the same thing to a listing and to a cut.

    Audio-only clips report ``Start``, ``End`` and ``Frames`` as empty strings (#46,
    live-verified): only ``Duration`` carries the length, as timecode. So whenever the
    out point is unreadable and ``Duration`` parses, the duration stands in — bounds are
    ``[start, start + duration)`` with an unreported start read as 0 — counted at the
    clip's own rate or, since audio reports no rate either, at the caller's ``fps`` (the
    timeline's, for a cut). With no rate at all, or no parseable ``Duration``, the
    unknowns stay ``None`` — unknown, never invented.
    """
    start = _number(reported, START)
    end = _number(reported, END)
    frames = _number(reported, FRAMES)
    out = end + 1 if end is not None else (start + frames if start is not None and frames else None)
    if out is None:
        rate = frame_rate(reported) or fps
        duration = frames_from_timecode(reported.get(DURATION, ""), rate) if rate else None
        if duration is not None:
            begin = start if start is not None else 0
            log.debug(
                "End/Frames unreported; bounds %d-%d read from %s %r at %s fps",
                begin,
                begin + duration,
                DURATION,
                reported.get(DURATION),
                rate,
            )
            return begin, begin + duration
        # The case a live session most needs to see: nothing reported and nothing to
        # derive from, so every bounds-based check downstream silently fails open.
        log.debug(
            "End/Frames unreported and %s %r cannot stand in (rate %s); bounds unknown",
            DURATION,
            reported.get(DURATION),
            rate,
        )
    return start, out


def audio_channels(reported: dict[str, str]) -> int | None:
    """How many audio channels the clip carries, or ``None`` when Resolve does not say."""
    return _number(reported, AUDIO_CHANNELS)


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


def looks_like_image(file_path: str) -> bool:
    """Whether a path names image media, judged by suffix alone — see :func:`is_still`."""
    return Path(file_path).suffix.lower() in IMAGE_SUFFIXES


def is_still(reported: dict[str, str]) -> bool:
    """Whether the clip is an image rather than moving footage.

    Judged by file suffix: the ``Type`` property does not separate a still from a
    sequence, and the suffix is what the still-duration workaround already keys off.
    """
    return looks_like_image(reported.get(FILE_PATH, ""))


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
        "frames": frame_count(reported),
        "fps": frame_rate(reported),
        "resolution": reported.get(RESOLUTION, ""),
        "offline": is_offline(file_path),
    }


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
