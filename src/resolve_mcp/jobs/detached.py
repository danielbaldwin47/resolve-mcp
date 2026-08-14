"""Handing a job to a process that outlives this one.

``runner`` starts heavy work on a daemon thread, and a daemon thread dies with its process.
For a render that is right — the work needs the Resolve handle this process holds, so there
is nothing to save. For stem separation it is the whole problem (G4): the pass over a
74-minute mix runs half an hour on the GPU, needs no Resolve handle once the audio is on
disk, and has twice now been killed at the 50% mark by the session that launched it ending.

So a worker that has reached that point returns ``runner.Detached`` instead of a result, and
this module launches ``python -m resolve_mcp.jobs.worker <job-id>`` to finish the job.

Four decisions:

* **The record is the whole hand-off.** The child is given one argument, a job id, and reads
  everything else off disk — params, cache key, and the ``plan`` the starter wrote. Nothing
  travels over a pipe, so there is no live channel between the two processes that could
  break, and the record stays the single source of truth it already was.

* **The child's config comes from the parent's config, not from the parent's environment.**
  ``Config.to_env`` is passed through explicitly, so a server configured in code — a test,
  or an entry point that set the cache directory itself — has its worker write into the same
  cache rather than into ``%LOCALAPPDATA%``.

* **Detached, and out of the process group.** On Windows ``DETACHED_PROCESS`` plus a new
  process group is what survives the parent exiting and a Ctrl-C in the parent's console;
  ``CREATE_BREAKAWAY_FROM_JOB`` is tried first because a launcher run inside a job object
  that kills its children on close — an agent's shell, a CI runner — would otherwise take
  the worker down with it, and that is exactly the failure this module exists to prevent. A
  job object that forbids breakaway refuses the create outright, so the flag is dropped and
  the create retried rather than the launch failing.

* **The worker's output goes to a file beside the record.** It is detached: it has no
  console to inherit and no parent reading its pipes, and a separation that fails in the
  child is diagnosed from that file or not at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..errors import InternalError
from ..logging_config import get_logger
from . import store
from .store import JobRecord

log = get_logger("jobs")

MODULE = "resolve_mcp.jobs.worker"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

WINDOWS_FLAGS = (
    DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB,
    DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
)
"""Preferred first, then the fallback for a job object that refuses to be broken away from."""

HANDING_OFF = "handing the rest of the job to a detached worker"

DOING = {"separate_stems": "separating"}
"""What a kind is called while it runs, for the step a poller reads. See ``step_for``."""


def step_for(kind: str, pid: int) -> str:
    """The step a handed-off job shows, in the words of the job's own kind.

    This module knows nothing about stems — it hands off whatever ``runner`` gives it — so a
    hardcoded "separating" here would mislabel the first other kind that detaches, and the
    step is the one line an agent reads to know what is happening. Unknown kinds get their
    own name rather than a guess.
    """
    return f"{DOING.get(kind, kind.replace('_', ' '))} in a detached worker (pid {pid})"

Spawn = Callable[[list[str], Path, Config], int]
"""Start that argv detached, with its output going to that file, and return its pid.

