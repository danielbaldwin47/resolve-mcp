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

* **Threads, not processes.** The heavy libraries are not loaded yet — they get imported
  inside the workers that need them, so server startup stays fast either way — and a
  worker driving the Resolve API has to live in the process holding the handle.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from ..config import Config, get_config
from ..errors import InternalError, ResolveMcpError
from ..logging_config import get_logger
from . import cache, store
from .store import JobRecord

log = get_logger("jobs")

WAIT_TIMEOUT = 30.0
WAITING = "waiting for Resolve"

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


Progress = Callable[[float, str], None]
Work = Callable[[Progress], JobOutput]


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
            _work(record, work, config)
        return
    _work(record, work, config)


def _work(record: JobRecord, work: Work, config: Config) -> None:
    """Run the worker, and turn whatever comes out of it into a closed job record."""

    def progress(fraction: float, step: str) -> None:
        record.progress = min(max(fraction, 0.0), 1.0)
        record.step = step
        store.save(record, config)

    try:
        output = work(progress)
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


def wait_for(job_id: str, timeout: float = WAIT_TIMEOUT, config: Config | None = None) -> JobRecord:
    """Block until the job is off the thread pool — for chained work, and for tests.

    A job started by an earlier server process has no thread here; its record already says
    what happened to it, so the answer comes straight off disk.
    """
    with _threads_lock:
        thread = _threads.get(job_id)
    if thread is not None:
        thread.join(timeout)
    return store.load(job_id, config)
