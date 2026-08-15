"""The job-lifecycle verdict, as a truth table over records built in memory.

Every row here used to cost a disk round-trip: the only way to reach a verdict was
``store.save`` then ``store.load``, which decided *and* wrote the failure back. The decision
is now ``lifecycle.verdict``, a total function of the record, the clock and an injected
liveness callable — so the table below is the table, and nothing here touches a file.

The store's composition of it (writing the failure back, folding in the worker's last output,
letting go of the child handle) is verified where it belongs, in ``test_job_store`` and
``test_detached_jobs``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from resolve_mcp.jobs import lifecycle
from resolve_mcp.jobs.lifecycle import Outcome, verdict
from resolve_mcp.jobs.store import JobRecord

KIND = "separate_stems"
NOW = datetime(2026, 8, 14, 12, 0, 0, tzinfo=UTC)

WORKER = 4242
LAUNCHER = 4243
DEAD = 5150
"""Three pid-shaped numbers. Which of them is alive is whatever the test says it is."""

LAUNCH_STALL = 300.0
"""Comfortably past ``LAUNCH_WINDOW``, and a round number to read back out of the cause."""


class Liveness:
    """The liveness callable the verdict is given, remembering what it was asked about."""

    def __init__(self, *live: int) -> None:
        self.live = live
        self.asked: list[int] = []

    def __call__(self, pid: int) -> bool:
        self.asked.append(pid)
        return pid in self.live


def _record(
    state: str = lifecycle.RUNNING,
    *,
    detached: bool = False,
    session: str = lifecycle.SESSION,
    pid: int | None = None,
    launcher_pid: int | None = None,
    silence: float = 1.0,
    updated_at: str | None = None,
) -> JobRecord:
    """A record as the file would have said it, without the file.

    ``silence`` is how long ago the record was last written, in seconds before ``NOW`` — the
    only clock reading any of this depends on.
    """
    return JobRecord(
        job_id="separate-stems-abc123",
        kind=KIND,
        state=state,
        session=session,
        detached=detached,
        pid=pid,
        launcher_pid=launcher_pid,
        started_at=(NOW - timedelta(seconds=silence)).isoformat(),
        updated_at=updated_at or (NOW - timedelta(seconds=silence)).isoformat(),
        step="separating four stems (50%)",
        progress=0.5,
    )


TABLE = [
    pytest.param(
        _record(lifecycle.COMPLETED),
        (),
        Outcome.SETTLED,
        id="a finished job is not judged at all",
    ),
    pytest.param(
        _record(lifecycle.FAILED, detached=True, pid=WORKER, session="a-server-that-is-gone"),
        (),
        Outcome.SETTLED,
        id="a finished detached job is not judged by its dead pid",
    ),
    pytest.param(
        _record(lifecycle.COMPLETED, session="a-server-that-is-gone"),
        (),
        Outcome.SETTLED,
        id="a finished job from a previous server is left alone",
    ),
    pytest.param(
        _record(),
        (),
        Outcome.LIVE,
        id="a running job of this session's is running",
    ),
    pytest.param(
        _record(session="the-server-that-restarted"),
        (),
        Outcome.RESTARTED,
        id="a running job of a dead session's died with it",
    ),
    pytest.param(
        _record(detached=True, pid=WORKER),
        (WORKER,),
        Outcome.LIVE,
        id="a detached job whose worker is alive is running",
    ),
    pytest.param(
        _record(detached=True, pid=WORKER, session="the-server-that-launched-it"),
        (WORKER,),
        Outcome.LIVE,
        id="a detached job outlives the session that launched it",
    ),
    pytest.param(
        _record(detached=True, pid=WORKER),
        (),
        Outcome.WORKER_GONE,
        id="a detached job whose worker exited is over",
    ),
    pytest.param(
        _record(detached=True, pid=WORKER, silence=lifecycle.HEARTBEAT_CEILING - 1),
        (WORKER,),
        Outcome.LIVE,
        id="a long separation is not closed for taking its time",
    ),
    pytest.param(
        _record(detached=True, pid=WORKER, silence=lifecycle.HEARTBEAT_CEILING),
        (WORKER,),
        Outcome.LIVE,
        id="silence exactly at the ceiling is still believed",
    ),
    pytest.param(
        _record(detached=True, pid=WORKER, silence=lifecycle.HEARTBEAT_CEILING + 60),
        (WORKER,),
        Outcome.SILENT,
        id="a live pid that has written nothing past the ceiling is a reissued number",
    ),
    pytest.param(
        _record(detached=True, pid=WORKER, updated_at="not a timestamp"),
        (WORKER,),
        Outcome.LIVE,
        id="a record whose timestamp cannot be read is not closed on the strength of it",
    ),
    pytest.param(
        _record(detached=True, launcher_pid=LAUNCHER),
        (LAUNCHER,),
        Outcome.LIVE,
        id="a launch in flight is a running job",
    ),
    pytest.param(
        _record(detached=True, launcher_pid=None),
        (),
        Outcome.STALLED_LAUNCH,
        id="a pid-less record naming no launcher is a launch that never happened",
    ),
    pytest.param(
        _record(detached=True, launcher_pid=DEAD),
        (WORKER,),
        Outcome.STALLED_LAUNCH,
        id="a launcher that is gone leaves a record nothing will ever write",
    ),
    pytest.param(
        _record(detached=True, launcher_pid=LAUNCHER, silence=lifecycle.LAUNCH_WINDOW),
        (LAUNCHER,),
        Outcome.LIVE,
        id="a launch exactly at the window is still in flight",
    ),
    pytest.param(
        _record(detached=True, launcher_pid=LAUNCHER, silence=lifecycle.LAUNCH_WINDOW + 60),
        (LAUNCHER,),
        Outcome.STALLED_LAUNCH,
        id="a live launcher pid that has produced no worker is recycled or idle",
    ),
    pytest.param(
        _record(detached=True, launcher_pid=LAUNCHER, updated_at="not a timestamp"),
        (LAUNCHER,),
        Outcome.LIVE,
        id="an unreadable timestamp does not stall a launch whose launcher is alive",
    ),
]


@pytest.mark.parametrize(("record", "live", "expected"), TABLE)
def test_the_verdict_truth_table(
    record: JobRecord, live: tuple[int, ...], expected: Outcome
) -> None:
    """State, detached flag, session, the two pids and one clock reading decide all of it."""
    assert verdict(record, NOW, Liveness(*live)).outcome is expected


@pytest.mark.parametrize(("record", "live", "expected"), TABLE)
def test_a_verdict_closes_the_job_exactly_when_nothing_is_running_it(
    record: JobRecord, live: tuple[int, ...], expected: Outcome
) -> None:
    """``cause`` is the whole of what the store needs to write back, and it is set or it isn't."""
    call = verdict(record, NOW, Liveness(*live))

    assert call.closes is (expected not in (Outcome.SETTLED, Outcome.LIVE))
    assert (call.cause is not None) is call.closes