A parameter for the same reason ffmpeg's and the separator's calls are: everything this
module decides — what the record says before and after, which command, which environment —
is worth verifying without a process, and the one test that does want a real process wants
it deliberately.
"""


def command(job_id: str, executable: str | None = None) -> list[str]:
    """The worker invocation, as a list — never a shell string.

    ``sys.executable`` rather than a configured interpreter: the worker imports this package
    and nothing else exotic, and the interpreter running the server is the one interpreter
    already proven to have it.
    """
    return [executable or sys.executable, "-m", MODULE, job_id]


def spawn_options(platform: str = sys.platform) -> list[dict[str, Any]]:
    """The ``Popen`` keywords that detach a child, best first.

    Parameterised by platform so the Windows shape is testable from anywhere — the flags are
    the part that decides whether the fix works at all, and they are unreachable on the CI
    box that runs the suite.
    """
    if platform == "win32":
        return [{"creationflags": flags} for flags in WINDOWS_FLAGS]
    return [{"start_new_session": True}]


def worker_log(job_id: str, config: Config) -> Path:
    """Where the detached worker's own output lands: beside the record, never inside it.

    ``store`` owns the name, because ``store`` is what reads the file back onto the record of a
    worker that died without writing its own failure (#192). This is the launcher's word for
    the same file, kept so callers here do not have to know which module named it.
    """
    return store.worker_log(job_id, config)


def child_env(config: Config, env: dict[str, str] | None = None) -> dict[str, str]:
    """The parent's environment with this config's settings written over it."""
    return {**(os.environ if env is None else env), **config.to_env()}


def launch(
    record: JobRecord,
    plan: dict[str, Any],
    config: Config | None = None,
    spawn: Spawn | None = None,
) -> int:
    """Write the hand-off onto the record, start the worker, and return its pid.

    The record is marked detached *before* the spawn, and names this process as the launcher:
    between the two saves it has no worker pid yet, and ``store`` reads that — plus a
    launcher that is still alive — as a launch in flight. The other order would leave a window
    in which the record looks like an ordinary job whose thread has ended, which is a
    failure any poller would report.
    """
    config = config or get_config()
    record.detached = True
    record.plan = plan
    record.step = HANDING_OFF
    record.launcher_pid = os.getpid()
    store.save(record, config)

    argv = command(record.job_id)
    destination = worker_log(record.job_id, config)
    destination.parent.mkdir(parents=True, exist_ok=True)
    config.cache_dir.mkdir(parents=True, exist_ok=True)
    pid = (spawn or _spawn)(argv, destination, config)

    # The launcher's own reading of the pid, and only until the worker adopts the record and
    # writes its own: a venv interpreter on Windows can be a trampoline that runs the real
    # interpreter as a child, so what was started here and what is doing the work are not
    # always the same process. The trampoline outlives its child, so this stays a truthful
    # "still running" for the second or so before the worker overwrites it.
    #
    # Left beside the record rather than written into it, because by now the record is not
    # ours to write: the worker adopts it as its first act and can even have finished a short
    # job, and this process's copy is stale in both cases. Writing it back would put a
    # launcher's pid and ``running`` over a record the worker had already closed — and the
    # worker never writes again, so that job would poll as running for good, its result gone
    # with the record that carried it. ``note_worker_pid`` writes a note only the launcher
    # touches, which readers fold in until the worker's own pid lands, and reports what a
    # reader would now see.
    latest = store.note_worker_pid(record.job_id, pid, step_for(record.kind, pid), config) or record
    if latest.pid == pid:
        log.info(
            "Job %s handed off to detached worker pid %s: %s", record.job_id, pid, " ".join(argv)
        )
    record.pid = latest.pid
    record.step = latest.step
    return pid


def _spawn(
    argv: list[str],
    destination: Path,
    config: Config,
    options: list[dict[str, Any]] | None = None,
) -> int:
    """Start the worker with the first set of detach flags the OS accepts.

    ``options`` is a parameter so the Windows retry can be exercised from a box that has no
    job objects to be refused by — the fallback only matters on the platform the tests do not
    run on, which is exactly the kind of code that is wrong for a year.
    """
    options = spawn_options() if options is None else options
    with destination.open("ab") as sink:
        for index, chosen in enumerate(options):
            try:
                process = subprocess.Popen(  # noqa: S603 - argv is built here, never a shell
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    cwd=str(config.cache_dir),
                    env=child_env(config),
                    close_fds=True,
                    **chosen,
                )
            except OSError as exc:
                if index < len(options) - 1:
                    log.warning("Detached launch refused %s (%s); retrying without it", chosen, exc)
                    continue
                raise InternalError(
                    cause=f"Could not start a detached worker: {type(exc).__name__}: {exc}",
                    fix=(
                        "Run the job again without detaching (detach=false) — it will run in "
                        "the server process and die with it, which is the old behaviour."
                    ),
                    detail={"argv": argv, "options": chosen},
                ) from exc
            # The handle is kept, not dropped: a worker started here is still this process's
            # child, and an unreaped child that has exited is a zombie whose pid still answers
            # every liveness question as if it were running. ``store`` polls it instead.
            store.remember_child(process.pid, process)
            return process.pid
    raise AssertionError("unreachable")  # pragma: no cover
