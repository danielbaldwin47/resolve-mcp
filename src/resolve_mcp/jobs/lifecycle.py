"""Is anything still running this job, and if not, why not. One pure function, no disk.

``verdict(record, now, alive)`` is a total function of the record's state, its detached flag,
its session, its two pids and its timestamps, plus whatever the injected ``alive`` says about
those pids. It reads nothing, writes nothing and decides nothing twice: the store composes it
(read the file → verdict → maybe write the failure back), and the store is the only thing here
that touches a disk. Splitting it out is what makes the truth table below testable in memory —
every row used to cost a save and a load.

The pid/session/silence reading inside it is not this module's own either: a job record is a
claim on the work exactly as a stems directory's marker file is, and ``lease.liveness`` is the
one place that says whether the process behind a claim is still working. What is here is
job-shaped — which pid to ask about and when, and the sentence the agent reads afterwards.

Three rules decide all of it.

* **A restart is detected by session, not by pid.** Every record carries the id of the server
  process that wrote it. A record still marked ``running`` whose session is not this one
  belongs to a server that is gone — its worker thread died with the process, so nothing will
  ever finish it. pids get recycled; a per-process uuid cannot be mistaken.

* **A detached job is judged by its pid instead, because the session rule is backwards for
  it.** A record marked ``detached`` names a worker process of its own, and outliving the
  server that started it is the whole point (G4: a 30-minute separation died with every
  session that launched it). So the session check is skipped for those and the pid answers
  instead: alive means running, gone means interrupted, and no pid yet means a launch still
  in flight — in this session, or in whichever server is named by ``launcher_pid`` while that
  process is still alive *and* the record is younger than ``LAUNCH_WINDOW``, because those are
  the only two processes that will ever write the worker's pid and neither takes two minutes
  to do it. A pid-less record whose launcher is gone, or one that has sat pid-less far longer
  than any launch takes, is closed rather than left running forever. pids do get recycled —
  a recycled one can only make a dead job read as running a while longer, never the reverse,
  and the worker writes its own ending in every path it can still reach. "A while longer" is
  the ceiling's doing: a live pid is believed only while the record is still being written to
  (``HEARTBEAT_CEILING``), because a reboot reissues every number on the machine and nothing
  else would ever close the record it left behind.

* **A job that is over is not judged at all.** The state on the record is the first question
  and it ends the matter: a finished detached job names a pid that has of course exited, and
  asking about it would fail a job that succeeded.

The log lines are here rather than at the call site because each one carries the number the
reasoning turned on — the silence, the age, the pid — and a verdict nobody can account for
afterwards is what left a live failure undiagnosable before. What is emitted is a line; what
is decided is the return value, and that depends on the arguments alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from ..lease import SESSION, Alive, Liveness, Owner, liveness
from ..logging_config import get_logger

if TYPE_CHECKING:
    from .store import JobRecord

log = get_logger("jobs")

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
STATES = (RUNNING, COMPLETED, FAILED)

LAUNCH_WINDOW = 120.0
"""How long a detached record may go with no worker pid before the launch is not in flight.

Generous by an order of magnitude: what has to fit inside it is a ``Popen`` and the worker's
own start-up as far as its adopt, which is a second or two even with a cold interpreter and a
Windows trampoline in the way. Long enough that a slow box is never judged, short enough that
a recycled launcher pid cannot hold a dead record open for the life of the machine. See
``_stalled_launch``.
"""

HEARTBEAT_CEILING = 6 * 60 * 60
"""How long a detached record may go unwritten before its live pid stops being believed.

