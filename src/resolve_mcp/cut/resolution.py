"""The optional delivery resolution: one reading of it for both the rules and the build.

A cut file that says nothing about resolution builds the way v1 always did — the timeline
inherits whatever the project creates timelines at, which on the corpus project is 4K while
every deliverable is 1920x1080. That gap is the whole device: the resolution the cut is
*for* was a hand step before every render (gauntlet G13), and a hand step that is skipped
delivers a 4K file nobody asked for and no return value mentions.

Stated on the timeline block, beside the frame rate it belongs with::

    "timeline": {"name": "sunset-set", "fps": 59.94,
                 "resolution": {"width": 1920, "height": 1080}}

Both sides are required together: a width with no height is a half-stated delivery, and
guessing the other from an aspect ratio the file never gave would be the server deciding.
Shape is judged as E1 in :mod:`resolve_mcp.cut.validate` — this module holds the reading
the rules and :mod:`resolve_mcp.resolve.build` share, so a cut cannot validate against one
reading and build against another.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

KEYS: Final[tuple[str, ...]] = ("width", "height")
"""Every field the block defines. Anything else is a typo, and E1 says so rather than
ignoring it — a misspelt side would silently keep the project default."""

MIN_SIDE: Final = 16
MAX_SIDE: Final = 16384
"""A typo guard, not a Resolve limit — the same reasoning as ``MAX_OVERLAY_TRACK``.

``"width": 19200`` is an author slip, and without a ceiling the build would set it, report
success, and hand back a render nothing can play. 16384 is past DCI 8K; a delivery that
genuinely needs more is a conversation, not a typo."""


@dataclass(frozen=True)
class Resolution:
    """What the built timeline is to be, in pixels."""

    width: int
    height: int

    def as_dict(self) -> dict[str, int]:
        return {"width": self.width, "height": self.height}


def read(doc: dict[str, Any]) -> Resolution | None:
    """The cut's stated resolution, or ``None`` for "whatever the project makes".

    Only ever called on a document the rules have already passed, so the fields are the
    integers E1 checked for; a malformed block reaches the build as an error, never here.
    """
    timeline = doc.get("timeline")
    if not isinstance(timeline, dict):
        return None
    stated = timeline.get("resolution")
    if not isinstance(stated, dict):
        return None
    return Resolution(width=int(stated["width"]), height=int(stated["height"]))


__all__ = ["KEYS", "MAX_SIDE", "MIN_SIDE", "Resolution", "read"]
