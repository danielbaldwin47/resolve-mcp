"""The one lease: is the owner of this claim still alive, and who may take it over.

Two things in this server are claimed across processes — a stems directory and a detached
job's record — and both used to reason about their owners on their own. The truth table is
here, asked of ``liveness`` in memory: no clock, no process table, no disk. What follows it is
the claim file protocol itself, exercised through ``claim`` and ``holder`` with the liveness
answer and the read both injected, so a dead process, a recycled pid and a filesystem that
refuses a read are all arrangements rather than monkeypatches.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp import lease
from resolve_mcp.lease import Liveness, Owner

CEILING = 6 * 60 * 60
"""A ceiling in the shape both callers use one: far longer than the work it measures."""

BUDGET = 5.0
"""Long enough that a wait in these tests is bounded by its own arrangement, not the clock."""


def _gone(pid: int) -> bool:
    """Liveness for a claim whose process is not there any more."""
    return False


def _running(pid: int) -> bool:
    """Liveness for a claim whose pid answers — whoever that pid now belongs to."""
    return True


def _written_by(
    path: Path,
    pid: int,
    session: str = "another-server",
    claimed_at: float | None = None,
) -> Path:
    """A claim somebody else says they are working under."""
    path.parent.mkdir(parents=True, exist_ok=True)
    held: dict[str, Any] = {"pid": pid, "session": session}
    if claimed_at is not None:
        held["claimed_at"] = claimed_at
    path.write_text(json.dumps(held), encoding="utf-8")
    return path


def _reading(*answers: bytes | BaseException) -> lease.Read:
    """A filesystem that gives these answers in order, then reads the file for real."""
    remaining = list(answers)

    def read(path: Path) -> bytes:
        if remaining:
            answer = remaining.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        return path.read_bytes()

    return read


def test_a_claim_this_session_wrote_is_our_own_leftover() -> None:
    """Asked by a caller that is not holding it, so it is a run of ours that died mid-write.

    Nothing is going to finish it: the thread that was working under it is gone with the run.
    """
    owner = Owner(pid=os.getpid(), session=lease.SESSION, silence=0.0)

    assert liveness_of(owner) is Liveness.OURS


def test_a_claim_from_another_session_wearing_this_pid_is_a_recycled_number() -> None:
    """pids are reissued, and the one a dead run named can end up being ours.

    Read as our own it used to lock the work out for good behind an error saying this very
    process was already doing it. The session is what tells the two apart.
    """
    owner = Owner(pid=os.getpid(), session="another-server", silence=0.0)

    assert liveness_of(owner) is Liveness.RECYCLED


def test_a_claim_whose_process_is_gone_is_adoptable() -> None:
    """The ordinary crash: a worker killed at the 50% mark must not lock everyone else out."""
    owner = Owner(pid=4242, session="another-server", silence=0.0)

    assert liveness_of(owner, alive=_gone) is Liveness.GONE


def test_a_claim_that_has_gone_quiet_for_longer_than_the_ceiling_is_not_believed() -> None:
    """The last way a claim outlives its run: a reissued pid belonging to a live stranger.

    Nothing about that pid says it is not the owner, so the silence is the only evidence left
    — and it is silence rather than runtime, because a run that is working says so.
    """
    owner = Owner(pid=4242, session="another-server", silence=CEILING + 60)

    assert liveness_of(owner) is Liveness.SILENT


def test_a_live_pid_inside_the_ceiling_is_somebody_working() -> None:
    """The one reading that means wait: a live process that has written recently."""
    owner = Owner(pid=4242, session="another-server", silence=60.0)

    assert liveness_of(owner) is Liveness.HELD


def test_a_silence_that_cannot_be_read_is_no_evidence_that_the_run_stopped() -> None:
    """Unknown is not stale. Closing a live job over an unparseable timestamp is the expensive
    direction, and the claim's pid still answers."""
    owner = Owner(pid=4242, session="another-server", silence=None)

    assert liveness_of(owner) is Liveness.HELD


