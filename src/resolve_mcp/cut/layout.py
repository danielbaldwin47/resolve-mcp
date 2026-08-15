"""The cut layout: where every entry in a cut document lands, answered once.

A cut file names no absolute frame. A segment's position is the sum of everything
before it, an overlay's is its anchor's position plus an offset, and the cut's length
is the sum of the lot — all *computed*, so a tightening pass moves the numbers instead
of the author maintaining them. This module is the only place those sums are taken.

That single place is the point. The rules in :mod:`resolve_mcp.cut.validate` judge
these numbers, :mod:`resolve_mcp.resolve.build` places against them, and
``virtual_transcript`` reads the cut back through them: a second derivation of "where
does segment N start" is a shot validated at one frame and built at another. The
deletion test says as much — remove this and build and virtual each grow their own copy
of the sum, which is precisely the drift it exists to prevent.

Pure by construction: documents in, positions out. No findings, no clip facts, no
Resolve handle — so the rules can import it without circularity and every consumer gets
the same answer from the same code.

The document is assumed shape-valid (E1 has passed). Reading an unvalidated document
here raises rather than reporting, because reporting is the rules module's job.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Final, TypeGuard

from ..timing import duration_frames

V1_TRACK: Final = 1
"""The sequential cut's own track — the one ``segments`` lays out."""

FIRST_OVERLAY_TRACK: Final = 2
"""V1 is the sequential cut's own track, so the lowest an overlay can claim is V2."""


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def is_gap(entry: Any) -> bool:
    """Whether a ``segments`` entry is black rather than a shot.

    Public because the build, the swap and the read-back all walk the same array and each
    has to answer this the same way: a second definition of what black looks like is how a
    gap ends up placed on one side of the seam and ignored on the other.
    """
    return isinstance(entry, dict) and "gap" in entry


def entry_duration(entry: dict[str, Any]) -> int:
    """How much record time one ``segments`` entry takes — a gap's is the black itself."""
    if is_gap(entry):
        return int(entry["gap"])
    return duration_frames(entry["in"], entry["out"])


def overlay_track(overlay: dict[str, Any]) -> int:
    """Which video track an overlay rides on. Absent means V2, the layer above the cut."""
    track = overlay.get("track")
    return int(track) if _is_int(track) else FIRST_OVERLAY_TRACK


def entries(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """``segments`` as authored: picture and black, in the order that lays out the V1."""
    laid_out: list[dict[str, Any]] = doc["segments"]
    return laid_out


def shots(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the entries that place a clip — *not* the same list as ``doc["segments"]``.

    Every rule about *media* — aliases, bounds, rates, takes — reads this rather than the
    raw array, so a gap is skipped by all of them at once instead of by a guard each of
    them could be written without. Public alongside :func:`gaps` because the build and the
    summaries need the same two counts, and counting them by hand at each call site is how
    one of them ends up disagreeing about what black is.
    """
    return [entry for entry in entries(doc) if not is_gap(entry)]


def gaps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Only the entries that are black."""
    return [entry for entry in entries(doc) if is_gap(entry)]


def overlays(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``overlays`` array, or an empty one — the block is optional."""
    riding: list[dict[str, Any]] = doc.get("overlays") or []
    return riding


def positions(doc: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Each ``segments`` id to its computed ``(start, duration)`` — sequential V1, in order.

    The overlay rules and the build both place against these numbers, and they have to be
    the same numbers: an offset validated against one layout and built against another
    would put the overlay somewhere nobody checked.

    A gap is here with the rest. It places no clip, but it occupies record time and so it
    moves everything after it — and an overlay may anchor over it, which is what makes a
    V2 bridge across black expressible at all. Positions stay *computed* either way: black
    is a duration in the array, never a frame number an author had to keep up to date.
    """
    placed: dict[str, tuple[int, int]] = {}
    at = 0
    for entry in entries(doc):
        duration = entry_duration(entry)
        placed[str(entry["id"])] = (at, duration)
        at += duration
    return placed


def placements(doc: dict[str, Any], start: int) -> dict[str, tuple[int, int]]:
    """Each segment id to its ``(record frame, duration)`` on a timeline starting at ``start``.

    The one place a cut's own offsets become absolute frames. A build sends these as
    ``recordFrame`` and a swap finds a shot by them, so a second derivation of the same sum
    somewhere else is a swap that quietly reads the wrong shot — there is only this one.
    """
    return {
        id: (start + offset, duration) for id, (offset, duration) in positions(doc).items()
    }


def total_frames(doc: dict[str, Any]) -> int:
    """The V1 span: sequential entries, so the sum of their durations, black included."""
    return sum(entry_duration(entry) for entry in entries(doc))


def overlay_positions(doc: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Each overlay id to its computed ``(start, duration)`` on V2 — start measured from
    the cut's first frame, exactly as :func:`positions` measures a segment's.

    An overlay names no absolute frame: its position is its anchor segment's computed
    start plus the offset, which is what makes it ride the content it covers through a
    tightening pass. E9 judges these numbers and the build places against them, from this
    one function — a position validated one way and built another is exactly the drift
    anchoring exists to prevent. An overlay whose anchor does not exist has no position
    and is absent here; E9 has already refused that document.
    """
    return {str(overlay["id"]): at for overlay, at in anchored(doc) if at is not None}


def anchored(doc: dict[str, Any]) -> Iterator[tuple[dict[str, Any], tuple[int, int] | None]]:
    """Every overlay with its resolved span, or ``None`` when its anchor is not a segment.

    Public for E9's sake alone: :func:`overlay_positions` drops the unanchored ones, and
    the rule that *reports* them needs to see the overlay that has no position rather than
    infer it from an absence.
    """
    placed = positions(doc)
    for overlay in overlays(doc):
        anchor = placed.get(str(overlay["over"]["segment"]))
        yield (
            overlay,
            None
            if anchor is None
            else (
                anchor[0] + int(overlay["over"]["offset"]),
                duration_frames(overlay["in"], overlay["out"]),
            ),
        )


__all__ = [
    "FIRST_OVERLAY_TRACK",
    "V1_TRACK",
    "anchored",
    "entries",
    "entry_duration",
    "gaps",
    "is_gap",
    "overlay_positions",
    "overlay_track",
    "overlays",
    "placements",
    "positions",
    "shots",
    "total_frames",
]
