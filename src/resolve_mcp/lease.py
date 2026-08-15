"""Is the owner of this claim still alive? One answer, for every kind of claim there is.

Two things in this server are held across processes, and both used to reason about their
owners on their own. A **stems directory** is claimed by a file naming the separation writing
it (``audio/stems``). A **detached job** is claimed by the record its worker writes
(``jobs/lifecycle``, ``jobs/store``). Neither is a lock the OS keeps for us — a detached
worker outlives the server that started it, so disk is the only thing the two share — and both
therefore ask exactly the same question of what they find: *is the process that wrote this
still working, or is this what a dead run left behind?*

``liveness`` is that question, as a total function of what the claim says (a pid, a session, a
silence) plus whatever the injected ``alive`` answers about that pid. Nothing here reads a
clock or a process table by itself, which is what makes the truth table testable in memory.
Four things make a claim adoptable and all four are the same in both callers:

* **The session says it is our own leftover.** Every claim carries the id of the process that
  wrote it. A claim whose session is this one, found by a caller that is not already holding
  it, is what a run of ours that died mid-write leaves behind — nothing is going to finish it.
* **The pid is now us.** The OS reissues pids, so a claim from a dead run can end up naming
  this very process. That used to read as "we are already working on it" and lock a directory
  out for the life of the server.
* **The pid names nothing.** The ordinary crash: a worker killed at the 50% mark, whose claim
  would otherwise lock every later run out of the work it was doing.
* **The claim has gone quiet for longer than the ceiling.** The last way a claim outlives its
  run: a reissued pid that belongs to a live stranger, which no liveness check can see through.
  What ages is silence rather than runtime — a run that is working says so (``beat``), a
  worker writes its record as it goes — so a ceiling far longer than any real job cannot
  close a live one. It is the only rule that can clear a claim whose pid answers, and after a
  reboot, when every number on the machine has been handed out again, it is the only rule left.

On top of that answer this module owns **the claim file protocol itself**: take it, hold it,
keep it young, release it. ``claim`` is the whole of it and ``holder`` is the reading behind
it, so a caller writes policy — how long is too long, how long to wait, what to say when it
refuses — and nothing about links, scratch names, sharing violations or recycled pids.

The exception is deliberately this module's own and deliberately plain: a caller that refuses
in its own vocabulary (``SeparationInProgressError`` for stems) catches ``LeaseHeld`` and says
so, and the lease never learns what the claim is for.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, NamedTuple

from .logging_config import get_logger
from .sharing import sharing

log = get_logger("lease")

SESSION = uuid.uuid4().hex
"""This server process. Anything written under another session predates a restart.

Here rather than with either kind of claim because both write it and both read it: a job
record carries it to tell a restart from a running job, a stems claim carries it to tell our
own leftover from a rival's live work. One uuid per process, made once.
"""

Alive = Callable[[int], bool]
"""Whether that pid names a running process. Injected: only the caller knows what it started.

``jobs.store.pid_alive`` is what both callers pass — it answers for a worker this process
started from the handle it kept, and asks the OS about everything else — and it is passed
rather than imported so that this module can be run against a dictionary.
"""

Waiting = Callable[[int | None], None]
"""Told, each time round, which process is being waited for. See ``claim``."""

Read = Callable[[Path], bytes]
"""How a claim's bytes come off disk. Injected so a refused read is a test, not a monkeypatch."""

_UNREADABLE = b""
"""What ``_bytes`` gives back for a claim that is there but could not be read.

Not the same answer as ``None``, which is no claim at all: a claim nothing can read is still
somebody's until it ages out, while a missing one is free for the taking. Empty bytes rather
than a sentinel of its own because a claim file with nothing in it — the window the fallback
create leaves open — is unreadable in exactly that way and wants exactly that answer.
"""


class Liveness(StrEnum):
    """What the claim's own fields say about the run that wrote it. One is held; five are not."""

    UNOWNED = "unowned"
    """The claim names no process at all."""

    OURS = "ours"
    """This process wrote it and is not holding it — a run of ours that died mid-write."""

    RECYCLED = "recycled"
    """Another session wrote it, and its pid has since been reissued to this process."""

    GONE = "gone"
    """The pid names no running process."""

    SILENT = "silent"
    """A live pid on a claim nothing has written for longer than the work can go quiet."""

    HELD = "held"
    """Somebody is working on it."""


