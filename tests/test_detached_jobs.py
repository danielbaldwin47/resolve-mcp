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

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.audio.stems import KIND, detached_pass, separate_stems, separation_params
from resolve_mcp.config import Config, get_config
from resolve_mcp.errors import ChainedJobError, InternalError, InvalidRequestError
from resolve_mcp.jobs import cache, detached, store
from resolve_mcp.jobs import worker as job_worker
from resolve_mcp.jobs.runner import Detached, JobOutput, Progress, follow, start_job, wait_for
from resolve_mcp.jobs.store import JobRecord
from resolve_mcp.resolve.connection import get_connection

from .conftest import Attach
from .fakes import FakeSeparator, FakeTimeline, studio, with_a_mix, write_wav

FOUR_STEMS = ("vocals", "drums", "bass", "other")
SIX_DRUM_STEMS = ("kick", "snare", "toms", "hh", "ride", "crash")

WORKER_BUDGET = 90.0
"""Long enough for a cold interpreter start on a busy box; a hung worker still ends the test."""

POLL = 0.05


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
    """What ``Popen`` gives back, as far as this module is concerned: a pid."""

    def __init__(self, pid: int) -> None:
        self.pid = pid


@pytest.fixture
def separating() -> FakeSeparator:
    return FakeSeparator(FOUR_STEMS, SIX_DRUM_STEMS)


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
    config = Config.from_env(
        {
            "RESOLVE_MCP_CACHE": "C:/cache",
            "RESOLVE_MCP_AUDIO_SEPARATOR": "D:/tools/audio-separator.exe",
            "RESOLVE_MCP_DRUM_MODEL": "some-drum-model.ckpt",
            "RESOLVE_MCP_ALLOW_ANY_PYTHON": "1",
        }
    )

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


def test_a_process_that_has_exited_does_not_read_as_a_live_one() -> None:
    """The one question the whole recovery rule hangs on, asked of a real process."""
    pid = _a_pid_that_has_exited()

    assert store.pid_alive(pid) is False
    assert store.pid_alive(os.getpid()) is True


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

    def work(taken: JobRecord, progress: Progress) -> JobOutput:
        progress(0.5, "halfway")
        return JobOutput({"from": taken.plan}, (artifact,))

    monkeypatch.setattr(job_worker, "worker_for", lambda kind: work)
    finished = job_worker.run(record.job_id)

    assert finished.state == store.COMPLETED
    assert finished.result == {"from": {"audio": {"path": "mix.wav"}}}
    assert finished.pid == os.getpid()
    assert finished.session == store.SESSION
    assert cache.lookup("the-key") == {"from": {"audio": {"path": "mix.wav"}}}


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
    assert finished.state == store.FAILED, finished.step
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


def _a_pid_that_has_exited() -> int:
    with subprocess.Popen([sys.executable, "-c", "import sys; sys.exit(0)"]) as process:
        process.wait(timeout=WORKER_BUDGET)
        return process.pid


def _until_finished(job_id: str, budget: float = WORKER_BUDGET) -> JobRecord:
    """Poll the record the way an agent does, and give up rather than hang the suite."""
    deadline = time.monotonic() + budget
    record = store.load(job_id)
    while record.state == store.RUNNING and time.monotonic() < deadline:
        time.sleep(POLL)
        record = store.load(job_id)
    return record