def test_a_claim_naming_no_process_is_owned_by_nobody() -> None:
    """A detached record with no worker pid yet, or a claim whose pid field is not one."""
    assert liveness_of(Owner(pid=None, session="another-server")) is Liveness.UNOWNED


def test_a_claim_judged_by_pid_alone_is_never_judged_by_its_session() -> None:
    """The detached path's whole point: a worker outlives the session that launched it.

    Its record carries the launching server's session, and reading that as "ours, therefore a
    leftover" would close the half-hour separation the detached path exists to protect. So the
    caller leaves the session out, and only the pid and the silence answer.
    """
    owner = Owner(pid=os.getpid(), session=None, silence=60.0)

    assert liveness_of(owner) is Liveness.HELD


def liveness_of(owner: Owner, alive: lease.Alive = _running) -> Liveness:
    """The reading, at the ceiling these tests share."""
    return lease.liveness(owner, ceiling=CEILING, alive=alive)


def test_the_holder_reading_says_held_or_free_and_names_who(tmp_path: Path) -> None:
    """The other half of the interface, asked on its own: who has this, if anyone.

    ``claim`` is what a run uses, but the reading behind it is a question worth asking without
    taking anything — and the three answers it gives are the three a caller acts on: nobody has
    it, somebody is working under it, or the run that wrote it is gone and it can be taken.
    """
    free = tmp_path / "free" / ".claim.json"
    taken = tmp_path / "taken" / ".claim.json"
    _written_by(taken, 4242, claimed_at=time.time())

    assert lease.holder(free, ceiling=CEILING, alive=_running) == lease.Held(False, None, None)

    working = lease.holder(taken, ceiling=CEILING, alive=_running)
    abandoned = lease.holder(taken, ceiling=CEILING, alive=_gone)

    assert (working.held, working.pid) == (True, 4242)
    assert (abandoned.held, abandoned.pid) == (False, 4242)
    assert abandoned.judged == taken.read_bytes(), "the verdict named bytes it had not read"


def test_a_claim_somebody_is_working_under_is_refused_and_names_them(tmp_path: Path) -> None:
    """Two runs writing one directory interleave into work that is neither run's."""
    path = tmp_path / "work" / ".claim.json"
    _written_by(path, 4242, claimed_at=time.time())

    with pytest.raises(lease.LeaseHeld) as raised, lease.claim(
        path, ceiling=CEILING, refresh=0.0, alive=_running
    ):
        pass  # pragma: no cover - the claim is refused on the way in

    assert raised.value.pid == 4242
    assert raised.value.reason is lease.Reason.RIVAL


def test_a_claim_whose_process_is_gone_is_taken_over_not_waited_on(tmp_path: Path) -> None:
    """A worker killed mid-run must not lock every later run out of its work."""
    path = tmp_path / "work" / ".claim.json"
    _written_by(path, 4242, claimed_at=time.time())

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_gone):
        held = json.loads(path.read_text(encoding="utf-8"))

    assert held["pid"] == os.getpid()
    assert held["session"] == lease.SESSION
    assert not path.exists(), "the claim outlived the run holding it"


def test_a_claim_this_process_left_behind_is_taken_back(tmp_path: Path) -> None:
    """Our own session, and no thread of ours holding the lock: a run of ours that died."""
    path = tmp_path / "work" / ".claim.json"
    _written_by(path, os.getpid(), session=lease.SESSION, claimed_at=time.time())

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running):
        held = json.loads(path.read_text(encoding="utf-8"))

    assert held["session"] == lease.SESSION
    assert not path.exists()


def test_a_claim_left_by_a_run_that_is_gone_is_taken_over_even_wearing_this_pid(
    tmp_path: Path,
) -> None:
    """A recycled pid that happens to be ours used to lock the work out permanently."""
    path = tmp_path / "work" / ".claim.json"
    _written_by(path, os.getpid(), claimed_at=time.time())

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running):
        held = json.loads(path.read_text(encoding="utf-8"))

    assert held["session"] == lease.SESSION