@dataclass(frozen=True)
class Owner:
    """What a claim says about whoever wrote it, however that claim is spelled on disk.

    A stems claim spells it as JSON in a marker file; a job record spells it as record fields.
    Both arrive here as these three, and everything past this point is the same reasoning.
    """

    pid: int | None = None
    """The process that wrote the claim, when it named one legibly."""

    session: str | None = None
    """The process id of the *server* that wrote it, or ``None`` where session is not the
    question — a detached worker outlives the session that launched it on purpose, so judging
    one by session would fail the job the detached path exists to protect."""

    silence: float | None = None
    """Seconds since the claim was last written; ``None`` when that cannot be read.

    Unknown is not stale: a timestamp nothing can parse is no evidence that the run behind it
    stopped, and closing a live job on it is the expensive direction."""


def liveness(
    owner: Owner,
    *,
    ceiling: float,
    alive: Alive,
    self_pid: int | None = None,
    self_session: str = SESSION,
) -> Liveness:
    """Whether the run that wrote this claim is still working, and if not, how we know.

    The rules are asked in the order they are cheap and certain: what the claim says about
    itself first, the process table second, the clock last. ``self_pid`` and ``self_session``
    are parameters rather than reads so that a test can be two processes at once.
    """
    if owner.pid is None:
        return Liveness.UNOWNED
    if owner.session is not None:
        if owner.session == self_session:
            return Liveness.OURS
        if owner.pid == (os.getpid() if self_pid is None else self_pid):
            return Liveness.RECYCLED
    if not alive(owner.pid):
        return Liveness.GONE
    if owner.silence is not None and owner.silence > ceiling:
        return Liveness.SILENT
    return Liveness.HELD


class Held(NamedTuple):
    """What the claim on disk says, as taking it needs to hear it."""

    held: bool
    """Somebody is working under this claim right now — wait rather than take over."""

    pid: int | None
    """Which process, when the claim names one legibly."""

    judged: bytes | None
    """The exact bytes this verdict was reached on, so a stale claim is cleared by identity."""


class Reason(StrEnum):
    """Why a lease was refused. The caller's error wording turns on this, nothing else does."""

    RIVAL = "rival"
    """Another process holds the claim."""

    THREAD = "thread"
    """Another thread of this process holds it — the file cannot separate those."""

    LOST = "lost"
    """A run that was holding the claim found it is somebody else's now."""


class LeaseHeld(Exception):
    """Somebody else has this claim. Caught by the caller and said in the caller's own words."""

    def __init__(self, path: Path, pid: int | None, reason: Reason) -> None:
        super().__init__(f"{path} is claimed by pid {pid} ({reason})")
        self.path = path
        self.pid = pid
        self.reason = reason


_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()
"""One lock per claim path, for the threads of *this* process. See ``claim``."""


def _local_lock(path: Path) -> threading.Lock:
    """The lock this process uses for that claim, made once and kept."""
    key = os.path.normcase(str(path.resolve()))
    with _locks_lock:
        return _locks.setdefault(key, threading.Lock())


@contextmanager
def claim(
    path: Path,
    *,
    ceiling: float,
    refresh: float,
    alive: Alive,
    waiting: Waiting | None = None,
    budget: float = 0.0,
    poll: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
    read: Read | None = None,
) -> Iterator[Callable[[], None]]:
    """Hold this claim for one run at a time, and yield the callable that keeps it young.

    Two locks, because there are two kinds of rival. A file for the other *processes*: a
    detached worker outlives the server that started it, and disk is the only thing those
    share. The claim is written under a name of this process's own and then *linked* into
    place, so the winner is the one whose link the filesystem accepted rather than whoever read
    an empty directory last — and what appears under the claim's name is a complete claim,
    never the empty file an exclusive create publishes before its content lands. And a plain
    in-process lock for the other *threads*, which the file cannot separate at all: they share
    a pid, so each would read the other's claim as its own and both would walk straight in.

    A claim nobody is working under is taken over rather than waited on (see ``liveness``); a
    claim that is merely unreadable is waited on, because the one thing that reliably makes a
    claim unreadable is being read at the moment it is written.

    A rival that is genuinely working is **waited out** whenever the caller passes ``waiting``
    — the callback that says the wait is still going, so a job with a progress bar never reads
    as hung — for up to ``budget`` seconds, looking again every ``poll``. A caller with nowhere
    to report a wait leaves it off and is refused at once, naming the holder either way.

    ``refresh`` is how often the yielded callable actually rewrites the claim: it is wired to
    whatever reports from inside the work, which moves several times a second, while the claim
    only has to stay younger than a ceiling measured in hours. The clock starts now rather than
    at zero, because the claim was written a moment ago.
    """
    reader = Path.read_bytes if read is None else read
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _local_lock(path)
    deadline = time.monotonic() + budget
    _hold_locally(lock, path, waiting, deadline, poll, sleep)
    try:
        _take(path, ceiling, alive, reader, waiting, deadline, poll, sleep)
        try:
            yield _beat(path, refresh, reader)
        finally:
            _release(path, reader)
    finally:
        lock.release()


