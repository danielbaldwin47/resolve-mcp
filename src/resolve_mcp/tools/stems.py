"""The stem separation starter — the first of the typed heavy-compute tools.

Acquiring the audio is inside this call rather than a step the agent sequences (#12), so
one tool call covers "get me the drums for this timeline" end to end and the agent polls
one job for the whole of it.
"""

from __future__ import annotations

from typing import Any

from ..audio import acquire, stems
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def separate_stems(
    scope: str = acquire.TIMELINE_SCOPE,
    timeline: str | None = None,
    clip: str | None = None,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    refresh: bool = False,
    split_wind: bool = False,
    detach: bool = True,
) -> dict[str, Any]:
    """Split audio into stems on the GPU, in two passes or three, as a background job.

    Returns a job_id immediately — poll it with get_job. Pass one splits the mix into
    vocals, drums, bass and other; pass two decomposes the drum stem into kick, snare and
    toms, which is what fill detection reads. The result is paths on disk, not audio.

    split_wind=true adds a third pass over the "other" stem, returned under "other" as
    "wind" (horns and reeds) and "comp" (everything else that landed in "other" — piano,
    guitar, vibes, percussion, and the bass line itself on a recording whose bass stem came
    back near-silent; it is not a piano stem). Ask for it when a horn or reed is what you
    need to hear apart from the piano, and leave it off otherwise: on a band with no piano
    "other" is already the winds, and the pass costs time to recover nothing. Turning it on
    for audio already separated re-runs the earlier passes too.

    The separation runs in a process of its own and keeps running if this server exits, so a
    half-hour pass on a full set is not lost with the session that started it; get_job reads
    the same either way. Pass detach=false to keep it on a thread here — only useful when you
    want the job to die with the server. The export that comes first cannot be detached (it
    drives Resolve), so a server that dies during that part still loses the job.

    scope=timeline exports and separates the timeline mix (the only route that captures
    Resolve's own summing of the tracks); scope=clip reads one media pool clip's file
    directly, which is faster but does not see any level or mapping the director set. Both
    are cached by content and parameters, so a rerun on unchanged media is instant; pass
    refresh=true when you changed something no reading can see, such as a clip's level.
    Asking again while a separation of the same audio is still running is safe: the second
    job waits for the first and returns the stems it wrote rather than failing or separating
    the same audio twice, so it reports "waiting" for as long as the first one takes.
    """
    return {
        "job": stems.separate_stems(
            get_connection(),
            scope=scope,
            timeline=timeline,
            clip=clip,
            bin=bin,
            refresh=refresh,
            split_wind=split_wind,
            detach=detach,
        )
    }


TOOLS: tuple[Any, ...] = (separate_stems,)

__all__ = ["TOOLS", "separate_stems"]