def test_a_settled_record_is_never_asked_about_a_pid() -> None:
    """The first question ends it: a job that is over cannot be judged by anybody's liveness."""
    asked = Liveness()
    record = _record(lifecycle.COMPLETED, detached=True, pid=WORKER, launcher_pid=LAUNCHER)

    verdict(record, NOW, asked)

    assert asked.asked == []


def test_a_detached_record_with_a_worker_pid_is_not_asked_about_its_launcher() -> None:
    """The launcher answers for one window only — record written, worker not yet adopted."""
    asked = Liveness(WORKER)
    record = _record(detached=True, pid=WORKER, launcher_pid=LAUNCHER)

    verdict(record, NOW, asked)

    assert asked.asked == [WORKER]


def test_the_verdict_leaves_the_record_exactly_as_it_found_it() -> None:
    """Deciding is not writing: the store composes the two, and only the store writes."""
    record = _record(session="the-server-that-restarted")
    before = record.payload()

    verdict(record, NOW, Liveness())

    assert record.payload() == before


def test_the_same_record_and_the_same_clock_reach_the_same_verdict() -> None:
    """Nothing under here reads a clock of its own — ``now`` is the only one."""
    record = _record(detached=True, pid=WORKER, silence=lifecycle.HEARTBEAT_CEILING + 60)

    first = verdict(record, NOW, Liveness(WORKER))
    second = verdict(record, NOW, Liveness(WORKER))

    assert first == second


@pytest.mark.parametrize(
    ("record", "live", "phrase"),
    [
        pytest.param(
            _record(session="the-server-that-restarted"),
            (),
            "The server restarted while separate_stems was running",
            id="restarted",
        ),
        pytest.param(
            _record(detached=True, pid=WORKER),
            (),
            f"exited before the job finished (pid {WORKER}).",
            id="worker gone",
        ),
        pytest.param(
            _record(detached=True, pid=WORKER, silence=lifecycle.HEARTBEAT_CEILING + 60),
            (WORKER,),
            f"so pid {WORKER} is a reissued number",
            id="silent",
        ),
        pytest.param(
            _record(detached=True, launcher_pid=DEAD),
            (),
            "exited before the worker was started",
            id="launcher gone",
        ),
        pytest.param(
            _record(detached=True, launcher_pid=LAUNCHER, silence=lifecycle.LAUNCH_WINDOW + 60),
            (LAUNCHER,),
            "never started one",
            id="launch stalled",
        ),
    ],
)
def test_the_cause_says_which_way_the_job_died(
    record: JobRecord, live: tuple[int, ...], phrase: str
) -> None:
    """What the agent reads on the failed record — one sentence per way a job stops running."""
    call = verdict(record, NOW, Liveness(*live))

    assert call.cause is not None
    assert phrase in call.cause


def test_a_stalled_launch_counts_the_silence_it_measured() -> None:
    """The number in the cause is the record's own age, not a constant."""
    record = _record(detached=True, launcher_pid=LAUNCHER, silence=LAUNCH_STALL)

    call = verdict(record, NOW, Liveness(LAUNCHER))

    assert call.cause is not None
    assert f"{LAUNCH_STALL:.0f} seconds" in call.cause