def test_a_claim_older_than_the_ceiling_is_taken_over_however_alive_its_pid_looks(
    tmp_path: Path,
) -> None:
    """The ceiling is the only rule that can clear a claim whose pid still answers."""
    path = tmp_path / "work" / ".claim.json"
    _written_by(path, 4242, claimed_at=time.time() - CEILING - 60)

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running):
        held = json.loads(path.read_text(encoding="utf-8"))

    assert held["pid"] == os.getpid()


def test_a_claim_that_cannot_be_read_is_waited_out_rather_than_taken_over(tmp_path: Path) -> None:
    """An empty claim is what a claim being written *right now* looks like.

    Read as abandoned it would have a rival clear the winner's fresh claim and walk in, which
    is the whole failure the claim exists to prevent.
    """
    path = tmp_path / "work" / ".claim.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"")

    with pytest.raises(lease.LeaseHeld) as raised, lease.claim(
        path, ceiling=CEILING, refresh=0.0, alive=_gone
    ):
        pass  # pragma: no cover - the claim is refused on the way in

    assert raised.value.pid is None
    assert path.read_bytes() == b"", "the claim being written was cleared"


def test_an_unreadable_claim_older_than_the_ceiling_is_still_taken_over(tmp_path: Path) -> None:
    """Held is not held for ever: a genuinely corrupt claim ages out like any other.

    Its age cannot come from its content — that is the part that could not be read — so it
    comes from the file's own timestamp.
    """
    path = tmp_path / "work" / ".claim.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{ half a claim")
    aged = time.time() - CEILING - 60
    os.utime(path, (aged, aged))

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_gone):
        held = json.loads(path.read_text(encoding="utf-8"))

    assert held["pid"] == os.getpid()


def test_a_claim_that_changed_after_it_was_judged_abandoned_is_left_alone(tmp_path: Path) -> None:
    """Two runs reading one abandoned claim is two runs deciding to clear it.

    The first clears it, wins the create and starts working; the second arrives a moment later
    with the same verdict and unlinks — by name — the claim the first is now holding. Nothing
    then refuses the second, and two runs write one directory. So the bytes that were judged
    are matched against the bytes on disk before anything is unlinked. Here the filesystem
    shows the stale claim once and the winner's live one from then on, which is that race.
    """
    path = tmp_path / "work" / ".claim.json"
    stale = json.dumps({"pid": 4242, "session": "a-dead-run", "claimed_at": time.time()})
    _written_by(path, 4242, claimed_at=time.time())
    path.write_text(stale, encoding="utf-8")
    live = json.dumps({"pid": 9001, "session": "the-winner", "claimed_at": time.time()}).encode()
    read = _reading(stale.encode(), live, live, live, live)

    with pytest.raises(lease.LeaseHeld) as raised, lease.claim(
        path, ceiling=CEILING, refresh=0.0, alive=lambda pid: pid == 9001, read=read
    ):
        pass  # pragma: no cover - the live claim is refused on the way in

    assert raised.value.pid == 9001
    assert path.read_text(encoding="utf-8") == stale, "a claim that had changed was cleared"


def test_a_read_the_filesystem_refused_for_an_instant_is_read_again_not_believed(
    tmp_path: Path,
) -> None:
    """A sharing violation is the file being read, not the work being held.

    Two handles land on a claim whenever anything is looking — a rival deciding whether to
    wait, the run holding it refreshing once a minute — and Windows refuses the read that
    arrives while the other side has it open. Believed the first time, that verdict reads an
    unreadable claim as held and costs this run work whose owner is long gone.
    """
    path = tmp_path / "work" / ".claim.json"
    _written_by(path, 4242, claimed_at=time.time())
    read = _reading(PermissionError("the file is open in another process"))

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_gone, read=read):
        held = json.loads(path.read_text(encoding="utf-8"))

    assert held["pid"] == os.getpid(), "one refused read locked this run out of a dead claim"


