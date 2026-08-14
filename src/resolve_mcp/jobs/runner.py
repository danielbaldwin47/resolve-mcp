"""Starting heavy work without stalling stdio.

The rule the whole server hangs off: a tool call returns in the time one Resolve API call
takes, never in the time a render takes. So a starter registers a job, hands back its id,
and lets a daemon thread do the work — the agent keeps editing while a concert exports.

Four decisions:

* **A cache hit never starts a thread.** It comes back from the starter already completed,
  with ``cached: true``, and is still a real job record so ``get_job`` and ``list_jobs``
  read the same for a hit as for a run.

* **Nothing escapes the worker.** There is no envelope around a thread — an exception here
  would vanish into a dead thread and leave a job running forever. Every failure is caught
  and written into the record as the same ``cause``/``fix`` shape a tool returns, and that
  includes writing the cache entry: a job that produced a result but could not record it is
  a failed job, not a job that never ended.

* **Only one job drives Resolve at a time.** The scripting API is one global application,
  and two jobs pushing the render queue at once corrupt it. Jobs that touch Resolve
  serialise on one lock; pure compute (analysis on already-acquired audio) does not wait.

* **Threads, not processes — until the work stops needing this process.** The heavy
  libraries are not loaded yet — they get imported inside the workers that need them, so
  server startup stays fast either way — and a worker driving the Resolve API has to live in
  the process holding the handle. But a thread dies with its process, and stem separation is
  half an hour of GPU work that needs no handle at all once the audio is on disk. So a
  worker may return ``Detached`` instead of a result: the rest of that job moves into a
  process of its own (``detached``), and the record on disk is how the two stay in touch.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from ..config import Config, get_config
from ..errors import ChainedJobError, InternalError, ResolveMcpError
from ..logging_config import get_logger
from . import cache, detached, store
from .store import JobRecord

log = get_logger("jobs")

WAIT_TIMEOUT = 30.0
FOLLOW_POLL = 0.1
WAITING = "waiting for Resolve"

PROGRESS_INTERVAL = 1.0
"""How often a moving progress bar is written to the record, in seconds. See ``execute``.

