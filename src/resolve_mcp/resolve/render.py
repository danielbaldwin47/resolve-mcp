"""Driving Resolve's render queue: add a job, start it, watch it, take it back off.

The queue is a global, stateful part of the application — settings are set on the project,
not passed to a call — so three things here are decisions rather than API calls:

* **The queue is left as it was found.** Every job this server adds is deleted once it has
  finished, whatever the outcome. The director's own queue is theirs; an export for
  analysis must not silently accumulate entries in it.

* **Status is polled, never assumed.** ``StartRendering`` returns as soon as the job is
  accepted. The only truthful completion signal is the job's own status going to Complete,
  and a Failed or Cancelled status is a failure even though every call returned True.

* **A completed render still has to have written the file.** Resolve reports success for
  renders that land nothing on disk (an unwritable target directory does this). The caller
  passes the path it expects, and its absence is a failure, not a cache entry.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..errors import RenderQueueError
from ..logging_config import get_logger

log = get_logger("render")

Project = Any

COMPLETE = "Complete"
FAILED = ("Failed", "Cancelled")
POLL_SECONDS = 1.0
RENDER_TIMEOUT = 3600.0

STATUS = "JobStatus"
PERCENT = "CompletionPercentage"


def submit(project: Project, settings: dict[str, Any], format_: str, codec: str) -> str:
    """Push one job onto the queue and return its id.

    ``SetRenderSettings`` and the format/codec pair are project-level state; they are set
    immediately before the job is added so that the job captures them.
    """
    if not project.SetCurrentRenderFormatAndCodec(format_, codec):
        raise RenderQueueError(
            cause=f"Resolve would not render {format_}/{codec}.",
            detail={"format": format_, "codec": codec},
        )
    if not project.SetRenderSettings(settings):
        raise RenderQueueError(
            cause="Resolve refused the render settings.",
            detail={"settings": settings},
        )
    job_id = project.AddRenderJob()
    if not job_id:
        raise RenderQueueError(cause="Resolve would not add the job to the render queue.")
    log.info("Queued render job %s", job_id)
    return str(job_id)


def render(
    project: Project,
    job_id: str,
    expecting: Path,
    progress: Callable[[float, str], None] | None = None,
    poll: float = POLL_SECONDS,
    timeout: float = RENDER_TIMEOUT,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Path:
    """Start the job, watch it to completion, and hand back the file it wrote.

    ``now`` and ``sleep`` are parameters so the polling loop is testable without a test
    that actually waits.
    """
    if not project.StartRendering(job_id):
        _remove(project, job_id)
        raise RenderQueueError(
            cause="Resolve would not start the render.",
            detail={"render_job_id": job_id},
        )
    try:
        _watch(project, job_id, progress, poll, timeout, now, sleep)
    finally:
        _remove(project, job_id)

    if not expecting.exists():
        raise RenderQueueError(
            cause=f"The render reported success but wrote nothing to {expecting}.",
            detail={"expected": str(expecting)},
        )
    log.info("Render job %s wrote %s", job_id, expecting)
    return expecting


def _watch(
    project: Project,
    job_id: str,
    progress: Callable[[float, str], None] | None,
    poll: float,
    timeout: float,
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    started = now()
    while True:
        reading = project.GetRenderJobStatus(job_id) or {}
        status = str(reading.get(STATUS, ""))
        percent = _percent(reading)
        if progress is not None:
            progress(percent, f"rendering ({int(percent * 100)}%)")
        if status == COMPLETE:
            return
        if status in FAILED:
            raise RenderQueueError(
                cause=f"The render job ended {status}.",
                detail={"render_job_id": job_id, "status": reading},
            )
        if now() - started > timeout:
            raise RenderQueueError(
                cause=f"The render job was still {status or 'unreported'} after {timeout:.0f}s.",
                fix=(
                    "Check the Deliver page — a modal dialog in the Resolve GUI stalls the "
                    "queue. Cancel the job there, then retry."
                ),
                detail={"render_job_id": job_id, "timeout_seconds": timeout},
            )
        sleep(poll)


def _percent(reading: dict[str, Any]) -> float:
    try:
        return min(max(float(reading.get(PERCENT, 0)) / 100.0, 0.0), 1.0)
    except (TypeError, ValueError):
        return 0.0


def _remove(project: Project, job_id: str) -> None:
    """Take the job back off the queue. Failing to is not worth failing the render over."""
    try:
        project.DeleteRenderJob(job_id)
    except Exception:  # noqa: BLE001 - a queue we could not tidy is not a lost render
        log.warning("Could not remove render job %s from the queue", job_id, exc_info=True)
