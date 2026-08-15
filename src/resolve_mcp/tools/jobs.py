"""The two tools every heavy-compute starter shares: poll one job, or list them all.

There is no start_job tool. Each heavy tool is its own typed starter (#12: "typed starters
+ one poller"), and audio acquisition is internal to those starters rather than a step the
agent sequences by hand — so what belongs here is only the reading side.
"""

from __future__ import annotations

from typing import Any

from ..jobs import store
from ..spill import capped
from .envelope import offline_tool

DEFAULT_JOB_LIMIT = 50


@offline_tool
def get_job(job_id: str) -> dict[str, Any]:
    """Poll one job: running with progress, completed with its result, or failed with a fix.

    progress is 0.0-1.0 and step says what the worker is doing now. A completed job carries
    result — for anything curve-shaped or long that is a path on disk plus gist stats, so
    read or grep the file rather than asking for it inline. A failed job carries the same
    cause/fix shape as any other failure; cached is true when the result came back without
    the work being redone.
    """
    return store.load(job_id).payload()


@offline_tool
def list_jobs(state: str | None = None, limit: int = DEFAULT_JOB_LIMIT) -> dict[str, Any]:
    """List jobs newest first — how a restarted session picks up what it started.

    state filters to running, completed or failed. Jobs survive a server restart because
    their records live in the cache directory; one that was still running when the server
    went down comes back failed with code job_interrupted, which means start it again —
    finished work is already in the result cache and is not paid for twice. count is how
    many there are, not how many came back: past limit the reply is capped and the whole
    listing spills to disk (spilled_to).
    """
    found = [one.payload() for one in store.load_all(state=state)]
    return capped(
        {"count": len(found)},
        key="jobs",
        whole=found,
        limit=limit,
        label="jobs",
        fallback="jobs",
    )


TOOLS: tuple[Any, ...] = (
    get_job,
    list_jobs,
)

__all__ = ["TOOLS", "get_job", "list_jobs"]