def test_a_claim_is_refreshed_by_the_run_that_is_holding_it(tmp_path: Path) -> None:
    """The ceiling has to measure silence, not runtime, or it steals from a live run.

    Six hours is longer than any pass, but a claim written once at the start and never touched
    again has a genuinely slow run's own claim read as abandoned while it is still writing.
    """
    path = tmp_path / "work" / ".claim.json"

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running) as beat:
        held = json.loads(path.read_text(encoding="utf-8"))
        aged = time.time() - CEILING - 60
        path.write_text(json.dumps({**held, "claimed_at": aged}), encoding="utf-8")

        beat()

        refreshed = json.loads(path.read_text(encoding="utf-8"))

    assert refreshed["claimed_at"] > aged + CEILING, "the claim was left at its old age"
    assert refreshed["pid"] == os.getpid()
    assert refreshed["session"] == lease.SESSION


def test_the_refresh_is_throttled_to_one_write_however_often_it_is_reported_to(
    tmp_path: Path,
) -> None:
    """It is wired to whatever reports from inside the work, which moves several times a second.

    The claim only has to stay younger than a ceiling measured in hours, so rewriting on every
    reading would be thousands of writes to say what one a minute already says. The clock
    starts at the claim rather than at zero, because the claim was written a moment ago.
    """
    path = tmp_path / "work" / ".claim.json"

    with lease.claim(path, ceiling=CEILING, refresh=60.0, alive=_running) as beat:
        written = json.loads(path.read_text(encoding="utf-8"))["claimed_at"]

        beat()
        beat()

        assert json.loads(path.read_text(encoding="utf-8"))["claimed_at"] == written


def test_a_claim_this_process_no_longer_holds_stops_the_run(tmp_path: Path) -> None:
    """A refresh is a write, and a write onto somebody else's claim is the theft it prevents.

    Not refreshing is only half of it: the claim says who may write these files, so a run that
    reads somebody else's is one whose output is landing in work another owns.
    """
    path = tmp_path / "work" / ".claim.json"

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running) as beat:
        theirs = json.dumps({"pid": 4242, "session": "another-server", "claimed_at": time.time()})
        path.write_text(theirs, encoding="utf-8")

        with pytest.raises(lease.LeaseHeld) as raised:
            beat()

        assert raised.value.pid == 4242
        assert raised.value.reason is lease.Reason.LOST
        assert path.read_text(encoding="utf-8") == theirs


def test_a_claim_that_vanished_under_a_running_run_also_stops_it(tmp_path: Path) -> None:
    """No claim is no better than a rival's: nothing is holding the work for this run."""
    path = tmp_path / "work" / ".claim.json"

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running) as beat:
        path.unlink()

        with pytest.raises(lease.LeaseHeld) as raised:
            beat()

        assert raised.value.pid is None
        assert raised.value.reason is lease.Reason.LOST


