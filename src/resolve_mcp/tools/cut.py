"""The cut-file tools: the contract, and the dry run that holds you to it.

A cut file is yours — you author it, the server never writes it. These two tools are how
you find out what it must contain and whether the one you wrote will build.
"""

from __future__ import annotations

from typing import Any

from ..cut.validate import DEFAULT_MIN_SEGMENT_FRAMES
from ..resolve import cut
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
    min_segment_frames: int = DEFAULT_MIN_SEGMENT_FRAMES,
) -> dict[str, Any]:
    """Dry-run a cut file and return every error and warning it has, with fix hints.

    Run this after every edit: build_timeline runs the identical checks pre-flight, so a
    file that is valid here will not abort a build. Each finding names the rule, the
    segment or overlay id, what is wrong and how to fix it — all of them at once, not the
    first. `min_segment_frames` tunes the W1 flash-frame warning only; it never blocks.
    """
    connection = get_connection()
    return cut.validate_cut(connection, cut_file, min_segment_frames)


TOOLS: tuple[Any, ...] = (
    get_cut_schema,
    validate_cut,
)

__all__ = ["TOOLS", "get_cut_schema", "validate_cut"]