def holder(
    path: Path,
    *,
    ceiling: float,
    alive: Alive,
    read: Read | None = None,
) -> Held:
    """Whether this claim is still held, and by whom — or that it is there for the taking.

    A claim that cannot be read at all is the one case that is *not* adoptable. Reading it is a
    guess either way, and the two guesses are not symmetrical: read as adoptable, the likely
    cause — a claim caught mid-write, or a rival's write this filesystem has not shown us yet —
    costs a second run in a directory somebody is writing, which is the whole failure the claim
    exists to prevent. Read as held, an actually-corrupt claim costs a refused job until the
    ceiling ages it out, and the ceiling is what clears it.
    """
    reader = Path.read_bytes if read is None else read
    raw = _bytes(path, reader)
    if raw is None:
        return Held(held=False, pid=None, judged=None)
    parsed = _parse(path, raw)
    if parsed is None:
        return _unreadable(path, raw, ceiling)
    pid = parsed.get("pid")
    if not isinstance(pid, int):
        log.warning("Ignoring a claim that names no process at %s", path)
        return _unreadable(path, raw, ceiling)
    stamped = parsed.get("claimed_at")
    silence = time.time() - stamped if isinstance(stamped, int | float) else None
    session = parsed.get("session")
    owner = Owner(pid=pid, session=session if isinstance(session, str) else None, silence=silence)
    verdict = liveness(owner, ceiling=ceiling, alive=alive)
    if verdict is Liveness.HELD:
        return Held(held=True, pid=pid, judged=raw)
    log.info("Taking over the claim at %s from pid %s: it reads as %s", path, pid, verdict)
    return Held(held=False, pid=pid, judged=raw)


def _content() -> bytes:
    """The claim this process would write, as the bytes that go on disk."""
    return json.dumps({"pid": os.getpid(), "session": SESSION, "claimed_at": time.time()}).encode()


def _ours(claim: dict[str, Any] | None) -> bool:
    """Whether that claim is the one this process wrote — the question both writers ask.

    A refresh is a write onto the claim and a release is a delete of it, so both have to be
    sure it is still ours: a rewrite of somebody else's claim is the theft the file exists to
    prevent, and a delete of one is that theft with an extra step. Asked in one place because
    two spellings of "still ours" would be two chances to disagree about it.
    """
    return claim is not None and claim.get("session") == SESSION and claim.get("pid") == os.getpid()


def _hold_locally(
    lock: threading.Lock,
    path: Path,
    waiting: Waiting | None,
    deadline: float,
    poll: float,
    sleep: Callable[[float], None],
) -> None:
    """Take this process's own lock on the claim, waiting out the thread that holds it.

    The claim file cannot separate two threads of one process — they share a pid, so each reads
    the other's claim as its own and both walk in — and two jobs over the same work land on one
    claim by design. Waited out on the same terms as another process's claim, and for the same
    reason: the thread ahead is doing the work this one is about to ask for.
    """
    if lock.acquire(blocking=False):
        return
    if waiting is not None:
        log.info(
            "Waiting for another thread of pid %s to release the claim on %s", os.getpid(), path
        )
        while time.monotonic() < deadline:
            waiting(os.getpid())
            sleep(poll)
            if lock.acquire(blocking=False):
                log.info("Took the claim on %s over from the thread that held it", path)
                return
        log.warning("Gave up waiting for this process's own thread to release %s", path)
    raise LeaseHeld(path, os.getpid(), Reason.THREAD)


def _take(
    path: Path,
    ceiling: float,
    alive: Alive,
    read: Read,
    waiting: Waiting | None,
    deadline: float,
    poll: float,
    sleep: Callable[[float], None],
) -> None:
    """Win the claim, or say who holds it — waiting the holder out where there is a way to wait.

    A rival that is genuinely working is not on its own a reason to fail: what it is producing
    is usually what this run came for, and a caller that can report progress waits for it
    rather than refusing. Without ``waiting`` there is nowhere to say a wait is happening, so
    the refusal is immediate — and it names the holder either way, at the start or after the
    budget has gone.
    """
    verdict = _one_turn(path, ceiling, alive, read)
    if verdict is None:
        return
    if waiting is None:
        raise LeaseHeld(path, verdict.pid, Reason.RIVAL)
    log.info("Waiting for pid %s to release the claim on %s", verdict.pid, path)
    while time.monotonic() < deadline:
        waiting(verdict.pid)
        sleep(poll)
        again = _one_turn(path, ceiling, alive, read)
        if again is None:
            log.info("Took the claim on %s after waiting for pid %s", path, verdict.pid)
            return
        verdict = again
    log.warning("Gave up waiting for pid %s to release the claim on %s", verdict.pid, path)
    raise LeaseHeld(path, verdict.pid, Reason.RIVAL)


