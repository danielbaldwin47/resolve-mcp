"""What the agent will actually see, read back off the file ffmpeg wrote.

The dimensions are the point: a grab is only useful if it is inside the client's image cap,
and the only honest way to report that is to read the header rather than to repeat what was
asked for — a source smaller than the cap comes back at its own size, and a scale filter
that silently did nothing else would go unnoticed.

Only the frame header is parsed, with the standard library: a decoder would pull a large
dependency in to answer a question two integers in the SOF segment already answer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import FrameGrabError

SOI = b"\xff\xd8"
STANDALONE = frozenset({0x01, *range(0xD0, 0xD9)})
"""Markers that carry no length field: the restart markers, SOI, and TEM."""

SOF_MARKERS = frozenset(set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC})
"""Every start-of-frame flavour — baseline, progressive, lossless — but not DHT/JPG/DAC."""


def describe(path: Path | str) -> dict[str, Any]:
    """``path``, ``width``, ``height`` and ``bytes`` for a JPEG on disk."""
    resolved = Path(path)
    data = resolved.read_bytes()
    width, height = _dimensions(data, resolved)
    return {
        "path": str(resolved),
        "width": width,
        "height": height,
        "bytes": len(data),
    }


def _dimensions(data: bytes, path: Path) -> tuple[int, int]:
    if not data.startswith(SOI):
        raise _unreadable(path, "it does not start with a JPEG marker")

    at = 2
    while at + 3 < len(data):
        if data[at] != 0xFF:
            raise _unreadable(path, f"a segment at byte {at} does not begin with a marker")
        marker = data[at + 1]
        if marker == 0xFF:  # fill bytes are legal padding before the real marker
            at += 1
            continue
        if marker in STANDALONE:
            at += 2
            continue
        length = int.from_bytes(data[at + 2 : at + 4], "big")
        if marker in SOF_MARKERS:
            height = int.from_bytes(data[at + 5 : at + 7], "big")
            width = int.from_bytes(data[at + 7 : at + 9], "big")
            return width, height
        at += 2 + length

    raise _unreadable(path, "it carries no start-of-frame segment")


def _unreadable(path: Path, why: str) -> FrameGrabError:
    return FrameGrabError(
        cause=f"{path.name} is not a JPEG this server can read: {why}.",
        fix="Grab the frame again with refresh=true; the file in the cache is not a frame.",
        detail={"path": str(path)},
    )
