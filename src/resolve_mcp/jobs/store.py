"""One JSON record per job, on disk. Four things here are decisions, not bookkeeping.

* **Disk is the only source of truth.** An in-memory registry would be faster to read
  and would vanish on restart, which is precisely the case ``list_jobs`` exists for. The
  running thread updates its record as it goes; readers always read the file.

* **A restart is detected by session, not by pid.** Every record carries the id of the
  server process that wrote it. A record still marked ``running`` whose session is not
  this one belongs to a server that is gone — its worker thread died with the process, so
  nothing will ever finish it. pids get recycled; a per-process uuid cannot be mistaken.

* **That verdict is written back.** The interrupted record is rewritten as failed under
  this session, so the reasoning happens once and the log carries one line for it rather
  than one per poll.

* **A corrupt record loses itself, not the listing.** A record half-written when the
  process died is skipped with a warning; one unreadable file must not hide every other
  job from the agent.

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
  and the worker writes its own ending in every path it can still reach.

* **Only the worker writes the record of a detached job.** The launching process has one
  thing to add — its reading of the worker's pid, for the second before the worker adopts —
  and it leaves that beside the record rather than in it (``note_worker_pid``), because a
  launcher writing its stale copy over a worker that had already finished would lose the
  result and leave the job polling as running for good. No guard can close that window; not
  writing can.
"""

from __future__ import annotations

import ctypes
import itertools
import json
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ..config import Config, get_config
from ..errors import JobInterruptedError, JobNotFoundError
from ..logging_config import get_logger
from ..naming import slug

log = get_logger("jobs")

RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"
STATES = (RUNNING, COMPLETED, FAILED)

SESSION = uuid.uuid4().hex
"""This server process. Records written under any other session predate a restart."""

SHARING_ATTEMPTS = 20
SHARING_PAUSE = 0.01
"""How long either side of a poll waits out the other's handle. See ``_sharing``."""

LAUNCH_WINDOW = 120.0
"""How long a detached record may go with no worker pid before the launch is not in flight.

Generous by an order of magnitude: what has to fit inside it is a ``Popen`` and the worker's
own start-up as far as its adopt, which is a second or two even with a cold interpreter and a
Windows trampoline in the way. Long enough that a slow box is never judged, short enough that
a recycled launcher pid cannot hold a dead record open for the life of the machine. See
``_stalled_launch``.
"""

_sequence = itertools.count()
"""Breaks ties in "newest first": the Windows clock is coarser than two starts in a row."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass
class JobRecord:
    """What the agent sees through ``get_job``, and what a restart reads back."""

    job_id: str
    kind: str
    state: str
    params: dict[str, Any] = field(default_factory=dict)
    session: str = SESSION
    sequence: int = 0
    started_at: str = ""
    updated_at: str = ""
    finished_at: str | None = None
    progress: float = 0.0
    step: str = ""
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    cache_key: str | None = None
    cached: bool = False
    detached: bool = False
    """This job is being run by a process of its own, not by a thread of the server's."""
    pid: int | None = None
    """The detached worker's process id — what says whether it is still there."""
    launcher_pid: int | None = None
    """The process that is starting the detached worker, written before the spawn.

    It answers the one question ``pid`` cannot: a record with no worker pid yet is either a
    launch in flight or a launch that never happened, and after the launching server exits
    those look identical. Written before the spawn so it is on disk for the whole window,
    and never rewritten — the worker's own pid is what matters from the adopt onwards.
    """
    plan: dict[str, Any] | None = None
    """Everything the detached worker needs that the params do not already carry.

    A thread closes over its work; a process cannot, so whatever the starter had already
    computed — for stems, the acquired audio — travels on the record instead. It is the
    hand-off, and it is on disk because disk is the only thing the two processes share.
    """

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _job_id(kind: str) -> str:
    return f"{slug(kind, 'job')}-{uuid.uuid4().hex[:12]}"


def _path(job_id: str, config: Config) -> Path:
    return config.job_dir / f"{job_id}.json"


def new_job(
    kind: str,
    params: dict[str, Any],
    cache_key: str | None = None,
    config: Config | None = None,
) -> JobRecord:
    """Register a running job and write it before any work starts."""
    stamp = _now()
    record = JobRecord(
        job_id=_job_id(kind),
        kind=kind,
        state=RUNNING,
        params=params,
        sequence=next(_sequence),
        started_at=stamp,
        updated_at=stamp,
        cache_key=cache_key,
    )
    save(record, config)
    log.info("Job %s started (%s)", record.job_id, kind)
    return record