def _one_turn(path: Path, ceiling: float, alive: Alive, read: Read) -> Held | None:
    """One go at the claim: ``None`` once it is this run's, or what holds it if it is not.

    Reading first and writing after is two runs both finding an empty directory and both
    proceeding; the link is what makes the answer the filesystem's. A link that is refused is
    not yet a refusal to run: the claim on disk may be one nothing is holding, and then it is
    cleared and the link tried once more. Only once more per go — a second loser is a live
    rival, not a stale file, and a caller that means to wait comes back round anyway.
    """
    if _create(path):
        return None
    verdict = holder(path, ceiling=ceiling, alive=alive, read=read)
    if not verdict.held:
        _discard(path, verdict.judged, read)
        if _create(path):
            return None
        verdict = holder(path, ceiling=ceiling, alive=alive, read=read)
    return verdict


def _create(path: Path) -> bool:
    """Publish this process's claim only if nobody else has one; ``False`` means somebody does.

    Written under a name of this process's own and hard-linked into place, rather than created
    exclusively and then filled in. Both refuse a second winner, but an exclusive create
    publishes the claim's *name* before its content: a rival reading in that instant finds an
    empty file, reads it as unreadable, and the recovery for an unreadable claim — whatever it
    is — is being run against the winner's live claim rather than against a stale one. A link
    makes the name and the content appear together, so there is no such instant to read.

    A filesystem that refuses hard links altogether (a cache directory on exFAT) falls back to
    the exclusive create, which is still correct about who won; the empty-file window is then
    covered by ``holder`` reading an unreadable claim as held rather than adoptable.
    """
    scratch = _scratch(path)
    payload = _content()
    try:
        scratch.write_bytes(payload)
        try:
            os.link(scratch, path)
        except FileExistsError:
            return False
        except OSError as exc:
            log.warning(
                "This filesystem will not link a claim into place (%s); falling back to an "
                "exclusive create at %s",
                exc,
                path,
            )
            return _create_exclusively(path, payload)
    finally:
        scratch.unlink(missing_ok=True)
    log.info("Claimed %s for pid %s", path, os.getpid())
    return True


def _create_exclusively(path: Path, payload: bytes) -> bool:
    """The fallback for a filesystem with no hard links: create, then fill in."""
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(handle, "wb") as sink:
        sink.write(payload)
    log.info("Claimed %s for pid %s", path, os.getpid())
    return True


def _beat(path: Path, refresh: float, read: Read) -> Callable[[], None]:
    """The callable a run holding the claim reports through, rewriting it at most every
    ``refresh`` seconds."""
    last = time.monotonic()

    def beat() -> None:
        nonlocal last
        now = time.monotonic()
        if now - last < refresh:
            return
        last = now
        _touch(path, read)

    return beat


def _touch(path: Path, read: Read) -> None:
    """Say the claim is still being worked on, so the ceiling measures silence not runtime.

    The ceiling exists for a claim whose process is gone in a way the pid check cannot see, and
    it can only mean that if a claim that *is* being worked on stays young. Without this a run
    long enough to pass the ceiling — the whole case the ceiling is written for — has its own
    claim read as abandoned and a second run walks into the work it is still doing. Rewritten
    rather than ``utime``d because the age is read out of the claim's content, and only while
    the claim is still this process's to rewrite.

    Finding the claim is somebody else's stops the run rather than skipping the refresh. The
    claim is what says who may write these files, so a run that has lost it is a run writing
    into a directory another owns — the interleaving the claim exists to prevent, arrived at
    from the inside. Carrying on quietly would produce exactly the half-and-half set that looks
    complete to whatever reads it next, so the run is failed here instead, naming whoever holds
    the claim now.

    A refresh that cannot be *written* is only a claim left to age, so that stays a warning:
    the claim is still ours and the work still running, and the next reading tries again. A
    refresh whose claim cannot be *read* is the same warning for the same reason. Finding
    somebody else's claim is evidence; finding no answer at all is not, and reading it as a
    loss ended half an hour of GPU work every time the filesystem refused one read — which on
    Windows is a routine thing for it to do, and is why the read retries first.
    """
    raw = _bytes(path, read)
    if raw == _UNREADABLE:
        log.warning(
            "Could not read the claim at %s to refresh it; it is this run's until something "
            "legible says otherwise, so the work carries on",
            path,
        )
        return
    held = None if raw is None else _parse(path, raw)
    if not _ours(held):
        raise _lost(path, held)
    scratch = _scratch(path)
    try:
        scratch.write_bytes(_content())
        os.replace(scratch, path)
    except OSError:
        log.warning("Could not refresh the claim at %s", path)
        # Guarded in its own right: this runs on the path where writing that very file already
        # failed, and on Windows a scratch file another handle still holds refuses the unlink
        # too. Letting that out would end a running job over a leftover file nothing reads —
        # the scratch name carries this process's pid, so no other run reads it as a claim.
        try:
            scratch.unlink(missing_ok=True)
        except OSError:  # pragma: no cover - a scratch claim left behind is read by nothing
            log.debug("Could not clear the scratch claim at %s", scratch)


