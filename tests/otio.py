"""Hand-editing an exported OTIO document — the route around the no-transitions wall.

The scripting API cannot cut a dissolve, so a transition is made by editing one into an
exported OTIO document and importing that back. The spec calls that edit hand-made and the
tool catalog has no injection tool, so this lives in the test support rather than in the
server: the live tier uses it to take the whole round trip in one command, and the fake
tier checks the document it produces without Resolve.

An OTIO transition is not an item with a duration of its own — it sits *between* two items
in a track's children and reaches ``in_offset`` frames back into the one before and
``out_offset`` frames into the one after. Both neighbours therefore have to be long enough
to give those frames up, which is what the length checks here are for.
"""

from __future__ import annotations

from typing import Any

Document = dict[str, Any]
Track = dict[str, Any]
Item = dict[str, Any]

DISSOLVE = "SMPTE_Dissolve"
DEFAULT_RATE = 24.0


def video_tracks(document: Document) -> list[Track]:
    """The document's video tracks, in order — where a cut's transitions belong."""
    stack = document.get("tracks") or {}
    children = stack.get("children") or []
    return [track for track in children if track.get("kind") == "Video"]


def children_of(track: Track) -> list[Item]:
    return list(track.get("children") or [])


def is_clip(item: Item) -> bool:
    return str(item.get("OTIO_SCHEMA", "")).startswith("Clip.")


def is_gap(item: Item) -> bool:
    return str(item.get("OTIO_SCHEMA", "")).startswith("Gap.")


def is_transition(item: Item) -> bool:
    return str(item.get("OTIO_SCHEMA", "")).startswith("Transition.")


def inject_dissolve(track: Track, frames: int = 6) -> bool:
    """Put a dissolve on the first cut between two clips that can carry it.

    Returns whether one was injected — a track of one clip, or of clips too short to hand
    over ``frames`` on each side, is left alone rather than given a transition that reaches
    past the media it dissolves.
    """
    children = children_of(track)
    for index in range(len(children) - 1):
        before, after = children[index], children[index + 1]
        if not (is_clip(before) and is_clip(after)):
            continue
        if _frames(before) <= frames or _frames(after) <= frames:
            continue
        children.insert(index + 1, _transition("Cross Dissolve", _rate(before), frames))
        track["children"] = children
        return True
    return False


def inject_fade_to_black(track: Track, frames: int = 6) -> bool:
    """Put a transition at a clip↔gap boundary — the spec's open verification note.

    A fade to black is a dissolve into nothing, so the boundary needs a gap on one side. An
    existing clip→gap boundary is used where there is one; otherwise a gap is appended
    after the last clip, which is where a fade to black belongs anyway.
    """
    children = children_of(track)
    for index in range(len(children) - 1):
        before, after = children[index], children[index + 1]
        if is_clip(before) and is_gap(after) and _frames(before) > frames:
            children.insert(index + 1, _transition("Fade to Black", _rate(before), frames))
            track["children"] = children
            return True

    last = children[-1] if children else None
    if last is None or not is_clip(last) or _frames(last) <= frames:
        return False
    rate = _rate(last)
    children.append(_transition("Fade to Black", rate, frames))
    children.append(_gap(rate, frames * 4))
    track["children"] = children
    return True


def _transition(name: str, rate: float, frames: int) -> Item:
    return {
        "OTIO_SCHEMA": "Transition.1",
        "name": name,
        "metadata": {},
        "transition_type": DISSOLVE,
        "in_offset": _rational(rate, frames),
        "out_offset": _rational(rate, frames),
    }


def _gap(rate: float, frames: int) -> Item:
    return {
        "OTIO_SCHEMA": "Gap.1",
        "name": "gap",
        "metadata": {},
        "effects": [],
        "markers": [],
        "source_range": {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": _rational(rate, 0),
            "duration": _rational(rate, frames),
        },
    }


def _rational(rate: float, value: int) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": value}


def _rate(item: Item) -> float:
    duration = ((item.get("source_range") or {}).get("duration")) or {}
    try:
        return float(duration.get("rate") or DEFAULT_RATE)
    except (TypeError, ValueError):
        return DEFAULT_RATE


def _frames(item: Item) -> int:
    duration = ((item.get("source_range") or {}).get("duration")) or {}
    try:
        return int(float(duration.get("value") or 0))
    except (TypeError, ValueError):
        return 0
