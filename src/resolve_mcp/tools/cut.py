"""The cut-file tools: the contract, and the dry run that holds you to it.

A cut file is yours — you author it, the server never writes it. These two tools are how
you find out what it must contain and whether the one you wrote will build.
"""

from __future__ import annotations

from typing import Any

from ..resolve import build, cut
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def get_cut_schema() -> dict[str, Any]:
    """Return the cut-file schema v1, its annotated example, and the validation rules.

    Read this before authoring or editing a cut file — the format is not guessable and
    the example is the contract. `rules` lists every error that blocks a build and every
    warning that does not. Needs no project open.
    """
    return cut.get_cut_schema()


@tool
def validate_cut(
    cut_file: str,
    min_segment_frames: int = cut.MIN_SEGMENT_FRAMES,
) -> dict[str, Any]:
    """Dry-run a cut file and return every error and warning it has, with fix hints.

    Run this after every edit: build_timeline runs the identical checks pre-flight, so a
    file that is valid here will not abort a build. Each finding names the rule, the
    segment or overlay id, what is wrong and how to fix it — all of them at once, not the
    first. `min_segment_frames` tunes the W1 flash-frame warning only; it never blocks.
    """
    connection = get_connection()
    return cut.validate_cut(connection, cut_file, min_segment_frames)


@tool
def build_timeline(
    cut_file: str,
    min_segment_frames: int = cut.MIN_SEGMENT_FRAMES,
) -> dict[str, Any]:
    """Build a cut file into a new `<name> v<N>` timeline and report what landed.

    Every build makes a new version and never touches an earlier one, so rebuilding after
    an edit is always safe. The segments land butt-joined in document order over one
    continuous master-audio clip; positions are computed, so gaps cannot happen.

    The validate_cut rules run first: a single error aborts before any timeline is created,
    and comes back with the same per-segment findings. The report echoes the cut file's
    content hash, which is what ties the timeline back to the exact cut that made it —
    record it if you note the version anywhere. A failure names what did not land; a
    partially built version, if one was made, is scrap and can be deleted.
    """
    connection = get_connection()
    return build.build_timeline(connection, cut_file, min_segment_frames)


TOOLS: tuple[Any, ...] = (
    get_cut_schema,
    validate_cut,
    build_timeline,
)

__all__ = ["TOOLS", "build_timeline", "get_cut_schema", "validate_cut"]
