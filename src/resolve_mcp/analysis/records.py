"""Writing analysis that an agent reads in slices.

A concert's beat grid is ten thousand records and its energy curve seven thousand — far past
what belongs in a tool result (#22: "timestamped LLM-friendly JSON with gist stats returned
inline"). So the file is ordinary JSON, and the layout is the affordance: the header is the
first few lines, and every record is exactly one line after it. ``head`` answers "what is
this", ``sed -n '400,420p'`` answers "what happens at bar 100", and neither pulls the whole
concert into the context window.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

INDENT = "  "


def write(
    path: Path,
    header: Mapping[str, Any],
    field: str,
    rows: Sequence[Mapping[str, Any]],
) -> Path:
    """Write ``header`` plus ``field: [rows]``, one record per line. Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "{",
        *(f"{INDENT}{json.dumps(key)}: {json.dumps(value)}," for key, value in header.items()),
        f"{INDENT}{json.dumps(field)}: [",
        *_rows(rows),
        f"{INDENT}]",
        "}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def read(path: Path) -> dict[str, Any]:
    """A file this module wrote, back as one dict — header keys and all of its records.

    For a worker reading another worker's half, not for a tool result: the layout above is
    what keeps an agent out of the whole file, and this is the one caller that wants it.
    """
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _rows(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each record on its own line, comma-separated the way JSON wants."""
    written = [json.dumps(dict(row)) for row in rows]
    return [line + "," for line in written[:-1]] + written[-1:]
