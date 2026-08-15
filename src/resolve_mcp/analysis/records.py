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

from ..errors import InvalidRequestError

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
        *_lines(rows),
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


def rows(path: Path, field: str) -> tuple[dict[str, Any], ...]:
    """One file's ``field`` records, in time order — the shape ``write`` leaves, read back.

    The strong reader, and the reason it lives beside the writer: what a record file has to
    be is this module's invariant, so every caller that reads one gets the same answer to a
    file that is not one. Four refusals — unreadable, not JSON, holding no such field, and
    holding no record with a time — cover the ways a path an agent typed can be the wrong
    path, and the ``"t"`` filter with the sort is what makes the rest of them a timeline
    rather than a list (#222).
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRequestError(
            cause=f"Could not read {path.name} as analysis JSON: {exc}.",
            fix="Pass the path an analysis job returned, unedited.",
            detail={"file": str(path), "field": field},
        ) from exc

    held = doc.get(field) if isinstance(doc, Mapping) else None
    if not isinstance(held, list):
        raise InvalidRequestError(
            cause=f"{path.name} holds no {field!r} records.",
            fix=f"That file is not the {field} analysis; pass the one whose kind is {field!r}.",
            detail={"file": str(path), "field": field},
        )
    found = [
        dict(row)
        for row in held
        if isinstance(row, Mapping) and isinstance(row.get("t"), int | float)
    ]
    if not found:
        # A file that was named but says nothing must not read like a file nobody named:
        # both would leave the column null, and only one of them is what the caller meant.
        raise InvalidRequestError(
            cause=f"{path.name} holds no {field} record with a time in it.",
            fix=(
                f"Pass the {field} file a finished analysis job wrote — its records each "
                'carry a "t" in seconds. An analysis that found nothing is worth rerunning '
                "rather than measuring against."
            ),
            detail={"file": str(path), "field": field},
        )
    return tuple(sorted(found, key=lambda row: float(row["t"])))


def _lines(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each record on its own line, comma-separated the way JSON wants."""
    written = [json.dumps(dict(row)) for row in rows]
    return [line + "," for line in written[:-1]] + written[-1:]
