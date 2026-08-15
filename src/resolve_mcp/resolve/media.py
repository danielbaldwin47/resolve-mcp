"""The six media operations: import, list, inspect, metadata, organize, relink.

Thin, testable, and MCP-free like the rest of this layer — one function per media tool,
each of them a caller of the pool adapter in :mod:`.pool`, which owns bin addressing, clip
lookup and clip reading. Nothing outside this module should import a pool name *through*
here; import it from :mod:`.pool` directly. Three things here are decisions rather than API
calls, and are the reason this file exists at all:

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
    RelinkFailedError,
)
from ..logging_config import get_logger
from ..spill import spill
from ..timing import dual_time
from .camera_sidecar import camera_model as recorded_camera_model
from .connection import ResolveConnection
from .pool import (
    BIN_SEPARATOR,
    FILE_PATH,
    Clip,
    LocatedBin,
    Pool,
    apply_still_workaround,
    audio_mapping,
    clips_under,
    ensure_bin,
    find_bin,
    find_clip,
    frame_bounds,
    frame_count,
    frame_rate,
    import_into,
    is_offline,
    looks_like_image,
    media_pool,
    properties,
    summarise,
)

log = get_logger("media")

DEFAULT_LIST_LIMIT = 200
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
# sidecar beside the clip (see :mod:`.camera_sidecar`); only a camera unreadable both ways
# falls back to the bare footage bin rather than guessing.
CAMERA_KEYS = ("Camera TC Type", "Camera Type")


def _wants_recursion(item: dict[str, Any]) -> bool:
    """A batch item's optional ``recursive`` key — deep unless it says otherwise (#134).

    A batch item is free-form JSON, so the key is checked rather than coerced: ``bool()``
    would read the string ``"false"`` as True and quietly search the opposite of what was
    asked, which is the kind of silence a lookup flag cannot afford.
    """
    wanted = item.get("recursive", True)
    if not isinstance(wanted, bool):
        raise InvalidRequestError(
            cause=f"recursive must be true or false, not {wanted!r}.",
            fix='Pass a JSON boolean: "recursive": false to search the named bin alone.',
        )
    return wanted


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


def import_media(
    connection: ResolveConnection,
    paths: list[str] | None = None,
    bin_path: str | None = None,
    sequences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Import media into ``bin_path``, creating the bin if needed.

    With no ``bin_path`` at all, every item gets a bin suggested by media type — the
    module docstring's third decision (#57). An explicit bin, the root included, bypasses
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

    Suffix, not the ``Type`` property, for the same reason as ``pool.is_still``: Resolve
    types a sequence and a movie both ``Video``. A sequence request (a dict) is image
    frames by construction.
    """
    if isinstance(item, dict):
        return ASSETS_BIN
    if looks_like_image(item):
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
    for found in clips_under(where, recursive):
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


def _bounds(clip: Clip, reported: dict[str, str]) -> dict[str, Any]:
    """Media bounds half-open ``[in, out)``, plus whatever mark in/out is set.

    Resolve reports ``End`` as the last frame; the cut file's convention is half-open, so
    the out point is that frame plus one and ``duration = out - in`` everywhere.
    """
    fps = frame_rate(reported)
    # The same rate feeds the Duration fallback, so a listing and a cut read one bounds —
    # except when the clip itself reports no rate (audio-only media): a listing has no
    # timeline to borrow a rate from, so its bounds stay unknown while a cut, which does,
    # can still derive them.
    start, out = frame_bounds(reported, fps=fps)
    duration = out - start if out is not None and start is not None else frame_count(reported)

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
    recursive: bool = True,
) -> dict[str, Any]:
    """Everything worth knowing about one clip before cutting with it."""
    pool = media_pool(connection)
    found_in, clip = find_clip(pool, name, bin_path, recursive)
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
        located = find_clip(pool, name, item.get("bin"), _wants_recursion(item))
    except (ClipNotFoundError, AmbiguousClipError, BinNotFoundError, InvalidRequestError) as exc:
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
    deep = _wants_recursion(operation)
    found = [find_clip(pool, str(name), source, deep) for name in names]
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
    recursive: bool = True,
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

    found = [find_clip(pool, str(name), bin_path, recursive) for name in clips]
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