def peek(job_id: str, config: Config | None = None) -> JobRecord | None:
    """The record exactly as it is on disk, with no restart check and no verdict written.

    ``load`` answers "what happened to this job", which for an orphan means writing a failure.
    This answers the narrower question a writer asks before it writes — has anyone else
    touched this record since I last saved it — and a reader that judged the job on the way
    past would turn a launch still in flight into a failure. ``None`` for no such record,
    because the callers here are choosing what to write, not reporting to the agent.
    """
    config = config or get_config()
    return _read(_path(job_id, config))


def adopt(job_id: str, config: Config | None = None) -> JobRecord:
    """Take a record over in this process — the detached worker's first act.

    Deliberately reads the file raw, without the restart check ``load`` runs: the record was
    written by the server process, and a worker that ran that check on the way in would fail
    the job at the instant it picked it up. Writing this process's session and pid is what
    makes the record honest afterwards — from here the worker is the only writer, and any
    reader asking whether the job is still alive is asking about this process.

    A record that has already ended is read back and *not* claimed: taking ownership of a
    finished job would rewrite a completed record's session and pid for a worker that is
    about to discover it has nothing to do, and the next reader would see this process named
    on a job it never ran.
    """
    config = config or get_config()
    record = _read(_path(job_id, config))
    if record is None:
        raise JobNotFoundError(job_id)
    if record.state != RUNNING:
        log.info("Job %s already %s; the detached worker is not claiming it", job_id, record.state)
        return record
    record.session = SESSION
    record.pid = os.getpid()
    record.detached = True
    save(record, config)
    log.info("Job %s adopted by detached worker pid %s", job_id, record.pid)
    return record


def save(record: JobRecord, config: Config | None = None) -> None:
    """Write the record atomically — a poll must never read a half-written file."""
    _write(record, config or get_config())


def _write(record: JobRecord, config: Config) -> None:
    """Write the record into place through a scratch file of this process's own."""
    record.updated_at = _now()
    target = _path(record.job_id, config)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = _scratch(target)
    scratch.write_text(json.dumps(record.payload(), indent=2), encoding="utf-8")
    _sharing(lambda: os.replace(scratch, target))


def _unclaimed(record: JobRecord) -> bool:
    """Nobody has adopted this record and nothing has ended it — the launcher's note still holds.

    Also the one condition under which that note is read at all: a record with a worker's own
    pid on it, or one that has ended, has been written by the worker and is the better answer.
    """
    return record.detached and record.pid is None and record.state == RUNNING


def note_worker_pid(
    job_id: str,
    pid: int,
    step: str,
    config: Config | None = None,
) -> JobRecord | None:
    """Leave the launcher's reading of the worker's pid *beside* the record, never in it.

    The launcher's copy of the record is stale the moment the child starts: the worker adopts
    as its first act and may have finished a cache-hit job by the time the launcher gets back
    from ``Popen``. Writing that copy back would put ``running`` and a launcher's pid over a
    record the worker had already closed — and the worker never writes again, so the job would
    poll as running for the life of the server, its result lost with the record that carried
    it. Re-reading the record and guarding the write on what disk said only narrows that: the
    read and the replace are two calls, and a worker that finishes between them is still
    overwritten, undetectably, because after the replace disk says exactly what this process
    put there.

    So the launcher does not write the record at all. Its pid reading goes to a file of its
    own that nothing else writes, and ``_read`` folds it in for readers while — and only while
    — the record is still unadopted and unfinished (``_unclaimed``). A note that loses the race
    is not wrong, just ignored from the moment the worker's own pid lands, and the worker's
    record can no longer be clobbered by anything the launcher does. What the note buys is the
    second or so before the adopt: a venv interpreter on Windows can be a trampoline that runs
    the real interpreter as a child, so what was started here and what does the work are not
    always the same process, and until the worker says otherwise the trampoline's pid is a
    truthful "still running".
    """
    config = config or get_config()
    current = peek(job_id, config)
    if current is None:
        log.warning("Job %s vanished before its launcher could record worker pid %s", job_id, pid)
        return None
    if current.pid is not None:
        log.info(
            "Job %s was adopted by pid %s before its launcher could record pid %s",
            job_id,
            current.pid,
            pid,
        )
        return current
    if current.state != RUNNING:
        log.info(
            "Job %s already ended (%s) before its launcher could record pid %s",
            job_id,
            current.state,
            pid,
        )
        return current
    _write_note(_path(job_id, config), pid, step)
    landed = peek(job_id, config)
    if landed is not None and landed.pid != pid:
        log.info(
            "Job %s: the detached worker (pid %s) reached the record first, so the launcher's "
            "note of pid %s is ignored",
            job_id,
            landed.pid,
            pid,
        )
    return landed or current


