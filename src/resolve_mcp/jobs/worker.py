"""The detached worker: one job id in, one closed job record out.

``python -m resolve_mcp.jobs.worker <job-id>``. Started by ``detached.launch`` and by
nothing else, in a process with no Resolve handle, no MCP transport and no parent listening
— so everything it has to say goes into the job record, and everything it prints goes to
the log file the launcher opened for it.

Three decisions:

* **It adopts the record before it does anything.** Writing its own session and pid is what
  turns "a record the server left running" into "a record this process owns", and it happens
  before the first import of anything heavy so that a worker which dies loading torch still
  reads as a worker that was there and stopped.

* **Which work runs is looked up from the record's kind, and imported at that moment.** The
  dispatch is a function rather than a registry the workers write into: a registry would
  have to be populated by importing every worker module, and importing the stems module in
  the server process is the cost this design exists to avoid.

* **The job is closed through ``runner.execute``**, the same code path a threaded job ends
  through. Result caching, structured refusals and "nothing escapes the worker" are one
  implementation, not two — a detached job that failed differently from a threaded one would
  be a second error contract nobody asked for.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from ..config import Config, get_config
from ..errors import InternalError, ResolveMcpError
from ..logging_config import configure_logging, get_logger
from . import runner, store
from .runner import JobOutput, Progress
from .store import JobRecord

log = get_logger("jobs")

Detachable = Callable[[JobRecord, Progress, Config], JobOutput]
"""The work itself. The config is passed rather than looked up: this process built one from
the environment its launcher handed it, and a worker that read the record through that config
while running the separation through another ``get_config()`` would write its stems and its
record into two different caches — the exact split the explicit hand-off exists to prevent."""

USAGE = "usage: python -m resolve_mcp.jobs.worker <job-id>"

STEMS = "separate_stems"


def worker_for(kind: str) -> Detachable:
    """The work a detached process runs for this kind of job, imported only now."""
    if kind == STEMS:
        from ..audio.stems import detached_pass

        return detached_pass
    raise InternalError(
        cause=f"No detached worker knows how to run a {kind!r} job.",
        fix="Start this kind of job without detaching — it runs on a thread in the server.",
        detail={"kind": kind, "detachable": [STEMS]},
    )


def run(job_id: str, config: Config | None = None) -> JobRecord:
    """Take the job over, run it to an ending, and hand back what the record says."""
    config = config or get_config()
    record = store.adopt(job_id, config)
    log.info("Detached worker running job %s (%s)", job_id, record.kind)
    if record.state != store.RUNNING:
        log.warning("Job %s was already %s; the worker has nothing to do", job_id, record.state)
        return record

    try:
        work = worker_for(record.kind)
    except ResolveMcpError as exc:
        return store.finish(record, error=exc.payload(), config=config)

    runner.execute(record, lambda progress: work(record, progress, config), config)
    return store.load(job_id, config)


def main(argv: Sequence[str] | None = None) -> int:
    """Exit 0 when the job completed, 1 when it failed, 2 when it could not be started."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print(USAGE, file=sys.stderr)  # noqa: T201 - this process has no other channel
        return 2
    configure_logging()
    try:
        record = run(args[0])
    except ResolveMcpError as exc:
        log.error("The detached worker could not start job %s: %s", args[0], exc.cause)
        return 2
    return 0 if record.state == store.COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(main())
