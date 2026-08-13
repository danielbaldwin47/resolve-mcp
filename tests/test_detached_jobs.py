"""Jobs that outlive the process that started them.

The gap this closes (G4): a stem separation is half an hour of GPU work on a full set, it
ran on a daemon thread, and twice the session that launched it ended at the 50% mark and
took it with it. The fix is a process of its own, and every decision in it — what the record
says at each step of the hand-off, which command and which environment the child gets, how a
reader tells a live worker from a dead one — is verified here with nothing spawned.

Three tests do spawn a real process, deliberately: one asks whether a pid that has exited
reads as gone (the whole recovery rule hangs on that answer, and on Windows it is a
``kernel32`` call rather than a signal), and one runs the real worker module end to end
against a real job record. Neither needs a GPU, a model or Resolve. What no seam here can
answer is whether a worker survives *this* process exiting — that is the live proof, and it
is why the Windows flags are asserted rather than assumed.

Note the pids the fake launcher hands back are this process's own: a record naming a pid
that is not running is a *failed* record by design, so a test that wanted a live detached
job and invented a number would be testing the recovery rule by accident.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.audio import stems
from resolve_mcp.audio.stems import (
    CLAIM,
    CLAIM_CEILING,
    KIND,
    claimed,
    detached_pass,
    separate_stems,
    separation_params,
)
from resolve_mcp.config import Config, get_config
from resolve_mcp.errors import (
    ChainedJobError,
    InternalError,
    InvalidRequestError,
    SeparationInProgressError,
)
from resolve_mcp.jobs import cache, detached, store
from resolve_mcp.jobs import worker as job_worker
from resolve_mcp.jobs.runner import (
    Detached,
    JobOutput,
    Progress,
    execute,
    follow,
    start_job,
    wait_for,
)
from resolve_mcp.jobs.store import JobRecord
from resolve_mcp.resolve.connection import get_connection

from .conftest import Attach
from .fakes import FakeSeparator, FakeTimeline, studio, with_a_mix, write_wav

FOUR_STEMS = ("vocals", "drums", "bass", "other")
SIX_DRUM_STEMS = ("kick", "snare", "toms", "hh", "ride", "crash")

WORKER_BUDGET = 90.0
"""Long enough for a cold interpreter start on a busy box; a hung worker still ends the test."""

POLL = 0.05

_WINDOWS_SYSTEM_PID = 4
"""The Windows ``System`` process: always running, and no normal token may query it."""


class FakeSpawn:
    """A launcher that starts nothing and remembers what it was asked to start."""

    def __init__(self, pid: int | None = None, watching: Callable[[], None] | None = None) -> None:
        self.pid = os.getpid() if pid is None else pid
        self.calls: list[tuple[list[str], Path, Config]] = []
        self._watching = watching

    def __call__(self, argv: list[str], destination: Path, config: Config) -> int:
        self.calls.append((list(argv), destination, config))
        if self._watching is not None:
            self._watching()
        return self.pid


class _FakeProcess:
    """What ``Popen`` gives back, as far as this module is concerned: a pid and a poll.

    ``poll`` is not optional garnish: ``store`` keeps the handle on every worker it starts and
    asks it whether that pid is still running, so a fake without one turns any later liveness
    question about its pid into an ``AttributeError`` in the middle of an unrelated test.
    """

    def __init__(self, pid: int, returncode: int | None = None) -> None:
        self.pid = pid
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


@pytest.fixture
def separating() -> FakeSeparator:
    return FakeSeparator(FOUR_STEMS, SIX_DRUM_STEMS)


@pytest.fixture(autouse=True)
def _forget_remembered_children() -> Iterator[None]:
    """``store._children`` is process-global, and pytest runs every test in one process.

    A test that hands the store a fake handle leaves it there for the rest of the session,
    where the next test to ask about that pid gets the previous test's answer. Cleared on
    both sides so the order tests run in cannot change what they read.
    """
    store._children.clear()
    yield
    store._children.clear()


# --- what the child is told ---------------------------------------------------------------


def test_the_command_names_this_interpreter_the_worker_module_and_the_job() -> None:
    argv = detached.command("separate_stems-abc123")

    assert argv == [sys.executable, "-m", "resolve_mcp.jobs.worker", "separate_stems-abc123"]


def test_a_windows_worker_is_detached_and_in_a_process_group_of_its_own() -> None:
    """The flags are the fix. A worker in the launcher's group dies with the launcher."""
    preferred, fallback = detached.spawn_options("win32")

    assert preferred["creationflags"] & detached.DETACHED_PROCESS
    assert preferred["creationflags"] & detached.CREATE_NEW_PROCESS_GROUP
    assert preferred["creationflags"] & detached.CREATE_BREAKAWAY_FROM_JOB
    assert (
        fallback["creationflags"] == detached.DETACHED_PROCESS | detached.CREATE_NEW_PROCESS_GROUP
    )


def test_a_posix_worker_gets_a_session_of_its_own() -> None:
    assert detached.spawn_options("linux") == [{"start_new_session": True}]


def test_the_workers_environment_carries_this_servers_config_not_the_machines() -> None:
    """A worker that wrote its record into a different cache is a job that never lands."""
    config = get_config()

    env = detached.child_env(config, {"RESOLVE_MCP_CACHE": "C:/somewhere/else", "PATH": "/usr/bin"})

    assert env["RESOLVE_MCP_CACHE"] == str(config.cache_dir)
    assert env["RESOLVE_MCP_STEM_MODEL"] == config.stem_model
    assert env["PATH"] == "/usr/bin"