The same recycling problem one step later. A worker's pid answers "still running" for as long
as *some* process wears that number, and a reboot hands every number out again: the record a
worker left running when the machine went down names a stranger, reads as running at every
poll for the life of the machine, and the only way out was to delete the file. But a bare
``LAUNCH_WINDOW`` here would close the jobs this whole path exists to protect — a separation
is half an hour of GPU work. What separates them is that a worker writes as it works
(``runner.execute`` saves the bar as it moves), so what ages here is silence, not runtime, and
the record's own ``updated_at`` is the heartbeat. Deliberately far longer than any job, for
the same reason stems' ``CLAIM_CEILING`` is: the longest measured pass is under an hour and
reports throughout, so six of them without one write is nobody working. Long silences are
legal — a cold model download reports nothing for a while — and the ceiling is set to outlast
them, because closing a live separation is a worse failure than a dead record read as running
for an afternoon. See ``_running_or_silent``.
"""


class Outcome(StrEnum):
    """What the record turned out to be. Two of these leave it alone; four close it."""

    SETTLED = "settled"
    """Not running any more. Whatever happened to it has already been written."""

    LIVE = "live"
    """Something is still running it: this session's thread, a worker, or a launch in flight."""

    RESTARTED = "restarted"
    """A thread job whose server is gone. Nothing survives a restart to finish it."""

    STALLED_LAUNCH = "stalled_launch"
    """A detached record that never got a worker, and never will."""

    WORKER_GONE = "worker_gone"
    """The detached worker exited before the job finished."""

    SILENT = "silent"
    """A live pid on a record nothing has written to for longer than any job runs silently."""


@dataclass(frozen=True)
class Verdict:
    """The outcome, and the sentence the agent reads when the job is being closed.

    A cause is set for exactly the four outcomes that close a job, so ``cause is None`` is the
    same question as "is anything still running it" — and it is the one the store asks, because
    it narrows the type it is about to pass on.
    """

    outcome: Outcome
    cause: str | None = None


def verdict(record: JobRecord, now: datetime, alive: Alive) -> Verdict:
    """Whether anything is still running this job, and the sentence for it if not.

    ``alive`` is asked about at most one pid: the worker's when the record has one, the
    launcher's when it does not, and neither when the job is already over.
    """
    if record.state != RUNNING:
        return Verdict(Outcome.SETTLED)
    if record.detached:
        return _detached(record, now, alive)
    if record.session == SESSION:
        return Verdict(Outcome.LIVE)
    log.info("Job %s was interrupted by a server restart", record.job_id)
    return Verdict(
        Outcome.RESTARTED,
        f"The server restarted while {record.kind} was running, so the job died with it.",
    )


def _detached(record: JobRecord, now: datetime, alive: Alive) -> Verdict:
    """A detached job outlives its session on purpose, so its pid is what gets asked.

    The reading itself is ``lease.liveness``: a record is a claim on the work, exactly as a
    stems directory's marker file is, and both ask whether the process that wrote it is still
    working. What is left here is the sentence — which record this was, and what to tell the
    agent about a job nothing is running any more.

    ``None`` is a launch still in flight — the starter marks the record detached before it
    spawns, so that a reader in the microseconds between never mistakes it for a thread job
    whose thread has ended. In flight in *some live process*, though: the only process that
    will ever write that pid is the launcher, between its own two saves. Which session wrote
    the record does not answer that, in either direction. A foreign session is not a dead one
    — a second server mid-spawn is a launch in flight exactly as ours is, and judging it by
    session alone would fail a job whose worker was about to start. And our own session is no
    proof that the launch is still happening: noting the worker's pid is best-effort by design
    (``store.note_worker_pid`` swallows a note it cannot write, because the alternative is the
    launcher failing a job a live worker owns), so a worker that dies before it adopts leaves
    a pid-less record here, in this session, that nothing will ever write again — read as "in
    flight in this process" it would run for the life of the server, and a chained job
    following it would poll for exactly as long (``runner.follow`` skips its stalled-thread
    escape for a detached job, on purpose: a detached job has no thread to be missing). So
    every pid-less record is asked the same two questions, its launcher and its age.

    A pid that answers is asked its age too, for the same reason and one step later: a number
    the OS has reissued — after a reboot, every number — reads as a worker still running at
    every poll for ever. What is asked is not how long the job has run but how long the record
    has gone unwritten, because the worker writes as it works. See ``HEARTBEAT_CEILING``.
    """
    if record.pid is None:
        return _stalled_launch(record, now, alive)
    silence = _age(record.updated_at, now)
    reading = liveness(
        Owner(pid=record.pid, silence=silence),
        ceiling=HEARTBEAT_CEILING,
        alive=alive,
    )
    if reading is Liveness.HELD:
        return Verdict(Outcome.LIVE)
    if reading is Liveness.SILENT and silence is not None:
        return _silent(record, silence)
    log.info("Job %s lost its detached worker (pid %s)", record.job_id, record.pid)
    return Verdict(
        Outcome.WORKER_GONE,
        f"The detached worker running {record.kind} exited before the job finished "
        f"(pid {record.pid}).",
    )