def _note_path(target: Path) -> Path:
    """Where the launcher's pid reading lives: beside the record, under a name of its own.

    Deliberately not ``.json``: ``load_all`` globs that, and a half-record read back as a job
    would list a job that does not exist.
    """
    return target.with_name(f"{target.stem}.launcher")


def _write_note(target: Path, pid: int, step: str) -> None:
    """Write the launcher's note atomically. Only the launching process ever writes this."""
    note = _note_path(target)
    scratch = _scratch(note)
    scratch.write_text(json.dumps({"pid": pid, "step": step}), encoding="utf-8")
    _sharing(lambda: os.replace(scratch, note))
    log.info(
        "Job %s: its launcher noted detached worker pid %s beside the record", target.stem, pid
    )


def _read_note(target: Path) -> dict[str, Any] | None:
    """The launcher's note, or ``None`` if there is none or it is not legible."""
    note = _note_path(target)
    try:
        raw = json.loads(_sharing(lambda: note.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("Ignoring an unreadable launcher note: %s", note)
        return None
    return raw if isinstance(raw, dict) and isinstance(raw.get("pid"), int) else None


def _clear_note(target: Path) -> None:
    """Drop the launcher's note once the record has an ending of its own to answer with."""
    try:
        _note_path(target).unlink(missing_ok=True)
    except OSError:  # pragma: no cover - a note left behind is read by nothing that matters
        log.debug("Could not clear the launcher note beside %s", target)


def _scratch(target: Path) -> Path:
    """Where a record is written before it is moved into place — one name per process.

    Per-pid, because two processes write this one record: the server up to the hand-off and
    the detached worker after it, overlapping for the second or so it takes the worker to
    adopt. One shared scratch name has them writing the same file and each replacing the
    target with whatever bytes happened to be in it — the atomic write's own guarantee, lost
    in exactly the case it was added for, and the record that lands is a splice of the two.
    Built from the whole name rather than the stem, so the record and the launcher's note —
    which sit beside each other and differ only in extension — do not write through one
    scratch file. The suffix is deliberately not ``.json``: a scratch file must never be read
    back as a record by ``load_all``.
    """
    return target.with_name(f"{target.name}.writing.{os.getpid()}")


def finish(
    record: JobRecord,
    result: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    config: Config | None = None,
) -> JobRecord:
    """Close the job out as completed or failed, and say so in the log."""
    config = config or get_config()
    record.state = FAILED if error is not None else COMPLETED
    record.result = result
    record.error = error
    record.finished_at = _now()
    record.progress = 1.0 if error is None else record.progress
    save(record, config)
    _clear_note(_path(record.job_id, config))
    release_child(record.pid)
    if error is None:
        log.info("Job %s completed", record.job_id)
    else:
        log.warning("Job %s failed: %s", record.job_id, error.get("cause"))
    return record


def load(job_id: str, config: Config | None = None) -> JobRecord:
    """Read one record, converting a restart-orphaned job into an honest failure."""
    config = config or get_config()
    record = _read(_path(job_id, config))
    if record is None:
        raise JobNotFoundError(job_id)
    return _recovered(record, config)


def load_all(state: str | None = None, config: Config | None = None) -> list[JobRecord]:
    """Every job this cache directory knows about, newest first."""
    config = config or get_config()
    records = [_read(path) for path in sorted(config.job_dir.glob("*.json"))]
    found = [_recovered(one, config) for one in records if one is not None]
    found.sort(key=lambda one: (one.started_at, one.sequence), reverse=True)
    if state is None:
        return found
    return [one for one in found if one.state == state]


def _sharing[T](attempt: Callable[[], T]) -> T:
    """Do it again if the other side of a poll had the file open.

    Windows refuses to replace a file while another handle holds it, and refuses the read
    that lands mid-replace — and polling is exactly two handles on one record: ``get_job``
    or a chained job on one side, the worker saving its progress on the other. The window
    is microseconds wide, so a short retry closes it. Letting the error out would kill the
    worker thread mid-save and leave the record saying ``running`` forever, which is the
    one failure the whole store exists to prevent; on the reading side it would report a
    running job as gone.
    """
    for attempt_number in range(SHARING_ATTEMPTS):
        try:
            return attempt()
        except PermissionError:
            if attempt_number == SHARING_ATTEMPTS - 1:
                raise
            time.sleep(SHARING_PAUSE)
    raise AssertionError("unreachable")  # pragma: no cover


def _read(path: Path) -> JobRecord | None:
    try:
        raw = json.loads(_sharing(lambda: path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.warning("Skipping an unreadable job record: %s", path)
        return None
    if not isinstance(raw, dict) or "job_id" not in raw:
        log.warning("Skipping a job record with no id: %s", path)
        return None
    known = {name: raw[name] for name in JobRecord.__dataclass_fields__ if name in raw}
    return _noted(JobRecord(**known), path)


def _noted(record: JobRecord, path: Path) -> JobRecord:
    """Fold the launcher's pid note into a record that has nothing better to say.

    The note is what the launcher used to write onto the record itself, moved out of the way
    of the worker (see ``note_worker_pid``). It answers for exactly one window — record
    written, worker not yet adopted — and from the adopt onwards the record's own pid is the
    answer and the note is not read again.
    """
    if not _unclaimed(record):
        return record
    note = _read_note(path)
    if note is None:
        return record
    record.pid = note["pid"]
    step = note.get("step")
    if isinstance(step, str) and step:
        record.step = step
    return record




STILL_ACTIVE = 259
"""What Windows reports as a process's exit code while it has not got one yet."""

_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x00100000
_ACCESS_RIGHTS = (_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, _QUERY_LIMITED_INFORMATION)
"""Both rights first — the wait needs ``SYNCHRONIZE`` — then query alone if that is refused."""

_ERROR_ACCESS_DENIED = 5
_WAIT_TIMEOUT = 0x102
_WAIT_FAILED = 0xFFFFFFFF


class Child(Protocol):
    """The part of ``subprocess.Popen`` this module needs: has it exited yet?"""

    def poll(self) -> int | None: ...


_children: dict[int, Child] = {}
_children_lock = threading.Lock()


def remember_child(pid: int, child: Child) -> None:
    """Keep the handle on a detached worker *this* process started.

    ``start_new_session`` takes the worker out of the process group, not out of the family:
    on POSIX it is still this process's child, so when it exits it stays a zombie until
    somebody reaps it — and a zombie answers ``kill(pid, 0)`` exactly as a live process does.
    Without this, a worker that crashed would read ``running`` for as long as the server
    lived, which is the one verdict the detached path exists to get right. ``poll`` both
    reaps it and gives the honest answer. On Windows the open handle also keeps the pid from
    being handed to anybody else while we are still asking about it.

    Every remembered handle is a promise to poll it, and only a *running* record is ever
    polled — so a job that ends leaves its handle behind, and on POSIX that is a zombie the
    process carries for as long as it lives. A server that separates all day would collect
    one per job. The sweep here is the cheap place to pay that back: a launch is rare, the
    poll is a syscall, and it costs nothing to reap everything that has already exited on
    the way past.
    """
    _prune_children()
    with _children_lock:
        _children[pid] = child


def _prune_children() -> None:
    """Reap and forget every remembered worker that has already exited."""
    with _children_lock:
        remembered = list(_children.items())
    gone = [pid for pid, child in remembered if child.poll() is not None]
    if not gone:
        return
    with _children_lock:
        for pid, child in remembered:
            if pid in gone and _children.get(pid) is child:
                del _children[pid]
    log.info("Reaped %s detached worker(s) that had exited: %s", len(gone), gone)


def release_child(pid: int | None) -> None:
    """Let go of the handle on a worker whose job has ended.

    Only the process that started the worker holds a handle on it, so only that process can
    let one go — and it is never the one that ends a detached job. ``finish`` runs in the
    worker, where the dict is empty, so the call there covers the launcher's own endings (an
    in-process job, a record it closes as interrupted) and nothing else. The launcher's real
    moment is the one after: ``_recovered`` sees a detached record that has stopped running,
    which is the first time the launching process learns the job is over. Both call this
    because after either, nothing asks ``pid_alive`` about this job again, so the handle would
    sit in the dict — and its process in the process table — until the server exited. A worker
    that is somehow still running keeps its handle: it is still this process's child, and
    dropping it early is how a zombie is made rather than reaped.
    """
    if pid is None:
        return
    with _children_lock:
        child = _children.get(pid)
    if child is None or child.poll() is None:
        return
    with _children_lock:
        if _children.get(pid) is child:
            del _children[pid]
    log.info("Released the handle on detached worker pid %s: its job has ended", pid)


def _child_state(pid: int) -> bool | None:
    """Whether a worker this process started is still running; ``None`` if it is not ours."""
    with _children_lock:
        child = _children.get(pid)
    if child is None:
        return None
    if child.poll() is None:
        return True
    with _children_lock:
        # Reaped and gone: forget it, or a recycled pid would be answered for by a dead handle.
        if _children.get(pid) is child:
            del _children[pid]
    if _os_pid_alive(pid):
        # The handle is spent but the number is not. Once a child is reaped the OS is free to
        # hand its pid to somebody else, and a *live* process wearing it is not our dead child
        # — answering "gone" from the stale handle would close somebody's running separation
        # as interrupted, which is the one verdict this rule exists to get right. The entry is
        # dropped and the question re-asked of the OS.
        log.info("pid %s is a live process, not our exited worker; dropping the handle", pid)
        return None
    return False


def pid_alive(pid: int) -> bool:
    """Whether that process is still running.

    A worker we started ourselves is judged by its own handle (see ``remember_child``); one
    adopted off disk after a restart is nobody's child here, and only the OS can answer for
    it.
    """
    if pid <= 0:
        return False
    ours = _child_state(pid)
    if ours is not None:
        return ours
    return _os_pid_alive(pid)


if sys.platform == "win32":
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Declared, not left to ctypes' default ``c_int``: a HANDLE is 64-bit in a 64-bit process
    # and the default return type truncates it, so an untruncated handle would be closed by
    # the wrong number — or read as NULL and reported as a dead process.
    _kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    _kernel32.GetExitCodeProcess.restype = ctypes.c_int
    _kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    _kernel32.CloseHandle.restype = ctypes.c_int

    def _open_process(pid: int) -> tuple[int | None, int]:
        """A query handle on that process, or ``None`` and the Windows error saying why."""
        error = 0
        for rights in _ACCESS_RIGHTS:
            ctypes.set_last_error(0)
            handle = _kernel32.OpenProcess(rights, 0, pid)
            if handle:
                return int(handle), 0
            error = ctypes.get_last_error()
        return None, error

    def _still_running(handle: int) -> bool:
        """``STILL_ACTIVE`` is also a legal exit code, so ask the wait which of the two it is.

        A process that exited with 259 reports 259 forever, and reading that as "running"
        would leave a finished job saying ``running`` until the server restarted. The wait
        does not have that ambiguity: a process object is signalled once and only once the
        process has exited. If the handle was opened without ``SYNCHRONIZE`` the wait cannot
        be asked at all, and then the exit code is all there is — read as alive, which is the
        direction that only ever delays a verdict rather than inventing one.
        """
        state = int(_kernel32.WaitForSingleObject(handle, 0))
        if state == _WAIT_FAILED:
            return True
        return state == _WAIT_TIMEOUT

    def _os_pid_alive(pid: int) -> bool:
        """Whether that process is still running, asked without touching it.

        ``os.kill(pid, 0)`` is the POSIX way to ask and is *not* an option here: on Windows
        Python implements ``os.kill`` as ``TerminateProcess``, so the innocent-looking probe
        would kill the separation it was asking about. ``OpenProcess`` with the
        query-only right and ``GetExitCodeProcess`` is the question actually being asked —
        and it has to be the exit code rather than whether the handle opened, because the
        launcher still holds a handle on a worker that has already exited, which keeps the
        process object openable long after the process is gone.

        A refused open is not an answer: ``ACCESS_DENIED`` means a process is there and this
        token may not ask about it — a worker started by another user, or by an elevated
        server — and reading that as "gone" would close a running separation as interrupted.
        It reads as alive, exactly as the POSIX side reads ``PermissionError``.
        """
        handle, error = _open_process(pid)
        if handle is None:
            if error == _ERROR_ACCESS_DENIED:
                log.debug("Windows refused a query handle on pid %s; reading it as alive", pid)
                return True
            return False
        try:
            code = ctypes.c_ulong()
            if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            if code.value != STILL_ACTIVE:
                return False
            return _still_running(handle)
        finally:
            _kernel32.CloseHandle(handle)

else:

    def _os_pid_alive(pid: int) -> bool:
        """Whether that process is still running. Signal 0 asks without delivering anything."""
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _recovered(record: JobRecord, config: Config) -> JobRecord:
    """A job still running under a dead session cannot finish. Say so, once.

    Also the launching process's only news of a detached job that ended well. ``finish`` runs
    in the worker, where the handle dict is empty, so a normally-completed detached job would
    leave the launcher holding its child handle — a zombie on POSIX — until the server exited.
    This is where the launcher reads that the job is over, so this is where it lets go.
    """
    if record.state != RUNNING:
        if record.detached:
            release_child(record.pid)
        return record
    if record.detached:
        return _recovered_detached(record, config)
    if record.session == SESSION:
        return record
    log.info("Job %s was interrupted by a server restart", record.job_id)
    interrupted = JobInterruptedError(
        cause=f"The server restarted while {record.kind} was running, so the job died with it.",
        detail={"job_id": record.job_id, "progress": record.progress, "step": record.step},
    )
    record.session = SESSION
    return finish(record, error=interrupted.payload(), config=config)


def _recovered_detached(record: JobRecord, config: Config) -> JobRecord:
    """A detached job outlives its session on purpose, so its pid is what gets asked.

    ``None`` is a launch still in flight — the starter marks the record detached before it
    spawns, so that a reader in the microseconds between never mistakes it for a thread job
    whose thread has ended. In flight in *some live process*, though: the only process that
    will ever write that pid is the launcher, between its own two saves. That is this session
    for a job we started, and another server's for a job read out of a shared cache directory
    — a second server mid-spawn is a launch in flight exactly as ours is, and judging it by
    session alone would fail a job whose worker was about to start. So the launcher's own pid
    is asked when the session is not ours, and only a launcher that is gone too makes this
    the record nothing will ever write again.
    """
    if record.pid is None:
        if record.session == SESSION:
            return record
        stalled = _stalled_launch(record)
        if stalled is None:
            return record
        return _interrupted(record, stalled, config)
    if pid_alive(record.pid):
        return record
    log.info("Job %s lost its detached worker (pid %s)", record.job_id, record.pid)
    return _interrupted(
        record,
        f"The detached worker running {record.kind} exited before the job finished "
        f"(pid {record.pid}).",
        config,
    )


def _stalled_launch(record: JobRecord) -> str | None:
    """Why this pid-less record is not a launch in flight, or ``None`` while it still is.

    The launcher's pid is asked first, and its own age second. A pid is a number the OS
    re-issues: a record left by a server that died mid-launch names a launcher that may since
    have been recycled onto some long-lived process, and liveness alone then reads that record
    as launching forever. Nothing is a launch for long — the spawn and the note that follows it
    are a second's work, and the worker writes the record from its adopt onwards — so a record
    with no worker pid that has not been touched in ``LAUNCH_WINDOW`` is a launch that is not
    happening, whatever its launcher's number now belongs to.
    """
    if record.launcher_pid is None or not pid_alive(record.launcher_pid):
        log.info("Job %s never got a detached worker: its launcher is gone", record.job_id)
        return (
            f"The server handing {record.kind} to a detached worker exited before the worker "
            "was started, so nothing is running it."
        )
    age = _age(record.updated_at)
    if age is not None and age > LAUNCH_WINDOW:
        log.warning(
            "Job %s has had no detached worker for %.0f s; pid %s is a recycled number rather "
            "than the launcher that wrote the record",
            record.job_id,
            age,
            record.launcher_pid,
        )
        return (
            f"The server handing {record.kind} to a detached worker never started one: nothing "
            f"has written to the record in {age:.0f} seconds, so the launch is not in flight."
        )
    log.debug(
        "Job %s has no worker pid yet, but its launcher (pid %s) is still running",
        record.job_id,
        record.launcher_pid,
    )
    return None


def _age(stamp: str) -> float | None:
    """How long ago that timestamp was, in seconds; ``None`` if it is not one."""
    try:
        written = datetime.fromisoformat(stamp)
    except ValueError:
        log.warning("Cannot read a job record's timestamp: %r", stamp)
        return None
    if written.tzinfo is None:
        written = written.replace(tzinfo=UTC)
    return (datetime.now(UTC) - written).total_seconds()


def _interrupted(record: JobRecord, cause: str, config: Config) -> JobRecord:
    """Close a job nothing is running any more, under this session, saying why."""
    interrupted = JobInterruptedError(
        cause=cause,
        detail={
            "job_id": record.job_id,
            "pid": record.pid,
            "progress": record.progress,
            "step": record.step,
        },
    )
    record.session = SESSION
    return finish(record, error=interrupted.payload(), config=config)