def test_a_refresh_that_cannot_be_written_leaves_the_run_going(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim that could not be rewritten is ours still, only older.

    Both the write and the cleanup of what it left behind are refused here — Windows refuses to
    unlink a file another handle holds, and that unlink runs inside the handler for the write
    that already failed. Unguarded it escapes and takes half an hour of GPU work with it, over
    a scratch file named for this process that nothing else ever reads.
    """
    path = tmp_path / "work" / ".claim.json"

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running) as beat:
        held = path.read_text(encoding="utf-8")

        def refuses(*args: Any, **kwargs: Any) -> None:
            raise PermissionError("the file is open in another process")

        monkeypatch.setattr(os, "replace", refuses)
        monkeypatch.setattr(Path, "unlink", refuses)

        beat()  # the run carries on

        monkeypatch.undo()
        assert path.read_text(encoding="utf-8") == held


def test_a_refresh_whose_read_is_refused_leaves_the_run_going(tmp_path: Path) -> None:
    """The other half: a claim that could not be *read* is this run's still.

    A read the filesystem refused used to arrive as "somebody else has it" — ending the run,
    and all the GPU time in it, over a file nothing had touched.
    """
    path = tmp_path / "work" / ".claim.json"
    refusing = _reading(*[PermissionError("the file is open in another process")] * 40)

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running, read=refusing) as beat:
        held = path.read_text(encoding="utf-8")

        beat()  # the run carries on

        assert path.read_text(encoding="utf-8") == held, "an unreadable claim was written over"


def test_a_second_thread_of_this_process_is_refused_the_claim_this_one_holds(
    tmp_path: Path,
) -> None:
    """The file names a pid, and two threads of one process share it.

    Both would read the claim as their own and walk in, which is the interleaving the claim
    exists to stop — and it is not a hypothetical: the server runs jobs on threads.
    """
    path = tmp_path / "work" / ".claim.json"
    refused: list[BaseException] = []

    def second_thread() -> None:
        try:
            with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running):
                pass  # pragma: no cover - the claim is refused on the way in
        except BaseException as exc:  # noqa: BLE001 - whatever it raised is the finding
            refused.append(exc)

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running):
        thread = threading.Thread(target=second_thread)
        thread.start()
        thread.join(timeout=BUDGET)

    assert len(refused) == 1, "a second thread walked into work already being done"
    held = refused[0]
    assert isinstance(held, lease.LeaseHeld)
    assert held.reason is lease.Reason.THREAD
    assert held.pid == os.getpid()


def test_a_run_waits_out_the_claim_already_under_way_rather_than_refusing_it(
    tmp_path: Path,
) -> None:
    """What the holder is producing is usually what the waiting run came for.

    A retry, a second agent, or the same call made twice all land here, so refusing tells a
    caller to go away empty-handed in the middle of the production of what it asked for.
    """
    path = tmp_path / "work" / ".claim.json"
    _written_by(path, 4242, claimed_at=time.time())
    watched: list[int | None] = []

    def waiting(pid: int | None) -> None:
        watched.append(pid)
        if len(watched) == 2:
            path.unlink()  # that run has finished and dropped its claim

    with lease.claim(
        path,
        ceiling=CEILING,
        refresh=0.0,
        alive=_running,
        waiting=waiting,
        budget=BUDGET,
        poll=0.0,
        sleep=lambda seconds: None,
    ):
        held = json.loads(path.read_text(encoding="utf-8"))

    assert watched == [4242, 4242], "the wait never reported who it was waiting for"
    assert held["pid"] == os.getpid(), "the run that waited never got the claim"


def test_a_wait_that_outlasts_its_budget_still_names_the_holder(tmp_path: Path) -> None:
    """Bounded: a rival that never finishes ends as the refusal it always was, not as a hang."""
    path = tmp_path / "work" / ".claim.json"
    _written_by(path, 4242, claimed_at=time.time())

    with pytest.raises(lease.LeaseHeld) as raised, lease.claim(
        path,
        ceiling=CEILING,
        refresh=0.0,
        alive=_running,
        waiting=lambda pid: None,
        budget=0.0,
        poll=0.0,
        sleep=lambda seconds: None,
    ):
        pass  # pragma: no cover - the claim is refused once the budget has gone

    assert raised.value.pid == 4242
    assert raised.value.reason is lease.Reason.RIVAL


def test_a_claim_that_is_not_ours_is_left_behind_when_the_run_ends(tmp_path: Path) -> None:
    """Releasing by name alone would drop the claim of whoever took the work over."""
    path = tmp_path / "work" / ".claim.json"
    theirs = json.dumps({"pid": 4242, "session": "another-server", "claimed_at": time.time()})

    with lease.claim(path, ceiling=CEILING, refresh=0.0, alive=_running):
        path.write_text(theirs, encoding="utf-8")

    assert path.read_text(encoding="utf-8") == theirs, "a rival's claim was released by name"
