"""Deliver tools — turning a finished cut into files, one preset and one span at a time."""

from __future__ import annotations

from typing import Any

from .. import deliver
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def list_render_presets() -> dict[str, Any]:
    """List the render presets this project offers, spelled the way render_timeline needs.

    Presets belong to the project and the machine, not to this server: what a preset renders
    — format, codec, resolution, bit rate — was decided in the Deliver page and saved there,
    so pick one by name rather than describing a shape. current is the format and codec the
    project would render with right now, which is the last preset anything loaded.
    """
    connection = get_connection()
    return deliver.list_presets(connection)


@tool
def render_timeline(
    preset: str | None = None,
    timeline: str | None = None,
    name: str | None = None,
    target_dir: str | None = None,
    start: Any = None,
    end: Any = None,
    refresh: bool = False,
) -> dict[str, Any]:
    """Render a timeline, or a span of one, as a background job. Returns a job to poll.

    A render is the longest thing this server does, so it hands back a job straight away and
    get_job reports it — progress while it runs, the file's path and size when it finishes,
    a cause and a fix if the queue refused it. A preset decides everything about the file
    except where it goes and which frames it covers.

    Leave preset out and the server's configured default is used, which is the normal way to
    render: the result says which preset ran and whether it was the default or explicit. Name
    one — spelled as list_render_presets spells it — only to override that for this render.
    Either way an unknown name is refused with the list of names that exist; nothing falls
    back to another preset, because a wrong-shaped file under a right-sounding name is worse
    than a refusal you can act on.

    start and end cut one deliverable out of a longer timeline — a song off a concert set.
    They are frames on the timeline's own clock, the numbers inspect_timeline and
    list_markers report (a timeline starting at 01:00:00:00 starts at frame 86400, not 0),
    or seconds with an explicit snap. The range is half-open [start, end) like every other
    range here; one bound alone runs to the timeline's own edge, and neither is the whole
    timeline.

    Without target_dir the file lands in the server's render directory, which it replaces
    freely on a re-render — that is the frictionless path after a review round. A target_dir
    you name is yours: a file already sitting there is refused rather than overwritten,
    until you pass refresh=true. name is the file's own name (defaulting to the timeline's,
    plus the range) and is what the director will see, so name it for the song.

    An unchanged cut rendered twice comes back from cache without rendering again; refresh
    forces the render, which is also how a change no reading can see (a clip's audio level)
    gets picked up. The result says what the file covers either way — a whole-timeline
    render reports the timeline's own bounds, with whole_timeline true.

    A render leaves the Deliver page set to the preset it used, the same way rendering by
    hand would; the timeline the director had open is the one thing put back.
    """
    connection = get_connection()
    return {
        "job": deliver.render_timeline(
            connection,
            preset=preset,
            timeline=timeline,
            name=name,
            target_dir=target_dir,
            start=start,
            end=end,
            refresh=refresh,
        )
    }


TOOLS: tuple[Any, ...] = (list_render_presets, render_timeline)

__all__ = ["TOOLS", "list_render_presets", "render_timeline"]