def test_a_config_survives_the_trip_through_the_environment() -> None:
    """Every field set to something no default would produce, which is the whole test.

    A field ``to_env`` forgets comes back out of ``from_env`` as its default — so a config
    built from defaults round-trips equal *whether or not* the field was written, and the one
    bug this test exists to catch passes it. Only a value no default can produce can tell the
    difference, and the assertion below fails first if some field ever stops being one.
    """
    config = Config(
        script_api=Path("D:/resolve/api"),
        script_lib=Path("D:/resolve/fusionscript.dll"),
        cache_dir=Path("D:/cache"),
        log_level="DEBUG",
        allow_any_python=True,
        ffmpeg="D:/tools/ffmpeg.exe",
        audio_separator="D:/tools/audio-separator.exe",
        stem_model="some-stem-model.ckpt",
        drum_model="some-drum-model.ckpt",
        wind_model="some-wind-model.ckpt",
        default_render_preset="SomePreset",
        whisper_device="some-device",
        whisper_compute_type="some-compute-type",
    )
    default = Config.from_env({})
    same_as_a_default = [
        one.name
        for one in fields(Config)
        if getattr(config, one.name) == getattr(default, one.name)
    ]

    assert same_as_a_default == [], "a field at its default cannot prove to_env wrote it"
    assert Config.from_env(config.to_env()) == config


# --- the hand-off -------------------------------------------------------------------------


def test_the_record_says_detached_before_a_pid_exists_so_no_poller_calls_it_dead() -> None:
    """The window between the two saves is the one a poller must not read as a dead thread."""
    record = store.new_job(KIND, {})
    seen: list[JobRecord] = []
    spawn = FakeSpawn(watching=lambda: seen.append(store.load(record.job_id)))

    detached.launch(record, {"audio": {"path": "x.wav"}}, spawn=spawn)

    assert seen[0].detached is True
    assert seen[0].pid is None
    assert seen[0].state == store.RUNNING
    assert seen[0].plan == {"audio": {"path": "x.wav"}}


def test_a_handed_off_job_stays_running_and_names_the_process_that_has_it() -> None:
    record = store.new_job(KIND, {})
    spawn = FakeSpawn()

    pid = detached.launch(record, {"audio": {"path": "x.wav"}}, spawn=spawn)

    landed = store.load(record.job_id)
    assert (landed.state, landed.detached, landed.pid) == (store.RUNNING, True, pid)
    assert str(pid) in landed.step
    assert spawn.calls[0][0] == detached.command(record.job_id)
    assert spawn.calls[0][1] == detached.worker_log(record.job_id, get_config())


def test_a_worker_that_hands_off_neither_finishes_the_job_nor_caches_a_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hand-off is not an ending: the record stays open for the process that took it."""
    spawn = FakeSpawn()
    monkeypatch.setattr(detached, "_spawn", spawn)

    started = start_job(KIND, {}, lambda progress: Detached({"audio": {}}), "the-key")
    record = wait_for(str(started["job_id"]))

    assert record.state == store.RUNNING
    assert record.pid == spawn.pid
    assert cache.lookup("the-key") is None


def test_a_launch_the_operating_system_refuses_fails_the_job_rather_than_stranding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one ending worse than a job that dies with its server is a job that never ends."""

    def refuse(argv: list[str], destination: Path, config: Config) -> int:
        raise OSError("no such interpreter")

    monkeypatch.setattr(detached, "_spawn", refuse)

    started = start_job(KIND, {}, lambda progress: Detached({"audio": {}}))
    record = wait_for(str(started["job_id"]))

    assert record.state == store.FAILED
    assert record.error is not None
    assert "no such interpreter" in record.error["cause"]


def test_a_job_object_that_will_not_let_a_child_break_away_gets_one_that_stays_inside_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Windows refuses the create outright, so the flag has to be dropped and retried.

    A shell or CI runner that kills its job object on close is precisely where a worker most
    needs to break away — and a launch that failed instead would be worse than one that stays
    inside it, because the second-best answer is still a separation that runs.
    """
    tried: list[dict[str, Any]] = []

    def popen(argv: list[str], **keywords: Any) -> Any:
        tried.append({name: keywords[name] for name in ("creationflags",) if name in keywords})
        if len(tried) == 1:
            raise OSError(5, "Access is denied")
        return _FakeProcess(4242)

    monkeypatch.setattr(subprocess, "Popen", popen)

    pid = detached._spawn(
        ["python"], tmp_path / "worker.log", get_config(), detached.spawn_options("win32")
    )

    assert pid == 4242
    assert tried[0]["creationflags"] & detached.CREATE_BREAKAWAY_FROM_JOB
    assert not tried[1]["creationflags"] & detached.CREATE_BREAKAWAY_FROM_JOB


def test_a_launch_no_flags_can_start_says_what_to_do_instead(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def refuse(argv: list[str], **keywords: Any) -> Any:
        raise OSError(2, "The system cannot find the file specified")

    monkeypatch.setattr(subprocess, "Popen", refuse)

    with pytest.raises(InternalError) as raised:
        detached._spawn(
            ["python"], tmp_path / "worker.log", get_config(), detached.spawn_options("win32")
        )

    assert "detach=false" in raised.value.fix


# --- who is still alive --------------------------------------------------------------------


def test_the_scratch_file_a_save_writes_through_is_this_processs_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One record, two writers: the atomic write is only atomic if they do not share a name.

    The server writes this record up to the hand-off and the detached worker from the adopt
    on, and for a moment both do. With one scratch name they write the same file and each
    moves it into place, so what lands is a splice of two records rather than either one.
    """
    target = get_config().job_dir / "separate_stems-abc123.json"
    ours = store._scratch(target)
    monkeypatch.setattr(os, "getpid", lambda: 424242)

    theirs = store._scratch(target)

    assert ours != theirs
    assert str(os.getpid()) in theirs.name
    assert ours.suffix != ".json", "a scratch file must never be read back as a record"


def test_a_launcher_does_not_write_over_a_worker_that_adopted_first() -> None:
    """Both processes write this record, and for one moment they both mean to write the pid.

    The worker adopts as its first act; the launcher's second save lands after that on a slow
    start. Saving the parent's copy blindly would put the launcher's pid, the launcher's
    session and the hand-off step back over the worker's own — leaving a record that names a
    process which will never write to it again, and a step frozen at the hand-off.
    """
    record = store.new_job(KIND, {})
    worker_pid = os.getpid()

    def adopts() -> None:
        taken = store.peek(record.job_id)
        assert taken is not None
        taken.session = "the-worker-that-got-there-first"
        taken.pid = worker_pid
        taken.step = "separating four stems (2%)"
        store.save(taken)

    detached.launch(record, {}, spawn=FakeSpawn(pid=424242, watching=adopts))

    landed = store.load(record.job_id)
    assert landed.pid == worker_pid
    assert landed.session == "the-worker-that-got-there-first"
    assert landed.step == "separating four stems (2%)"


