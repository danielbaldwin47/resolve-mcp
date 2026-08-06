"""The cut file and media pool the build tests share.

One miniature concert — three shots over one continuous master mix — used by every test
that builds a cut and by every test that swaps a take on a built one. They are the same
fixture on purpose: a swap is only meaningful against a timeline this build made.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fakes import FakeMediaPoolItem, FakeTimeline, FakeTimelineItem, media_pool, studio


def a_cut(tmp_path: Path, doc: Any, name: str = "sunset-set.cut.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def valid_doc(**overrides: Any) -> dict[str, Any]:
    """Three shots over one continuous master mix — the concert substrate, in miniature."""
    doc: dict[str, Any] = {
        "schema": 1,
        "timeline": {"name": "sunset-set", "fps": 59.94},
        "sources": {
            "gtr_close": {"clip": "C0012.mp4", "bin": "Angles"},
            "keys_wide": {"clip": "C0031.mp4", "bin": "Angles"},
            "master_mix": {"clip": "sunset-master.wav"},
        },
        "audio": {"source": "master_mix", "in": 0, "out": 240},
        "segments": [
            {"id": "s001", "source": "gtr_close", "in": 1000, "out": 1100},
            {"id": "s002", "source": "keys_wide", "in": 4000, "out": 4080},
            {"id": "s003", "source": "gtr_close", "in": 2500, "out": 2560},
        ],
    }
    doc.update(overrides)
    return doc


SEGMENT_DURATIONS = (100, 80, 60)
TOTAL_FRAMES = sum(SEGMENT_DURATIONS)


def doc_with_alternates() -> dict[str, Any]:
    """The same cut with two of its three shots carrying an equal-duration angle choice.

    ``s002`` carries two alternates off *one* source: a second and third pass of the same
    tune from the same camera is an ordinary retake, so an alias is not a unique key into
    a selector — which is why swap_take counts indexes rather than naming a source.
    """
    doc = valid_doc()
    doc["segments"][0]["alternates"] = [{"source": "keys_wide", "in": 4500, "out": 4600}]
    doc["segments"][1]["alternates"] = [
        {"source": "gtr_close", "in": 5000, "out": 5080},
        {"source": "gtr_close", "in": 7000, "out": 7080},
    ]
    return doc


def a_pool(**clips: FakeMediaPoolItem) -> Any:
    """The pool :func:`valid_doc` builds against; ``clips`` swaps one out by alias."""
    angle = clips.get(
        "gtr_close",
        FakeMediaPoolItem(
            "C0012.mp4",
            file_path="D:/media/C0012.mp4",
            properties={"Frames": "20000", "Start": "0", "End": "19999"},
        ),
    )
    keys = clips.get(
        "keys_wide",
        FakeMediaPoolItem(
            "C0031.mp4",
            file_path="D:/media/C0031.mp4",
            properties={"Frames": "20000", "Start": "0", "End": "19999"},
        ),
    )
    master = clips.get(
        "master_mix",
        FakeMediaPoolItem(
            "sunset-master.wav",
            file_path="D:/media/sunset-master.wav",
            properties={"Type": "Audio", "FPS": "", "Frames": "600", "Start": "0", "End": "599"},
        ),
    )
    return media_pool({"Angles": [angle, keys], "": [master]})


def empty_project(pool: Any, **kwargs: Any) -> Any:
    """A project with a media pool and no timelines yet."""
    return studio(timeline=None, timelines=[], pool=pool, **kwargs)


def built(resolve: Any, name: str) -> FakeTimeline:
    """The timeline the build made, read back off the project."""
    project = resolve.current_project
    found = [
        project.GetTimelineByIndex(index)
        for index in range(1, project.GetTimelineCount() + 1)
        if project.GetTimelineByIndex(index) is not None
    ]
    match: FakeTimeline = next(timeline for timeline in found if timeline.GetName() == name)
    return match


def placements(
    timeline: FakeTimeline,
    track_type: str = "video",
    index: int = 1,
) -> list[tuple[str, int, int]]:
    return [
        (item.GetName(), item.GetStart(), item.GetDuration())
        for item in timeline.GetItemListInTrack(track_type, index) or []
    ]


def shots(timeline: FakeTimeline) -> list[FakeTimelineItem]:
    """The video track, item by item — where a segment's take selector lives."""
    items: list[FakeTimelineItem] = list(timeline.GetItemListInTrack("video", 1) or [])
    return items


def selector(item: FakeTimelineItem) -> list[tuple[str, int, int]]:
    """Every take in the item's selector, in order: which clip, and which source frames."""
    return [
        (take["mediaPoolItem"].GetName(), take["startFrame"], take["endFrame"])
        for take in (item.GetTakeByIndex(index) for index in range(1, item.GetTakesCount() + 1))
        if take is not None
    ]


__all__ = [
    "SEGMENT_DURATIONS",
    "TOTAL_FRAMES",
    "a_cut",
    "a_pool",
    "built",
    "doc_with_alternates",
    "empty_project",
    "placements",
    "selector",
    "shots",
    "valid_doc",
]