def _lost(path: Path, raw: dict[str, Any] | None) -> LeaseHeld:
    """Why a run stops mid-work: the claim it is writing under is not its own any more."""
    named = raw.get("pid") if raw is not None else None
    pid = named if isinstance(named, int) else None
    log.warning("The claim at %s is no longer this process's; stopping the work", path)
    return LeaseHeld(path, pid, Reason.LOST)


def _release(path: Path, read: Read) -> None:
    """Drop the claim, but only if it is still the one this process wrote."""
    raw = _bytes(path, read)
    parsed = None if raw is None else _parse(path, raw)
    if parsed is None:
        return
    if not _ours(parsed):
        log.info("Leaving the claim at %s alone: it is not the one this process wrote", path)
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not clear the claim at %s", path)
        return
    log.info("Released the claim at %s", path)


def _discard(path: Path, judged: bytes | None, read: Read) -> None:
    """Clear the exact claim that was judged abandoned — never whatever is there now.

    The gap between judging a claim and unlinking it is one in which the judged claim can be
    released and a rival's live one can appear under the same name: unlinking by name alone
    hands a second run into work the first is doing, which is what the claim exists to prevent.
    So the bytes are read back and matched first. That leaves a window of its own, narrower by
    the whole liveness check and unreachable on the ordinary path — the link in ``_create`` is
    what makes the ordinary path safe, and nothing but a claim already judged stale ever
    reaches this.
    """
    if judged is None:
        return
    current = _bytes(path, read)
    if current is None:
        return
    if current != judged:
        log.info("Leaving the claim at %s alone: it changed after it was judged abandoned", path)
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        log.warning("Could not clear the abandoned claim at %s", path)
        return
    log.info("Cleared the abandoned claim at %s", path)


def _scratch(path: Path) -> Path:
    """The name this process writes a claim under before it is linked into place."""
    return path.with_name(f"{path.name}.{os.getpid()}")


def _bytes(path: Path, read: Read) -> bytes | None:
    """The claim exactly as it is on disk, ``None`` for no claim, ``_UNREADABLE`` for no answer.

    Retried the way the store retries a record read, and for the same reason: Windows refuses
    the read that lands while another handle holds the file, and a claim has two handles on it
    whenever anything is looking — a rival deciding whether to wait, the run holding it
    refreshing once a minute. Believing one refusal is expensive at both ends: to a rival an
    unreadable claim reads as held, so work whose owner is long gone stays locked out; to the
    run holding it the same non-answer used to read as *lost*, which ended the job and the GPU
    time in it.
    """
    try:
        return sharing(lambda: read(path))
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("Could not read the claim at %s, even on a retry", path)
        return _UNREADABLE


def _parse(path: Path, raw: bytes) -> dict[str, Any] | None:
    """The claim as a record, or ``None`` if those bytes are not one."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        log.warning("Ignoring an unreadable claim at %s", path)
        return None
    if not isinstance(parsed, dict):
        log.warning("Ignoring a claim that is not a record at %s", path)
        return None
    return parsed


def _unreadable(path: Path, raw: bytes, ceiling: float) -> Held:
    """A claim that names no process: held while it is young, adoptable once it is not.

    Its age cannot come from its content — that is the part that could not be read — so it
    comes from the file's own timestamp, which is the moment it was linked into place.
    """
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        age = 0.0
    if age > ceiling:
        log.warning(
            "Taking over the claim at %s: it names no process and is %.1f hours old",
            path,
            age / 3600,
        )
        return Held(held=False, pid=None, judged=raw)
    log.info(
        "Waiting out the claim at %s: it names no process, which is what a claim being written "
        "right now looks like",
        path,
    )
    return Held(held=True, pid=None, judged=raw)