def test_a_launcher_does_not_reopen_a_job_its_worker_had_already_finished(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worst version of that race: the worker ends the job *between* the read and the write.

    A short job — a cache hit, an import that fails in the first second — can be over before
    the launcher gets back from ``Popen``. A pid written from the launcher's copy then puts
    ``running`` and a launcher's pid back over a record the worker has already closed, and the
    worker never writes again: that job polls as running for the rest of the server's life.
    The check is therefore re-read immediately before the record is moved into place, and this
    ends the job inside exactly that window.
    """
    record = store.new_job(KIND, {})
    original = store._scratch
    ended: list[bool] = []

    def ends_the_job_first(target: Path) -> Path:
        if not ended:
            ended.append(True)
            taken = store.peek(record.job_id)
            assert taken is not None
            taken.pid = os.getpid()
            store.finish(taken, error={"code": "job_failed", "cause": "the worker gave up"})
        return original(target)

    def arm() -> None:
        monkeypatch.setattr(store, "_scratch", ends_the_job_first)

    detached.launch(record, {}, spawn=FakeSpawn(pid=424242, watching=arm))

    landed = store.load(record.job_id)
    assert landed.state == store.FAILED
    assert landed.error is not None
    assert landed.error["cause"] == "the worker gave up"


def test_a_worker_that_finished_inside_the_launchers_window_keeps_its_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The completed half of that race, and the expensive one: a result is not recoverable.

    A guard cannot close this window — reading the record and replacing it are two calls, and
    a worker that finishes between them is overwritten with no trace, because afterwards disk
    says exactly what the launcher put there. So the launcher writes no record at all: its pid
    reading goes to a note beside it, which is read only while the record has nothing better
    to say. Here the worker completes with a result inside the window; the record it wrote is
    what a poll must still find, half an hour of GPU work being what is on the other side of
    it.
    """
    record = store.new_job(KIND, {})
    replacing = os.replace
    ended: list[bool] = []

    def finishes_the_job_first(source: Any, target: Any) -> None:
        # Inside the window itself: whatever the launcher checked, it checked before this, and
        # this is the call that would put its copy of the record on top of the worker's.
        if not ended:
            ended.append(True)
            taken = store.peek(record.job_id)
            assert taken is not None
            taken.pid = os.getpid()
            store.finish(taken, result={"directory": "/stems/sunset-set-abc123"})
        replacing(source, target)

    def arm() -> None:
        monkeypatch.setattr(os, "replace", finishes_the_job_first)

    detached.launch(record, {}, spawn=FakeSpawn(pid=424242, watching=arm))

    landed = store.load(record.job_id)
    assert ended, "the finish was never injected, so the window was never exercised"
    assert landed.state == store.COMPLETED
    assert landed.result == {"directory": "/stems/sunset-set-abc123"}
    assert landed.pid == os.getpid(), "the launcher's note answered for a record that had ended"


def test_a_detached_job_outlives_the_session_that_started_it() -> None:
    """The session rule is backwards for these: surviving the server is the whole point."""
    record = _running_under_a_dead_server(os.getpid())

    assert store.load(record.job_id).state == store.RUNNING


def test_a_detached_job_whose_worker_is_gone_is_failed_and_says_so() -> None:
    pid = _a_pid_that_has_exited()
    record = _running_under_a_dead_server(pid, step="separating four stems (50%)")

    failed = store.load(record.job_id)

    assert failed.state == store.FAILED
    assert failed.error is not None
    assert failed.error["code"] == "job_interrupted"
    assert str(pid) in failed.error["cause"]
    assert failed.error["detail"]["step"] == "separating four stems (50%)"
    assert store.load(record.job_id).state == store.FAILED  # written back, judged once


def test_a_follower_of_a_detached_job_hears_its_worker_died_not_that_a_thread_is_missing() -> None:
    """``alive`` only knows threads. A detached job has none, and is not thereby dead."""
    record = _running_under_a_dead_server(_a_pid_that_has_exited())

    with pytest.raises(ChainedJobError) as raised:
        follow(record.job_id, poll=0.0, sleep=lambda seconds: None)

    assert raised.value.code == "job_interrupted"


def test_a_launch_that_never_happened_is_closed_rather_than_left_running_forever() -> None:
    """No pid to ask about and no session left to run it: the one record nothing could close.

    ``pid: None`` means a launch in flight — true only while the process doing the launching
    is still between its two saves. Killed there, it leaves a record that skips the session
    rule for being detached and has no pid to judge, and every poll for the rest of time reads
    it as running.
    """
    record = store.new_job(KIND, {})
    record.detached = True
    record.session = "the-server-that-died-mid-launch"
    record.step = detached.HANDING_OFF
    store.save(record)

    failed = store.load(record.job_id)

    assert failed.state == store.FAILED
    assert failed.error is not None
    assert failed.error["code"] == "job_interrupted"
    assert "before the worker was started" in failed.error["cause"]
    assert store.load(record.job_id).state == store.FAILED  # written back, judged once


def test_a_launch_in_flight_in_this_session_is_still_a_running_job() -> None:
    """The other half of that rule: our own hand-off must not close itself mid-flight."""
    record = store.new_job(KIND, {})
    record.detached = True
    record.step = detached.HANDING_OFF
    store.save(record)

    assert store.load(record.job_id).state == store.RUNNING


def test_a_launch_in_flight_in_another_live_server_is_not_closed_as_one_that_never_happened() -> (
    None
):
    """A foreign session is not by itself a dead one — a cache directory can have two servers.

    The record is written before the spawn and the worker's pid after it, so any server
    reading the cache in between sees a detached job with no pid. Judging that by session
    alone fails a job whose worker is about to start, and the failure is written back: the
    worker then adopts a record that has already been closed. The launcher's own pid is the
    question that separates the two, and here it is a process that is plainly running.
    """
    record = store.new_job(KIND, {})
    record.detached = True
    record.session = "another-server-that-is-mid-spawn"
    record.launcher_pid = os.getpid()
    record.step = detached.HANDING_OFF
    store.save(record)

    assert store.load(record.job_id).state == store.RUNNING


def test_a_launch_whose_launcher_died_before_the_spawn_is_still_closed() -> None:
    """The other side of it: a launcher that is gone leaves a record nothing will ever write."""
    record = store.new_job(KIND, {})
    record.detached = True
    record.session = "a-server-that-died-mid-launch"
    record.launcher_pid = _a_pid_that_has_exited()
    record.step = detached.HANDING_OFF
    store.save(record)

    failed = store.load(record.job_id)

    assert failed.state == store.FAILED
    assert failed.error is not None
    assert "before the worker was started" in failed.error["cause"]


def test_a_launch_that_has_had_no_worker_far_longer_than_a_launch_takes_is_closed() -> None:
    """A live launcher pid is not an answer for ever: pids are reissued.

    The record left by a server that died mid-launch names a launcher that the OS is free to
    hand to somebody else, and a long-lived stranger wearing that number reads as a launch
    still in flight — for the life of the machine. What separates the two is that nothing is a
    launch for long: the spawn and the note that follows it are a second's work, so a record
    that still has no worker pid two minutes on is a launch that is not happening, whatever
    its launcher's number now belongs to.
    """
    record = store.new_job(KIND, {})
    record.detached = True
    record.session = "a-server-whose-pid-has-since-been-reissued"
    record.launcher_pid = os.getpid()
    record.step = detached.HANDING_OFF
    store.save(record)
    _last_written(record.job_id, store.LAUNCH_WINDOW + 60)

    failed = store.load(record.job_id)

    assert failed.state == store.FAILED
    assert failed.error is not None
    assert failed.error["code"] == "job_interrupted"
    assert "never started one" in failed.error["cause"]


def test_a_launch_in_flight_is_not_closed_for_being_a_few_seconds_old() -> None:
    """The other side of that ceiling: a cold interpreter start is not a dead launcher."""
    record = store.new_job(KIND, {})
    record.detached = True
    record.session = "another-server-that-is-mid-spawn"
    record.launcher_pid = os.getpid()
    record.step = detached.HANDING_OFF
    store.save(record)
    _last_written(record.job_id, store.LAUNCH_WINDOW / 2)

    assert store.load(record.job_id).state == store.RUNNING


def test_the_launcher_lets_go_of_a_worker_that_finished_the_job_itself() -> None:
    """``finish`` runs in the worker, where the handle dict is empty — so it releases nothing.

    Only the launching process holds a handle on the worker, and the job ending well is news
    it gets by reading the record. Without that, every normally-completed detached job leaves
    its handle behind: a zombie per job on POSIX, and a pid nobody else may reuse on Windows,
    for as long as the server lives.
    """
    record = store.new_job(KIND, {})
    record.detached = True
    record.session = "the-worker-that-ran-it"
    record.pid = 4242
    record.state = store.COMPLETED
    record.result = {"directory": "/stems/sunset-set-abc123"}
    store.save(record)
    store.remember_child(4242, _FakeProcess(4242, returncode=0))

    landed = store.load(record.job_id)

    assert 4242 not in store._children, "the launcher is still holding a worker that has exited"
    assert landed.result == {"directory": "/stems/sunset-set-abc123"}


def test_a_launcher_writes_the_pid_of_the_process_doing_the_launching() -> None:
    """It is on the record before the spawn or it is not there for the window it exists for."""
    record = store.new_job(KIND, {})
    seen: list[JobRecord] = []
    spawn = FakeSpawn(watching=lambda: seen.append(store.load(record.job_id)))

    detached.launch(record, {}, spawn=spawn)

    assert seen[0].launcher_pid == os.getpid()


def test_a_process_that_has_exited_does_not_read_as_a_live_one() -> None:
    """The one question the whole recovery rule hangs on, asked of a real process."""
    pid = _a_pid_that_has_exited()

    assert store.pid_alive(pid) is False
    assert store.pid_alive(os.getpid()) is True


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="POSIX truncates an exit status to its low byte, so 259 leaves as 3 and collides "
    "with nothing — the ambiguity being tested exists only on Windows",
)
def test_a_process_that_exited_with_259_is_not_mistaken_for_a_running_one() -> None:
    """259 is ``STILL_ACTIVE``, and also a perfectly legal exit code.

    On Windows a process that exited with it reports it forever, so an exit code read on its
    own says "running" about a worker that has been gone for an hour, and the job it was doing
    never gets closed. The wait is what separates the two, and this is the process that tells
    them apart. Run anywhere else the test still passed, but not for its own reason: the exit
    status came back as 3 and no ``STILL_ACTIVE`` was ever collided with.
    """
    process = subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(259)"])
    _EXITED.append(process)
    process.wait(timeout=WORKER_BUDGET)

    assert store.pid_alive(process.pid) is False