Comfortably below ``store.HEARTBEAT_CEILING``, which reads a record nothing has written to as
a worker that is gone: the bar is the heartbeat, and a throttle that outran the ceiling would
have a running job declare itself dead.
"""

RESOLVE_LOCK = threading.Lock()
"""Held for the whole of any job that drives the Resolve application."""

_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


class JobOutput(NamedTuple):
    """What a worker returns: the result the agent reads, and the files it owns.

    ``artifacts`` is what makes a cache entry verifiable — a hit is only a hit while the
    files it names are still on disk.
    """

    result: dict[str, Any]
    artifacts: tuple[Path, ...] = ()


class Detached(NamedTuple):
    """What a worker returns instead of a result when the rest of the job leaves this process.

    ``plan`` is everything the standalone worker will need that the job's params do not
    already carry — for stems, the acquired audio, which only exists once the part of the job
    that *did* need the Resolve handle has run. It goes onto the record, because the record
    is all the two processes share.
    """

    plan: dict[str, Any]


Progress = Callable[[float, str], None]
Work = Callable[[Progress], JobOutput | Detached]
Watch = Callable[[JobRecord], None]


def band(progress: Progress, floor: float, ceiling: float) -> Progress:
    """Map a sub-task's own 0-1 onto the part of the job that sub-task actually is.

    A render reports its own percentage and knows nothing about the hashing either side of
    it; without this the job would jump to 100% and then sit there.
    """
    span = ceiling - floor

    def scaled(fraction: float, step: str) -> None:
        progress(floor + span * fraction, step)

    return scaled


def start_job(
    kind: str,
    params: dict[str, Any],
    work: Work,
    cache_key: str | None = None,
    touches_resolve: bool = False,
    refresh: bool = False,
    config: Config | None = None,
) -> dict[str, Any]:
    """Register the job and return its record now — completed already, on a cache hit.

    ``refresh`` skips the lookup but not the write: a caller who believes the cache is
    stale gets the work redone, and the fresh result replaces the entry.
    """
    config = config or get_config()
    record = store.new_job(kind, params, cache_key=cache_key, config=config)

    if cache_key is not None and not refresh:
        hit = cache.lookup(cache_key, config)
        if hit is not None:
            record.cached = True
            record.step = "cached"
            log.info("Job %s answered from cache", record.job_id)
            return store.finish(record, result=hit, config=config).payload()

    if touches_resolve and RESOLVE_LOCK.locked():
        record.step = WAITING
        store.save(record, config)

    thread = threading.Thread(
        target=_run,
        args=(record, work, touches_resolve, config),
        name=f"job-{record.job_id}",
        daemon=True,
    )
    with _threads_lock:
        _forget_finished_threads()
        _threads[record.job_id] = thread
    thread.start()
    return record.payload()


def _forget_finished_threads() -> None:
    """The registry only exists to join running work; finished entries are dead weight."""
    for job_id in [one for one, thread in _threads.items() if not thread.is_alive()]:
        del _threads[job_id]


def _run(record: JobRecord, work: Work, touches_resolve: bool, config: Config) -> None:
    if touches_resolve:
        with RESOLVE_LOCK:
            if record.step == WAITING:
                record.step = ""
                store.save(record, config)
            execute(record, work, config)
        return
    execute(record, work, config)


def execute(record: JobRecord, work: Work, config: Config) -> None:
    """Run the worker, and turn whatever comes out of it into a closed job record.

    Public because the detached worker process runs a job through exactly this: the caching,
    the error shaping and the guarantee that nothing escapes are the same guarantees whether
    the work is on a thread here or alone in a process of its own.
    """
    # The bar reaches disk at most once a second. It moves several times a second and a
    # separation moves it for half an hour, which is tens of thousands of writes to a file
    # ``get_job`` is polling at the same time — on Windows the two sides already have to retry
    # around each other's handles (``store._sharing``), and one write a second says everything
    # a poller can read anyway. Two things are never throttled: a step change, which is the one
    # line an agent reads to know what is happening and happens a dozen times in a job rather
    # than a thousand, and the ending, which ``store.finish`` writes itself — so whichever tick
    # the throttle skipped, the last thing the record says is the true one.
    last_save = float("-inf")

    def progress(fraction: float, step: str) -> None:
        nonlocal last_save
        record.progress = min(max(fraction, 0.0), 1.0)
        moved_on = step != record.step
        record.step = step
        now = time.monotonic()
        if moved_on or now - last_save >= PROGRESS_INTERVAL:
            last_save = now
            store.save(record, config)

    try:
        output = work(progress)
        if isinstance(output, Detached):
            # Not an ending: the record stays running, and the worker process closes it. The
            # cache entry is written there too, for the same reason the result is — a job is
            # only cacheable once it has a result, and this one does not have one yet.
            resumed = _hand_off(record, output.plan, progress, config)
            if resumed is None:
                return
            output = resumed
        if record.cache_key is not None:
            cache.remember(record.cache_key, record.kind, output.result, output.artifacts, config)
    except ResolveMcpError as exc:
        store.finish(record, error=exc.payload(), config=config)
        return
    except Exception as exc:  # noqa: BLE001 - a dead thread must not swallow the failure
        log.exception("Job %s raised unexpectedly", record.job_id)
        store.finish(
            record,
            error=InternalError(cause=f"{type(exc).__name__}: {exc}").payload(),
            config=config,
        )
        return

    store.finish(record, result=output.result, config=config)


def _hand_off(
    record: JobRecord,
    plan: dict[str, Any],
    progress: Progress,
    config: Config,
) -> JobOutput | None:
    """Move the rest of the job into a process of its own — unless this already *is* that one.

    ``execute`` is shared on purpose: the detached worker closes its job through exactly this
    path. That makes the hand-off branch reachable inside the worker too, and a worker that
    launched a worker would launch another, and another — one process per generation, each
    holding the same record. The record says which side of the hand-off we are on: it is
    marked detached and names *this* pid only in the process that adopted it. There the plan
    is run here instead, which is what a launch would have arranged anyway, minus the process.
    """
    if record.detached and record.pid == os.getpid():
        log.warning(
            "Job %s handed off inside its own detached worker (pid %s); running it here",
            record.job_id,
            record.pid,
        )
        record.plan = plan
        store.save(record, config)
        from .worker import worker_for  # local: worker imports this module at its own import

        return worker_for(record.kind)(record, progress, config)
    detached.launch(record, plan, config)
    return None


def alive(job_id: str) -> bool:
    """Whether a worker thread for this job is still running in this process.

    A job that a chained job is following can only be finished by that thread. If the
    thread is gone while the record still says running, nothing will ever close it, and a
    follower that kept polling would wait forever.

    Says nothing about a detached job: its thread ends at the hand-off, on purpose, and what
    is still running is a process this registry never held. ``store`` answers for those.
    """
    with _threads_lock:
        thread = _threads.get(job_id)
    return thread is not None and thread.is_alive()


def follow(
    job_id: str,
    watch: Watch | None = None,
    poll: float = FOLLOW_POLL,
    sleep: Callable[[float], None] = time.sleep,
    config: Config | None = None,
) -> JobRecord:
    """Wait for a job this job started, reporting it as it goes, and raise what it raised.

    ``wait_for`` joins a thread and hands back whatever the record says; this is the other
    half of "for chained work" — the caller sees each record as it lands, so a render that
    is minutes of the parent job is minutes the parent job can report, and a failure comes
    back as an exception carrying the child's own cause and fix rather than as a result the
    caller has to inspect. A cache hit is already finished and is never waited on at all.
    """
    record = store.load(job_id, config)
    while record.state == store.RUNNING:
        if watch is not None:
            watch(record)
        sleep(poll)
        record = store.load(job_id, config)
        if record.state == store.RUNNING and not record.detached and not alive(job_id):
            # The thread may have closed the record between that read and this check, so
            # the answer is the record read *after* the thread is known to be gone.
            record = store.load(job_id, config)
            if record.state == store.RUNNING:
                raise InternalError(
                    cause=f"The {record.kind} job {job_id} stopped without finishing.",
                    detail={"job_id": job_id, "step": record.step, "progress": record.progress},
                )

    if record.state == store.FAILED:
        raise ChainedJobError(record.error or {}, job_id)
    return record


def wait_for(job_id: str, timeout: float = WAIT_TIMEOUT, config: Config | None = None) -> JobRecord:
    """Block until the job is off the thread pool — for chained work, and for tests.

    A job started by an earlier server process has no thread here; its record already says
    what happened to it, so the answer comes straight off disk. A detached job comes back
    still running for the same reason: its thread ended at the hand-off and the process that
    will finish it is not one this can join.
    """
    with _threads_lock:
        thread = _threads.get(job_id)
    if thread is not None:
        thread.join(timeout)
    return store.load(job_id, config)
