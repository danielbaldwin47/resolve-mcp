"""Composed scenarios: a wired-up ``studio()``, a reference sync, a timeline with a mix."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .connection import FakeResolve
from .project import FakeProject
from .timeline import FakeTimeline, FakeTrack
from .timeline_item import FakeTimelineItem

if TYPE_CHECKING:
    from .pool import FakeMediaPool


def sync_reference(
    name: str = "sunset-set sync",
    fps: str = "59.94",
    angles: dict[str, tuple[int, int, int]] | None = None,
    start_frame: int = 0,
) -> FakeTimeline:
    """The director's stacked layout: one angle per video track, each landing on its own frame.

    ``angles`` maps a track name to ``(record_start, source_start, duration)``. That is the
    shape a hand-synced reference has, and the per-angle sync offset is the difference
    between the two starts.
    """
    angles = angles or {"Cam A": (0, 1000, 500), "Cam B": (120, 3000, 400)}
    video = [
        FakeTrack(
            track,
            [FakeTimelineItem(f"{track}.mp4", record, duration, source_start=source)],
        )
        for track, (record, source, duration) in angles.items()
    ]
    return FakeTimeline(name, fps, start_frame=start_frame, video=video)


def with_a_mix(timeline: FakeTimeline) -> FakeTimeline:
    """Put a board recording on the timeline's first audio track, and hand it back.

    A ``FakeTimeline`` is built with no tracks at all, which is the one timeline Resolve
    will not export: an audio-only render of a cut with nothing on its audio tracks is
    queued and then never run (#88). Any fake standing in for a timeline whose mix comes
    off the render queue therefore has to carry audio, or the test is exercising the
    refusal rather than the export it means to.

    Kept as a wrapper rather than a default on the fake, because a timeline that carries
    audio is not the default a *Resolve* timeline has, and the tests that assert on track
    counts read the fake's tracks as given.
    """
    item = FakeTimelineItem("Board mix.wav", timeline._start_frame, 500)
    if timeline._owner is not None:
        item.adopt(timeline._owner)
    timeline._tracks["audio"].append(FakeTrack("Audio 1", [item]))
    return timeline


def studio(
    project: str | None = "sunset-set",
    timeline: str | FakeTimeline | None = "sunset-set v3",
    fps: str = "59.94",
    extra_projects: tuple[str, ...] = ("holiday-gig",),
    pool: FakeMediaPool | None = None,
    timelines: list[FakeTimeline | None] | None = None,
    export_types: Sequence[str] | None = None,
) -> FakeResolve:
    """A conventional fake: Studio running, one project open, one timeline current.

    ``timeline`` is the current one, by name or as a built ``FakeTimeline``; ``timelines``
    is everything the project holds, defaulting to the current one alone. Passing
    ``timelines`` makes the first of them current, unless ``timeline`` says otherwise —
    ``None`` for a project whose timelines are all closed.
    """
    if isinstance(timeline, FakeTimeline) or timeline is None:
        current = timeline
    elif timelines is not None:
        current = timelines[0] if timelines else None
    else:
        current = FakeTimeline(timeline, fps)
    projects: dict[str, FakeProject] = {}
    for name in extra_projects:
        projects[name] = FakeProject(name, fps=fps)
    if project is not None:
        projects[project] = FakeProject(
            project,
            current,
            fps=fps,
            media_pool=pool,
            timelines=timelines,
        )
    resolve = FakeResolve(projects, current=project, export_types=export_types)
    if pool is not None:
        pool.adopt(resolve)
    owned = [one for one in (timelines or []) if one is not None]
    if current is not None and current not in owned:
        owned.append(current)
    for one in owned:
        one.adopt(resolve)
    return resolve
