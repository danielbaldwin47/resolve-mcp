"""Media pool tools — how media enters the project and how the agent reads it.

Bin paths are slash-separated from the media pool root ("Concert/Angles") and
case-sensitive. Clips are named exactly as the media pool shows them; when a name appears
in more than one bin, pass bin= to say which.
"""

from __future__ import annotations

from typing import Any

from ..resolve import media
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def import_media(
    paths: list[str] | None = None,
    bin: str | None = None,  # noqa: A002 - the agent-facing word for a media pool folder
    sequences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Import media into a bin, creating the bin (and any missing parents) if needed.

    Use paths for ordinary files. Use sequences for PNG and other image sequences, one
    entry per sequence: {"path": "D:/titles/song_%04d.png", "start_index": 1,
    "end_index": 96}. Image media gets the still-duration workaround applied at import, so
    later timeline appends honour the exact durations you ask for.

    With no bin, each item gets a bin suggested by media type: video into
    02_Footage/<Camera> (camera model read from clip metadata after import, falling back
    to the camera's XML sidecar for media Resolve reports no camera metadata for, such as
    Sony mirrorless MP4; plain 02_Footage when neither is readable), audio into 03_Audio,
    stills and image sequences into 04_Assets — bins created on demand. Each imported clip
    reports the bin used and bin_source (camera_metadata, camera_sidecar, fallback,
    media_type, or explicit). Suggestions are category-from-root; in a multi-gig project
    supply the <Gig>/ prefix yourself via bin=.
    In a project that already has its own bin structure, check that structure first and
    pass bin= to follow it rather than accepting a suggestion — a suggestion is never
    enforcement, and an explicit bin (any path at all) bypasses it entirely.

    Returns a summary per imported clip, plus not_imported for anything Resolve refused —
    usually a path that is not readable from the machine Resolve runs on.
    """
    connection = get_connection()
    return media.import_media(connection, paths=paths, bin_path=bin, sequences=sequences)


@tool
def list_media(
    bin: str | None = None,  # noqa: A002
    name_contains: str | None = None,
    offline_only: bool = False,
    recursive: bool = True,
    limit: int = media.DEFAULT_LIST_LIMIT,
) -> dict[str, Any]:
    """Summarise media pool clips: name, bin, path, type, frames, fps, resolution, offline.

    Without a bin this walks the whole pool, recursive says whether a named one includes
    its subfolders, and the bin reported against each clip reads that clip back verbatim —
    pass it to any tool that takes a bin, "" included.
    offline_only shows the clips whose media has
    moved away — the ones relink_media exists for. Past limit clips the full listing is
    written to disk and spilled_to holds the path, so a large pool never blows the token
    budget: read or grep that file for the rest.
    """
    connection = get_connection()
    return media.list_media(
        connection,
        bin_path=bin,
        name_contains=name_contains,
        offline_only=offline_only,
        recursive=recursive,
        limit=limit,
    )


@tool
def inspect_clip(
    clip: str,
    bin: str | None = None,  # noqa: A002
    recursive: bool = True,
) -> dict[str, Any]:
    """Read one clip in full: properties, metadata, audio mapping, markers, in/out bounds.

    Bounds are dual time — frames, seconds, timecode and fps — with media bounds half-open
    [in, out), the same convention the cut file uses. audio_mapping carries the linked-audio
    paths and sample offsets for externally synced audio; markers are the clip's own,
    numbered relative to the clip.

    bin: omit it to search the whole pool, name a bin to search it and its subfolders, or
    pass "" for the pool root alone — the form that names a root clip whose name is also
    used inside a bin. list_media's reported bin is always the value to pass here.
    recursive=false stops the search descending, so only the clips the named bin holds
    itself are looked at — the way to reach a copy that a subfolder also holds a copy of.
    """
    connection = get_connection()
    return media.inspect_clip(connection, clip, bin_path=bin, recursive=recursive)


@tool
def set_clip_metadata(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply a batch of metadata writes: [{"clip": name, "bin": optional, "fields": {...}}].

    An item may add "recursive": false to keep its bin lookup out of that bin's subfolders.
    Each field is routed by what the clip itself reports: a key Resolve lists as a clip
    property (FPS, Super Scale, Out …) is written as one, everything else becomes clip
    metadata. The route taken comes back per field in applied. One item failing never sinks
    the batch — every item gets its own result.
    """
    connection = get_connection()
    return media.set_clip_metadata(connection, items)


@tool
def organize_media(operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Run a batch of bin operations, each reported separately.

    Two operations:
      {"op": "create_bin", "bin": "Concert/Angles"} — creates missing parents, and is
        happy if the bin already exists (created says which happened);
      {"op": "move_clips", "clips": ["C0012.mp4"], "to_bin": "Concert/Angles",
        "from_bin": optional, "recursive": optional} — moves clips, creating the target
        bin if needed. recursive: false keeps the from_bin lookup out of its subfolders.
    """
    connection = get_connection()
    return media.organize_media(connection, operations)


@tool
def relink_media(
    clips: list[str],
    path: str,
    bin: str | None = None,  # noqa: A002
    recursive: bool = True,
) -> dict[str, Any]:
    """Point offline clips at media that has moved, and report what came back online.

    Give a folder to relink several clips at once — Resolve matches them by file name
    inside it. Give a file to replace one clip's media outright, which is the route for
    media that moved and was renamed. Each result says whether that clip is still offline.
    The file route also renames the pool clip after the new file — Resolve's behaviour,
    not ours — so follow-up calls must use the name the result reports, not the one
    passed in.
    recursive=false keeps the bin lookup out of that bin's subfolders.
    """
    connection = get_connection()
    return media.relink_media(connection, clips, path, bin_path=bin, recursive=recursive)


TOOLS: tuple[Any, ...] = (
    import_media,
    list_media,
    inspect_clip,
    set_clip_metadata,
    organize_media,
    relink_media,
)

__all__ = [
    "TOOLS",
    "import_media",
    "inspect_clip",
    "list_media",
    "organize_media",
    "relink_media",
    "set_clip_metadata",
]
