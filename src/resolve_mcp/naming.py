"""Names for the files the server writes, and for the timelines it materializes.

Snapshots and spilled listings both land in the cache directory named after something the
director chose — a project, a bin — and both have to survive Windows filename rules and a
second write in the same session. One rule, one place.

Built timelines are named ``<base> v<N>``: every build makes a new version and no build
ever touches an old one, so which number is next is a question about names that already
exist. The scan lives here with the other naming rules.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

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


def version_pattern(base: str) -> re.Pattern[str]:
    """Matches exactly ``<base> v<N>`` — a name that merely starts with the base is not one."""
    return re.compile(rf"^{re.escape(base)} v(\d+)$")


def version_number(base: str, name: str) -> int | None:
    """The version ``name`` carries for ``base``, or ``None`` if it is not a version of it."""
    found = version_pattern(base).match(name)
    return int(found.group(1)) if found else None


def latest_version(base: str, existing: Iterable[str]) -> int:
    """The highest version already built for ``base``; ``0`` when there is none.

    Max rather than count: a deleted v2 must not hand v3's number out a second time.
    """
    numbers = [
        found
        for found in (version_number(base, name) for name in existing)
        if found is not None
    ]
    return max(numbers, default=0)


def next_version_name(base: str, existing: Iterable[str]) -> str:
    """The name the next build takes: ``<base> v<latest + 1>``."""
    return f"{base} v{latest_version(base, existing) + 1}"