@pytest.mark.skipif(sys.platform != "win32", reason="the kernel32 path only exists on Windows")
def test_a_windows_process_this_token_may_not_query_still_reads_as_alive() -> None:
    """``ACCESS_DENIED`` is "there, and not yours to ask about" — never "gone".

    A worker started by another user, or by a server running elevated, refuses the query
    handle; reading that as a dead process would close a separation that is still running as
    interrupted, which is the exact verdict the pid rule exists to get right. The POSIX side
    has always read ``PermissionError`` as alive. pid 4 is the Windows ``System`` process:
    always running, and not a process a normal token gets to interrogate.
    """
    assert store.pid_alive(_WINDOWS_SYSTEM_PID) is True


def test_a_worker_this_process_started_is_judged_by_the_handle_it_left_behind() -> None:
    """A child nobody reaped is a zombie, and a zombie answers ``kill(pid, 0)`` as if alive.

    ``start_new_session`` takes the worker out of the process group, not out of the family, so
    on POSIX every detached worker is still this process's child. Asking the OS about a
    crashed one gets "running" back for as long as the server lives — the record would say
    ``running`` forever, which is precisely the failure the detached path was built to end.
    """
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    store.remember_child(process.pid, process)
    try:
        assert store.pid_alive(process.pid) is True
        process.terminate()

        assert _until_gone(process.pid) is False
        assert process.returncode is not None, "the handle was never polled, so nothing reaped it"
    finally:
        if process.poll() is None:  # pragma: no cover - only if the assertions above failed
            process.kill()
            process.wait(timeout=WORKER_BUDGET)


