"""Names for the files the server writes.

Snapshots and spilled listings both land in the cache directory named after something the
director chose — a project, a bin — and both have to survive Windows filename rules and a
second write in the same session. One rule, one place.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
STAMP_FORMAT = "%Y%m%d-%H%M%S"


def timestamped_name(
    label: str,
    suffix: str,
    fallback: str,
    now: datetime | None = None,
) -> str:
    """``<slug>-<yyyymmdd-hhmmss><suffix>``, falling back when the label slugs to nothing."""
    stamp = (now or datetime.now()).strftime(STAMP_FORMAT)
    slug = UNSAFE_IN_FILENAME.sub("-", label).strip("-") or fallback
    return f"{slug}-{stamp}{suffix}"


def write_target(
    path: str | Path | None,
    label: str,
    suffix: str,
    default_dir: Path,
    fallback: str,
) -> Path:
    """Where a file the server writes goes, whether or not the caller named a path.

    A path the caller gave keeps its folder and stem but never a suffix that disagrees with
    what is about to be written: a ``.drp`` holding OTIO, or an ``.xml`` holding FCPXML,
    gets opened by the wrong thing weeks later. Without a path the file lands under the
    cache directory this kind of write owns, named for what it came from.

    Creating the directory is left to the caller: only the caller knows which failure the
    agent should be told about when it cannot be created.
    """
    if path is None:
        return default_dir / timestamped_name(label, suffix, fallback)
    target = Path(path)
    if target.suffix.lower() != suffix:
        target = target.with_suffix(suffix)
    return target
