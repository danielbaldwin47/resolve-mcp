"""OTIO documents, so the round trip a tail is built on is a real round trip in the fakes.

``Timeline.Export`` and ``MediaPool.ImportTimelineFromFile`` are the only pair of Resolve
calls this server uses to *change* a cut rather than read one, and the tail device is built
entirely out of what happens between them. A fake export that wrote a placeholder string
would let every tail test pass against a document nobody could have imported — so the
export here writes the shape Resolve writes, and the import reads it back.

Two things are modelled from the live probe rather than from the OTIO spec, because they
are what the build has to survive:

* A transition is a child of a track with no duration of its own. It reaches ``in_offset``
  frames back into the item before it, which is why the import below skips transitions when
  it rebuilds items and keeps them on the timeline instead.
* Resolve *renames* every transition it accepts — ``Fade to Black`` comes back as
  ``Cross Dissolve`` on video and ``Cross Fade 0 dB`` on audio (verified live, 21.0.3). The
  import does the same, so a test that asserted on the name it wrote would fail here
  exactly as it would fail on the machine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .timeline import FakeTimeline, FakeTrack
from .timeline_item import FakeTimelineItem

Document = dict[str, Any]

RENAMED: dict[str, str] = {"Video": "Cross Dissolve", "Audio": "Cross Fade 0 dB"}
"""What Resolve calls a transition it took, by track kind."""


def is_otio(path: str) -> bool:
    return Path(path).suffix.lower() == ".otio"


def document_of(timeline: FakeTimeline) -> Document:
    """One timeline as the OTIO document Resolve would export for it.

    Every track is padded with a trailing gap out to the length of the longest, which is
    what Resolve does (verified live, 21.0.3) and is the ordinary shape of a concert cut:
    the mix outlives the picture, so V1 exports ending in black rather than in a shot. A
    fake that left tracks ragged would let a tail land after the pad and never say so.
    """
    rate = _rate(timeline)
    children: list[dict[str, Any]] = []
    for kind, label in (("video", "Video"), ("audio", "Audio")):
        for index, track in enumerate(timeline.tracks_of(kind), start=1):
            children.append(
                {
                    "OTIO_SCHEMA": "Track.1",
                    "name": track.name or f"{label} {index}",
                    "kind": label,
                    "children": [_clip(item, rate) for item in track.items],
                }
            )
    longest = max((sum(_frames(item) for item in one["children"]) for one in children), default=0)
    for one in children:
        short = longest - sum(_frames(item) for item in one["children"])
        if short > 0 and one["children"]:
            one["children"].append(_gap(rate, short))
    # Transitions go back out where they came in — after the track's last clip. An export
    # that dropped them would make the read-back the server does after an import always
    # report a lost tail.
    for one in children:
        for carried in timeline.transitions:
            if carried["track"] == one["name"]:
                one["children"].insert(
                    _after_last_clip(one["children"]),
                    _transition(rate, int(carried["in_offset"]), str(carried["name"])),
                )
    return {
        "OTIO_SCHEMA": "Timeline.1",
        "name": timeline.GetName(),
        "global_start_time": _rational(rate, timeline.GetStartFrame()),
        "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": children},
    }


def timeline_from(document: Document, name: str, keeps_transitions: bool = True) -> FakeTimeline:
    """The timeline Resolve would materialise from ``document``, transitions and all.

    ``keeps_transitions=False`` models the import that takes the document, answers with a
    timeline, and quietly leaves the transitions out — the one outcome nothing downstream
    can see, since a cut whose dissolve never landed and a cut that never asked for one are
    the same timeline.
    """
    tracks: dict[str, list[FakeTrack]] = {"video": [], "audio": []}
    transitions: list[dict[str, Any]] = []
    rate = 24.0
    for track in (document.get("tracks") or {}).get("children") or []:
        kind = str(track.get("kind") or "Video")
        items: list[FakeTimelineItem] = []
        start = int(float((document.get("global_start_time") or {}).get("value") or 0))
        for child in track.get("children") or []:
            schema = str(child.get("OTIO_SCHEMA", ""))
            if schema.startswith("Transition."):
                if keeps_transitions:
                    transitions.append(
                        {
                            "track": str(track.get("name") or ""),
                            "kind": kind,
                            "name": RENAMED.get(kind, "Cross Dissolve"),
                            "in_offset": int(
                                float((child.get("in_offset") or {}).get("value") or 0)
                            ),
                        }
                    )
                continue
            frames = _frames(child)
            rate = _rate_of(child) or rate
            if schema.startswith("Clip."):
                items.append(
                    FakeTimelineItem(
                        str(child.get("name") or "clip"),
                        start,
                        frames,
                        source_start=_source_start(child),
                    )
                )
            start += frames
        tracks["video" if kind == "Video" else "audio"].append(
            FakeTrack(str(track.get("name") or kind), items)
        )
    imported = FakeTimeline(
        name,
        fps=str(rate),
        start_frame=int(float((document.get("global_start_time") or {}).get("value") or 0)),
        video=tracks["video"],
        audio=tracks["audio"],
    )
    imported.transitions = transitions
    return imported


def read_document(path: str) -> Document | None:
    """The OTIO document at ``path``, or ``None`` when that is not what is there."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) and "tracks" in document else None


def _clip(item: FakeTimelineItem, rate: float) -> dict[str, Any]:
    source_start = item.GetSourceStartFrame()
    return {
        "OTIO_SCHEMA": "Clip.2",
        "name": item.GetName(),
        "source_range": {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": _rational(rate, int(source_start or 0)),
            "duration": _rational(rate, item.GetDuration()),
        },
    }


def _after_last_clip(children: list[dict[str, Any]]) -> int:
    for index in range(len(children) - 1, -1, -1):
        if str(children[index].get("OTIO_SCHEMA", "")).startswith("Clip."):
            return index + 1
    return len(children)


def _transition(rate: float, frames: int, name: str) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Transition.1",
        "name": name,
        "transition_type": "SMPTE_Dissolve",
        "in_offset": _rational(rate, frames),
        "out_offset": _rational(rate, 0),
    }


def _gap(rate: float, frames: int) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Gap.1",
        "name": "",
        "source_range": {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": _rational(rate, 0),
            "duration": _rational(rate, frames),
        },
    }


def _rational(rate: float, value: int) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": value}


def _rate(timeline: FakeTimeline) -> float:
    try:
        return float(timeline.GetSetting("timelineFrameRate") or 24.0)
    except (TypeError, ValueError):
        return 24.0


def _duration(item: dict[str, Any]) -> dict[str, Any]:
    duration: dict[str, Any] = ((item.get("source_range") or {}).get("duration")) or {}
    return duration


def _frames(item: dict[str, Any]) -> int:
    try:
        return int(float(_duration(item).get("value") or 0))
    except (TypeError, ValueError):
        return 0


def _rate_of(item: dict[str, Any]) -> float:
    try:
        return float(_duration(item).get("rate") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _source_start(item: dict[str, Any]) -> int:
    start: dict[str, Any] = ((item.get("source_range") or {}).get("start_time")) or {}
    try:
        return int(float(start.get("value") or 0))
    except (TypeError, ValueError):
        return 0