def _silent(record: JobRecord, silence: float) -> Verdict:
    """A live pid on a record nothing has written to for longer than the job can run silently.

    The worker owns this record from its adopt onwards and writes it as the work moves, so the
    time since the last write is the worker's heartbeat — the freshest one the record carries,
    and the only one that does not have to be paid for with a second file. Silence past the
    ceiling means the pid is a reissued number rather than the worker that wrote the record.
    """
    log.warning(
        "Job %s has not been written to in %.0f s while pid %s reads as alive; that number "
        "belongs to some other process now, not to the worker that ran the job",
        record.job_id,
        silence,
        record.pid,
    )
    return Verdict(
        Outcome.SILENT,
        f"Nothing has written to the record of {record.kind} in {silence:.0f} seconds, far "
        f"longer than the job can run silently, so pid {record.pid} is a reissued number "
        "rather than the detached worker — nothing is running the job.",
    )


def _stalled_launch(record: JobRecord, now: datetime, alive: Alive) -> Verdict:
    """Why this pid-less record is not a launch in flight, or ``LIVE`` while it still is.

    The launcher's pid is asked first, and its own age second. A pid is a number the OS
    re-issues: a record left by a server that died mid-launch names a launcher that may since
    have been recycled onto some long-lived process, and liveness alone then reads that record
    as launching forever. Nothing is a launch for long — the spawn and the note that follows it
    are a second's work, and the worker writes the record from its adopt onwards — so a record
    with no worker pid that has not been touched in ``LAUNCH_WINDOW`` is a launch that is not
    happening, whatever its launcher's number now belongs to.

    The age question is the one that answers for *this* session's own records: our launcher is
    alive by definition, so nothing but the clock can tell a hand-off in flight from one whose
    worker died before it adopted (see ``_detached``).
    """
    age = _age(record.updated_at, now)
    reading = liveness(
        Owner(pid=record.launcher_pid, silence=age),
        ceiling=LAUNCH_WINDOW,
        alive=alive,
    )
    if reading is Liveness.HELD:
        log.debug(
            "Job %s has no worker pid yet, but its launcher (pid %s) is still running",
            record.job_id,
            record.launcher_pid,
        )
        return Verdict(Outcome.LIVE)
    if reading is not Liveness.SILENT or age is None:
        log.info("Job %s never got a detached worker: its launcher is gone", record.job_id)
        return Verdict(
            Outcome.STALLED_LAUNCH,
            f"The server handing {record.kind} to a detached worker exited before the worker "
            "was started, so nothing is running it.",
        )
    log.warning(
        "Job %s has had no detached worker for %.0f s; pid %s is either a recycled number "
        "rather than the launcher that wrote the record, or a launcher that never started one",
        record.job_id,
        age,
        record.launcher_pid,
    )
    return Verdict(
        Outcome.STALLED_LAUNCH,
        f"The server handing {record.kind} to a detached worker never started one: nothing "
        f"has written to the record in {age:.0f} seconds, so the launch is not in flight.",
    )


def _age(stamp: str, now: datetime) -> float | None:
    """How long before ``now`` that timestamp was, in seconds; ``None`` if it is not one."""
    try:
        written = datetime.fromisoformat(stamp)
    except ValueError:
        log.warning("Cannot read a job record's timestamp: %r", stamp)
        return None
    if written.tzinfo is None:
        written = written.replace(tzinfo=UTC)
    return (now - written).total_seconds()
