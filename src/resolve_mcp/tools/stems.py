"""The stem separation starter — the first of the typed heavy-compute tools.

Acquiring the audio is inside this call rather than a step the agent sequences (#12), so
one tool call covers "get me the drums for this timeline" end to end and the agent polls
one job for the whole of it.
"""

from __future__ import annotations

from typing import Any

from ..audio import stems
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def separate_stems(
    scope: str = "timeline",
    timeline: str | None = None,
    clip: str | None = None,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    refresh: bool = False,
) -> dict[str, Any]:
    """Split audio into stems on the GPU, in two passes, as a background job.

    Returns a job_id immediately — poll it with get_job. Pass one splits the mix into
    vocals, drums, bass and other; pass two decomposes the drum stem into kick, snare and
    toms, which is what fill detection reads. The result is paths on disk, not audio.

    scope=timeline exports and separates the timeline mix (the only route that captures
    Resolve's own summing of the tracks); scope=clip reads one media pool clip's file
    directly, which is faster but does not see any level or mapping the director set. Both
    are cached by content and parameters, so a rerun on unchanged media is instant; pass
    refresh=true when you changed something no reading can see, such as a clip's level.
    """
    return {
        "job": stems.separate_stems(
            get_connection(),
            scope=scope,
            timeline=timeline,
            clip=clip,
            bin=bin,
            refresh=refresh,
        )
    }


TOOLS: tuple[Any, ...] = (separate_stems,)

__all__ = ["TOOLS", "separate_stems"]