def test_a_recycled_pid_is_not_answered_for_by_the_dead_handle_that_used_to_wear_it() -> None:
    """The handle outlives the process, and the number is handed to somebody else.

    Only a *running* record is ever asked about, so a handle can sit in the dict long after
    its worker exited — and once that child is reaped the OS may reissue its pid. Answering
    "gone" from the stale handle would close a live separation as interrupted. This fake has
    exited and wears the pid of a process that is unarguably running: this one.
    """
    store.remember_child(os.getpid(), _FakeProcess(os.getpid(), returncode=0))

    assert store.pid_alive(os.getpid()) is True
    assert os.getpid() not in store._children, "the stale handle was kept to answer again"


def test_a_worker_that_has_exited_is_reaped_rather_than_carried_for_the_life_of_the_server() -> (
    None
):
    """One leaked handle per job is a zombie per job on POSIX, for as long as the server runs."""
    store.remember_child(4242, _FakeProcess(4242, returncode=0))

    store.remember_child(4243, _FakeProcess(4243))

    assert 4242 not in store._children, "the exited worker was never reaped"
    assert 4243 in store._children, "the running worker was reaped with it"


def test_a_job_that_ends_lets_go_of_the_handle_on_the_worker_that_ran_it() -> None:
    """After the record is closed nothing asks about that pid again, so nothing would poll it."""
    record = store.new_job(KIND, {})
    record.pid = 4242
    store.remember_child(4242, _FakeProcess(4242, returncode=0))

    store.finish(record, result={})

    assert 4242 not in store._children


# --- the worker process --------------------------------------------------------------------


