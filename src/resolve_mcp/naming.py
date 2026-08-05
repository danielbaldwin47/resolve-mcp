"""Names for the files the server writes.

Snapshots and spilled listings both land in the cache directory named after something the
director chose — a project, a bin — and both have to survive Windows filename rules and a
second write in the same session. One rule, one place.
"""

from __future__ import annotations

import re
from datetime import datetime

UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
STAMP_FORMAT = "%Y%m%d-%H%M%S"


def slug(label: str, fallback: str) -> str:
    """A filename-safe version of something the director named, or ``fallback``."""
    return UNSAFE_IN_FILENAME.sub("-", label).strip("-") or fallback


def timestamped_name(
    label: str,
    suffix: str,
    fallback: str,
    now: datetime | None = None,
) -> str:
    """``<slug>-<yyyymmdd-hhmmss><suffix>``, falling back when the label slugs to nothing."""
    stamp = (now or datetime.now()).strftime(STAMP_FORMAT)
    return f"{slug(label, fallback)}-{stamp}{suffix}"