def test_the_worker_takes_the_record_over_closes_it_and_caches_what_it_made(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact = write_wav(tmp_path / "cache" / "stems" / "drums.wav", seconds=0.1)
    record = store.new_job(KIND, {}, cache_key="the-key")
    record.detached = True
    record.session = "a-server-that-has-since-exited"
    record.plan = {"audio": {"path": "mix.wav"}}
    store.save(record)

    def work(taken: JobRecord, progress: Progress, config: Config) -> JobOutput:
        progress(0.5, "halfway")
        return JobOutput({"from": taken.plan}, (artifact,))

    monkeypatch.setattr(job_worker, "worker_for", lambda kind: work)
    finished = job_worker.run(record.job_id)

    assert finished.state == store.COMPLETED
    assert finished.result == {"from": {"audio": {"path": "mix.wav"}}}
    assert finished.pid == os.getpid()
    assert finished.session == store.SESSION
    assert cache.lookup("the-key") == {"from": {"audio": {"path": "mix.wav"}}}


def test_the_worker_hands_the_work_the_config_it_was_started_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One config, or the record and the stems land in two different caches.

    The worker builds its config from the environment its launcher handed it and does its
    record IO through that. Work that called ``get_config()`` for itself would be reading the
    process default — the same object in this test only by accident, and a different cache
    directory in the one case detaching exists for.
    """
    config = replace(get_config(), stem_model="the-model-this-worker-was-told-to-use")
    record = store.new_job(KIND, {}, config=config)
    seen: list[Config] = []

    def work(taken: JobRecord, progress: Progress, given: Config) -> JobOutput:
        seen.append(given)
        return JobOutput({"model": given.stem_model})

    monkeypatch.setattr(job_worker, "worker_for", lambda kind: work)
    finished = job_worker.run(record.job_id, config)

    assert seen == [config]
    assert finished.result == {"model": "the-model-this-worker-was-told-to-use"}


def test_the_worker_refuses_a_kind_it_cannot_run_by_failing_the_job() -> None:
    """Left running, that record would say "separating" until someone read the log."""
    record = store.new_job("beat_grid", {})

    finished = job_worker.run(record.job_id)

    assert finished.state == store.FAILED
    assert finished.error is not None
    assert "No detached worker" in finished.error["cause"]


def test_the_worker_leaves_a_job_that_already_ended_alone() -> None:
    record = store.finish(store.new_job(KIND, {}), result={"done": True})

    assert job_worker.run(record.job_id).result == {"done": True}


def test_a_worker_that_hands_off_again_runs_the_work_itself_instead_of_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``execute`` is shared, so the hand-off branch is reachable inside the worker too.

    One worker per generation is what the unguarded version does: the detached process runs
    the same ``execute``, sees the same ``Detached``, and launches another process holding the
    same record. The record says which side of the hand-off we are on, and on the far side the
    plan is run in this process.
    """
    record = store.new_job("beat_grid", {})
    record.detached = True
    record.pid = os.getpid()
    store.save(record)
    spawn = FakeSpawn()
    monkeypatch.setattr(detached, "_spawn", spawn)
    plans: list[dict[str, Any]] = []

    def inline(taken: JobRecord, progress: Progress, config: Config) -> JobOutput:
        plans.append(taken.plan or {})
        return JobOutput({"ran": "in the worker"})

    monkeypatch.setattr(job_worker, "worker_for", lambda kind: inline)

    execute(record, lambda progress: Detached({"audio": "already on disk"}), get_config())

    assert spawn.calls == []
    assert plans == [{"audio": "already on disk"}]
    assert store.load(record.job_id).result == {"ran": "in the worker"}


def test_the_stems_job_is_the_kind_the_worker_knows_how_to_run() -> None:
    assert job_worker.worker_for(KIND) is detached_pass
    with pytest.raises(InternalError):
        job_worker.worker_for("beat_grid")


def test_a_real_detached_worker_finds_the_record_and_closes_it() -> None:
    """The one end-to-end spawn: real command, real flags, real environment, real record.

    It uses a kind no worker can run, so the child does its whole job — adopt the record,
    look up the work, write a structured failure — in under a second and with no GPU. What is
    being proved is the plumbing: that ``python -m resolve_mcp.jobs.worker <id>`` starts, that
    it reads this test's cache directory out of the environment it was handed, and that what
    it writes there is a closed record.

    The pid on the finished record is the worker's own, written when it adopted the record,
    and it is not always the pid the launcher saw: a uv-managed venv's ``python.exe`` on
    Windows is a trampoline that runs the real interpreter as a child. That is why the worker
    writes its own rather than trusting the one handed to it.
    """
    record = store.new_job("beat_grid", {})

    detached.launch(record, {})

    finished = _until_finished(record.job_id)
    assert finished.state == store.FAILED, _why_the_worker_did_not_finish(finished)
    assert finished.error is not None
    assert "No detached worker" in finished.error["cause"]
    assert finished.pid is not None
    assert finished.pid != os.getpid()
    assert finished.session != store.SESSION
    assert detached.worker_log(record.job_id, get_config()).exists()


# --- the separation itself -----------------------------------------------------------------


def test_the_separation_hands_off_once_the_audio_is_on_disk_and_not_before(
    attach: Attach,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The acquisition drives Resolve, so it stays here; the passes are what leaves."""
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))
    spawn = FakeSpawn()
    monkeypatch.setattr(detached, "_spawn", spawn)

    started = separate_stems(get_connection(), detach=True)
    record = wait_for(str(started["job_id"]))

    assert record.state == store.RUNNING
    assert record.detached is True
    assert record.pid == spawn.pid
    assert record.plan is not None
    assert Path(str(record.plan["audio"]["path"])).exists()
    assert record.plan["audio"]["content_sha256"]
    assert record.plan["reuse"] is True
    assert spawn.calls[0][0][-1] == record.job_id


def test_a_detached_separation_cannot_be_handed_a_substituted_separator(
    attach: Attach,
    separating: FakeSeparator,
) -> None:
    """A function cannot travel on a record, and silently running the real one is worse."""
    attach(studio(timeline=with_a_mix(FakeTimeline("sunset-set v3", "59.94"))))

    with pytest.raises(InvalidRequestError):
        separate_stems(get_connection(), detach=True, runner=separating)


def test_the_detached_pass_runs_both_passes_from_the_record_alone(
    tmp_path: Path,
    separating: FakeSeparator,
) -> None:
    """No connection, no closure, no starter: everything it needs is what was written down."""
    record = _handed_off(tmp_path)
    plan: dict[str, Any] = record.plan or {}

    output = detached_pass(record, _ignored, runner=separating)

    assert set(output.result["stems"]) == set(FOUR_STEMS)
    assert output.result["audio"]["path"] == plan["audio"]["path"]
    assert all(Path(one).exists() for one in output.result["drums"].values())
    assert len(separating.calls) == 2


def test_a_second_worker_refuses_a_stems_directory_another_one_is_writing(tmp_path: Path) -> None:
    """Two separators in one directory interleave into stems that are neither run's.

    The directory is keyed by the audio and the models, so a retry, a second agent or a job
    started twice all land in the same one — and since G4 they are separate processes, which
    is why the claim is a file and why it names the pid holding it.
    """
    directory = tmp_path / "stems-abc123"

    with _a_live_process() as other:
        _claim_held_by(directory, other.pid)

        with pytest.raises(SeparationInProgressError) as raised, claimed(directory):
            pass  # pragma: no cover - the claim is refused on the way in

        assert raised.value.detail["pid"] == other.pid


def test_a_stems_claim_whose_process_is_gone_is_taken_over_not_waited_on(tmp_path: Path) -> None:
    """A worker killed at the 50% mark must not lock every later run out of its directory."""
    directory = tmp_path / "stems-abc123"
    _claim_held_by(directory, _a_pid_that_has_exited())

    with claimed(directory):
        held = json.loads((directory / CLAIM).read_text(encoding="utf-8"))

    assert held["pid"] == os.getpid()
    assert not (directory / CLAIM).exists()


def test_a_second_thread_of_this_process_is_refused_a_directory_this_one_is_separating(
    tmp_path: Path,
) -> None:
    """The file names a pid, and two threads of one server share it.

    Both would read the claim as their own and walk in, which is the interleaving the claim
    exists to stop — and it is not a hypothetical: the server runs jobs on threads, and two
    jobs over the same mix land in the same directory by design.
    """
    directory = tmp_path / "stems-abc123"
    refused: list[BaseException] = []

    def second_thread() -> None:
        try:
            with claimed(directory):
                pass  # pragma: no cover - the claim is refused on the way in
        except BaseException as exc:  # noqa: BLE001 - whatever it raised is the finding
            refused.append(exc)

    with claimed(directory):
        thread = threading.Thread(target=second_thread)
        thread.start()
        thread.join(timeout=WORKER_BUDGET)

    assert len(refused) == 1, "a second thread walked into a directory already being separated"
    assert isinstance(refused[0], SeparationInProgressError)
    assert refused[0].detail["pid"] == os.getpid()


def test_a_stems_claim_left_by_a_run_that_is_gone_is_taken_over_even_wearing_this_pid(
    tmp_path: Path,
) -> None:
    """pids are reissued, and the one it names can end up being ours.

    Then the claim is read as our own, which used to mean the directory was locked out for
    good behind an error saying this process was already separating into it. The session the
    claim carries is what tells the two apart, and it has been written since the claim
    existed without anything ever reading it.
    """
    directory = tmp_path / "stems-abc123"
    _claim_held_by(directory, os.getpid())

    with claimed(directory):
        held = json.loads((directory / CLAIM).read_text(encoding="utf-8"))

    assert held["session"] == store.SESSION
    assert not (directory / CLAIM).exists()


def test_a_stems_claim_older_than_any_separation_is_taken_over_however_alive_its_pid_looks(
    tmp_path: Path,
) -> None:
    """The last way a claim outlives its run: a recycled pid that belongs to a live stranger.

    Nothing about that pid says it is not the separator, so the age is the only evidence left.
    The ceiling is far longer than the longest measured pass, so crossing it cannot be a
    separation that is merely slow.
    """
    directory = tmp_path / "stems-abc123"

    with _a_live_process() as other:
        marker = _claim_held_by(directory, other.pid, claimed_at=time.time() - CLAIM_CEILING - 60)

        with claimed(directory):
            held = json.loads(marker.read_text(encoding="utf-8"))

    assert held["pid"] == os.getpid()


def test_a_claim_that_changed_after_it_was_judged_abandoned_is_left_alone(tmp_path: Path) -> None:
    """Two processes reading one abandoned claim is two processes deciding to clear it.

    The first clears it, wins the create and starts separating; the second arrives a moment
    later with the same verdict and unlinks — by name — the claim the first is now holding.
    Nothing then refuses the second, and two separators write one directory, which is the
    exact failure the claim exists to prevent. So the bytes that were judged are matched
    against the bytes on disk before anything is unlinked.
    """
    directory = tmp_path / "stems-abc123"
    marker = _claim_held_by(directory, _a_pid_that_has_exited())
    judged = marker.read_bytes()
    live = json.dumps({"pid": os.getpid(), "session": "the-winner", "claimed_at": time.time()})
    marker.write_text(live, encoding="utf-8")

    stems._discard(marker, judged)

    assert marker.exists(), "a live claim was cleared by a process judging a stale one"
    assert marker.read_text(encoding="utf-8") == live


def test_a_claim_that_cannot_be_read_is_waited_out_rather_than_taken_over(tmp_path: Path) -> None:
    """An empty claim is what a claim being written *right now* looks like.

    An exclusive create publishes the name before the content lands, so a rival reading in
    that instant sees zero bytes — and reading that as abandoned has it clear the winner's
    fresh claim and walk in. The claim is linked into place rather than created empty, so the
    window is gone on this filesystem; read as held, an unreadable claim also costs nothing on
    one where the fallback create is what ran.
    """
    directory = tmp_path / "stems-abc123"
    directory.mkdir(parents=True)
    (directory / CLAIM).write_bytes(b"")

    with pytest.raises(SeparationInProgressError) as raised, claimed(directory):
        pass  # pragma: no cover - the claim is refused on the way in

    assert raised.value.detail["pid"] is None
    assert (directory / CLAIM).read_bytes() == b"", "the claim being written was cleared"


def test_an_unreadable_claim_older_than_the_ceiling_is_still_taken_over(tmp_path: Path) -> None:
    """Held is not held for ever: a genuinely corrupt claim ages out like any other.

    Its age cannot come from its content — that is the part that could not be read — so it
    comes from the file's own timestamp.
    """
    directory = tmp_path / "stems-abc123"
    directory.mkdir(parents=True)
    marker = directory / CLAIM
    marker.write_bytes(b"{ half a claim")
    aged = time.time() - CLAIM_CEILING - 60
    os.utime(marker, (aged, aged))

    with claimed(directory):
        held = json.loads(marker.read_text(encoding="utf-8"))

    assert held["pid"] == os.getpid()


def test_a_claim_is_refreshed_by_the_run_that_is_holding_it(tmp_path: Path) -> None:
    """The ceiling has to measure silence, not runtime, or it steals from a live separation.

    Six hours is longer than any measured pass, but the claim is written once at the start and
    never touched again — so a separation that is genuinely slow (a full set, a busy box, a
    model downloading) has its own claim read as abandoned while it is still writing, and the
    ceiling built to rescue a dead run hands a second separator into a live one instead.
    """
    directory = tmp_path / "stems-abc123"

    with claimed(directory) as refresh:
        marker = directory / CLAIM
        held = json.loads(marker.read_text(encoding="utf-8"))
        aged = time.time() - CLAIM_CEILING - 60
        marker.write_text(json.dumps({**held, "claimed_at": aged}), encoding="utf-8")

        refresh()

        refreshed = json.loads(marker.read_text(encoding="utf-8"))

    assert refreshed["claimed_at"] > aged + CLAIM_CEILING, "the claim was left at its old age"
    assert refreshed["pid"] == os.getpid()
    assert refreshed["session"] == store.SESSION


def test_a_claim_this_process_no_longer_holds_is_not_refreshed(tmp_path: Path) -> None:
    """A refresh is a write, and a write onto somebody else's claim is the theft it prevents."""
    directory = tmp_path / "stems-abc123"

    with claimed(directory) as refresh:
        marker = directory / CLAIM
        theirs = json.dumps({"pid": 4242, "session": "another-server", "claimed_at": time.time()})
        marker.write_text(theirs, encoding="utf-8")

        refresh()

        assert marker.read_text(encoding="utf-8") == theirs


def test_the_bar_a_separation_reports_through_keeps_the_claim_young(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hung off the progress bar because that is the only thing reporting from inside a pass.

    Throttled because the bar moves several times a second and the claim only has to stay
    younger than a ceiling measured in hours.
    """
    refreshed: list[int] = []
    reported: list[tuple[float, str]] = []
    beat = stems._keeping_the_claim(
        lambda fraction, step: reported.append((fraction, step)),
        lambda: refreshed.append(1),
    )

    beat(0.1, "separating four stems (10%)")
    beat(0.2, "separating four stems (20%)")
    assert refreshed == [], "the claim was rewritten twice in a second"

    monkeypatch.setattr(stems, "CLAIM_REFRESH", 0.0)
    beat(0.3, "separating four stems (30%)")

    assert refreshed == [1]
    assert reported == [
        (0.1, "separating four stems (10%)"),
        (0.2, "separating four stems (20%)"),
        (0.3, "separating four stems (30%)"),
    ], "the bar stopped reporting once it had a claim to keep"


def test_a_separation_long_enough_to_pass_the_ceiling_still_owns_its_directory(
    tmp_path: Path,
    separating: FakeSeparator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresh, wired: the passes are what a real hour goes into, so they are what is asked.

    The claim is aged past the ceiling from inside the first pass — a fast way to be a slow
    separation — and the second pass is asked what the claim on disk says by then.
    """
    monkeypatch.setattr(stems, "CLAIM_REFRESH", 0.0)
    record = _handed_off(tmp_path)
    aged = time.time() - CLAIM_CEILING - 60
    stamps: list[float] = []

    def watching(argv: Sequence[str], on_line: Callable[[str], None]) -> int:
        out_dir = Path(argv[list(argv).index("--output_dir") + 1])
        marker = out_dir.parent / CLAIM
        held = json.loads(marker.read_text(encoding="utf-8"))
        stamps.append(float(held["claimed_at"]))
        if len(stamps) == 1:
            marker.write_text(json.dumps({**held, "claimed_at": aged}), encoding="utf-8")
        return separating(argv, on_line)

    detached_pass(record, _ignored, None, watching)

    assert len(stamps) == 2, "the two passes did not both run"
    assert stamps[1] > aged + CLAIM_CEILING, "the second pass inherited a claim past the ceiling"


def test_a_finished_separation_holds_the_claim_for_the_passes_and_leaves_none_behind(
    tmp_path: Path,
    separating: FakeSeparator,
) -> None:
    """Held for the passes and dropped at the end, however they end.

    The absence at the end proves nothing on its own — a separation that never claimed the
    directory at all leaves exactly the same empty directory behind. So the separator itself
    is asked, mid-pass, whether the claim is on disk while it is writing.
    """
    record = _handed_off(tmp_path)
    held: list[bool] = []

    def watching(argv: Sequence[str], on_line: Callable[[str], None]) -> int:
        out_dir = Path(argv[list(argv).index("--output_dir") + 1])
        held.append((out_dir.parent / CLAIM).exists())
        return separating(argv, on_line)

    output = detached_pass(record, _ignored, None, watching)

    assert held and all(held), "the separator ran without the directory being claimed"
    assert not (Path(str(output.result["directory"])) / CLAIM).exists()


def test_a_hand_off_that_lost_its_audio_is_a_named_failure_not_a_crash(tmp_path: Path) -> None:
    record = _handed_off(tmp_path)
    record.plan = {"reuse": True}

    with pytest.raises(InternalError):
        detached_pass(record, _ignored)


# --- helpers -------------------------------------------------------------------------------


def _ignored(fraction: float, step: str) -> None:
    """A progress callback for the tests that are not about progress."""


def _running_under_a_dead_server(pid: int, step: str = "") -> JobRecord:
    """A detached record left behind by a server that has since exited."""
    record = store.new_job(KIND, {})
    record.session = "a-server-that-has-since-exited"
    record.detached = True
    record.pid = pid
    record.step = step
    store.save(record)
    return record


def _handed_off(tmp_path: Path) -> JobRecord:
    """A record as the starter leaves it: params, cache key, and the audio it acquired."""
    audio = write_wav(tmp_path / "cache" / "audio" / "mix.wav", seconds=0.5)
    record = store.new_job(
        KIND,
        {"scope": "timeline", "timeline": "sunset-set v3", **separation_params()},
        cache_key="stems-key",
    )
    record.detached = True
    record.plan = {
        "audio": {
            "path": str(audio),
            "duration_seconds": 0.5,
            "content_sha256": cache.content_hash(audio),
            "scope": "timeline",
        },
        "reuse": True,
    }
    store.save(record)
    return record


_EXITED: list[subprocess.Popen[bytes]] = []
"""Every child ``_a_pid_that_has_exited`` made, kept open. See there for why."""


def _a_pid_that_has_exited() -> int:
    """A pid this process owned and watched exit — the only kind we can be sure has gone.

    Reaped rather than left a zombie: a zombie still answers ``kill(pid, 0)``, so a test
    written on one would be asserting the opposite of what it reads as. The handle is then
    kept for the rest of the session, which on Windows is a guarantee — a pid is not handed to
    anybody else while a handle on it is open — and on POSIX is the receipt that makes a
    failure here readable: ``returncode`` says this pid was our child and that it exited, so a
    red test means the number was recycled underneath us, not that ``pid_alive`` is broken.
    """
    process = subprocess.Popen([sys.executable, "-c", ""])
    _EXITED.append(process)
    process.wait(timeout=WORKER_BUDGET)
    assert process.returncode is not None, "the helper's own child never exited"
    return process.pid


@contextlib.contextmanager
def _a_live_process() -> Iterator[subprocess.Popen[bytes]]:
    """A process that is certainly running, and is certainly not this one.

    A sleep rather than a read on stdin: under pytest the child would inherit a stdin that is
    already at end of file, and a "live" process that exited immediately would make every
    assertion resting on it pass for the wrong reason.
    """
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    try:
        yield process
    finally:
        process.terminate()
        process.wait(timeout=WORKER_BUDGET)


def _last_written(job_id: str, seconds_ago: float) -> None:
    """Age a record on disk, the way a launch that never happened ages by sitting there.

    Written straight into the file because every path through ``store`` stamps ``updated_at``
    with now — which is the point of the field, and the reason a test cannot arrange this by
    saving a record with an old one on it.
    """
    path = get_config().job_dir / f"{job_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    written = datetime.now(UTC) - timedelta(seconds=seconds_ago)
    raw["updated_at"] = written.isoformat(timespec="microseconds")
    path.write_text(json.dumps(raw), encoding="utf-8")


def _claim_held_by(directory: Path, pid: int, claimed_at: float | None = None) -> Path:
    """A stems directory another process says it is separating into."""
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / CLAIM
    held: dict[str, Any] = {"pid": pid, "session": "another-server"}
    if claimed_at is not None:
        held["claimed_at"] = claimed_at
    marker.write_text(json.dumps(held), encoding="utf-8")
    return marker


def _until_gone(pid: int, budget: float = WORKER_BUDGET) -> bool:
    """Whether that pid still reads as alive once it has had time to stop."""
    deadline = time.monotonic() + budget
    while store.pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(POLL)
    return store.pid_alive(pid)


def _why_the_worker_did_not_finish(record: JobRecord) -> str:
    """The child's own output, which is the only place its failure was ever written.

    A bare assertion here reads "expected FAILED, got RUNNING" after a minute and a half of
    polling, and says nothing about the worker that did not start — a missing interpreter, an
    import error, a cache directory it could not write. That is all in the log file the
    launcher opened for it.
    """
    log_file = detached.worker_log(record.job_id, get_config())
    output = log_file.read_text(encoding="utf-8") if log_file.exists() else "(no worker log)"
    return f"job {record.job_id} is {record.state} at step {record.step!r}; worker log:\n{output}"


def _until_finished(job_id: str, budget: float = WORKER_BUDGET) -> JobRecord:
    """Poll the record the way an agent does, and give up rather than hang the suite."""
    deadline = time.monotonic() + budget
    record = store.load(job_id)
    while record.state == store.RUNNING and time.monotonic() < deadline:
        time.sleep(POLL)
        record = store.load(job_id)
    return record
