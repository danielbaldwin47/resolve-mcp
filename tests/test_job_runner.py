"""Starting a job and not waiting for it.

Every test here blocks the worker on an ``Event`` rather than sleeping, so "the starter
came back before the work did" is asserted, not timed. The one thing a stdio server can
never do is stall, so that is the property under test.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from resolve_mcp.config import get_config
from resolve_mcp.errors import MediaOperationError
from resolve_mcp.jobs import cache, store
from resolve_mcp.jobs.runner import JobOutput, Progress, start_job, wait_for


def test_the_starter_returns_before_the_work_finishes() -> None:
    release = threading.Event()

    def work(progress: Progress) -> JobOutput:
        release.wait(timeout=5)
        return JobOutput({"done": True})

    started = start_job("extract_audio", {"scope": "timeline"}, work)

    assert started["state"] == "running"
    assert store.load(started["job_id"]).state == "running"

    release.set()
    finished = wait_for(started["job_id"])

    assert finished.state == "completed"
    assert finished.result == {"done": True}


def test_progress_from_the_worker_is_visible_to_a_poller() -> None:
    reported = threading.Event()
    release = threading.Event()

    def work(progress: Progress) -> JobOutput:
        progress(0.5, "exporting the timeline mix")
        reported.set()
        release.wait(timeout=5)
        return JobOutput({})

    started = start_job("extract_audio", {}, work)
    assert reported.wait(timeout=5)

    polled = store.load(started["job_id"])
    assert polled.progress == pytest.approx(0.5)
    assert polled.step == "exporting the timeline mix"
    assert polled.state == "running"

    release.set()
    wait_for(started["job_id"])


def test_a_refusal_inside_a_worker_arrives_as_a_structured_error() -> None:
    def work(progress: Progress) -> JobOutput:
        raise MediaOperationError(cause="Resolve refused the render job.")

    record = wait_for(start_job("extract_audio", {}, work)["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "media_operation_failed"
    assert record.error["cause"] == "Resolve refused the render job."


def test_a_bug_in_a_worker_becomes_an_internal_error_not_a_lost_job() -> None:
    def work(progress: Progress) -> JobOutput:
        raise ValueError("off by one")

    record = wait_for(start_job("extract_audio", {}, work)["job_id"])

    assert record.state == "failed"
    assert record.error is not None
    assert record.error["code"] == "internal_error"
    assert "ValueError" in record.error["cause"]


def test_a_finished_job_writes_its_result_into_the_cache() -> None:
    artifact = _artifact("mix.wav")

    def work(progress: Progress) -> JobOutput:
        return JobOutput({"path": str(artifact)}, (artifact,))

    wait_for(start_job("extract_audio", {}, work, cache_key="key1")["job_id"])

    assert cache.lookup("key1") == {"path": str(artifact)}


def test_a_rerun_with_unchanged_media_and_params_never_starts_a_worker() -> None:
    artifact = _artifact("mix.wav")
    cache.remember("key1", "extract_audio", {"path": str(artifact)}, [artifact])

    def work(progress: Progress) -> JobOutput:
        raise AssertionError("a cache hit must not run the work")

    started = start_job("extract_audio", {}, work, cache_key="key1")

    assert started["state"] == "completed"
    assert started["cached"] is True
    assert started["result"] == {"path": str(artifact)}


def test_a_cache_hit_is_still_a_job_the_agent_can_poll_and_list() -> None:
    artifact = _artifact("mix.wav")
    cache.remember("key1", "extract_audio", {"path": str(artifact)}, [artifact])

    started = start_job("extract_audio", {}, _never_runs, cache_key="key1")

    assert store.load(started["job_id"]).cached is True
    assert [one.job_id for one in store.load_all()] == [started["job_id"]]


def test_a_failed_job_leaves_the_cache_alone() -> None:
    def work(progress: Progress) -> JobOutput:
        raise MediaOperationError(cause="Resolve refused the render job.")

    wait_for(start_job("extract_audio", {}, work, cache_key="key1")["job_id"])

    assert cache.lookup("key1") is None


def test_only_one_job_at_a_time_drives_resolve() -> None:
    """Two jobs on the render queue at once is a corrupted queue, not a faster export."""
    entered: list[str] = []
    release = threading.Event()
    first_in = threading.Event()

    def blocking(progress: Progress) -> JobOutput:
        entered.append("first")
        first_in.set()
        release.wait(timeout=5)
        return JobOutput({})

    def second(progress: Progress) -> JobOutput:
        entered.append("second")
        return JobOutput({})

    one = start_job("extract_audio", {}, blocking, touches_resolve=True)
    assert first_in.wait(timeout=5)
    two = start_job("extract_audio", {}, second, touches_resolve=True)

    assert entered == ["first"]
    assert store.load(two["job_id"]).step == "waiting for Resolve"

    release.set()
    wait_for(one["job_id"])
    wait_for(two["job_id"])

    assert entered == ["first", "second"]


def test_a_job_that_does_not_touch_resolve_runs_alongside_one_that_does() -> None:
    release = threading.Event()
    holding = threading.Event()
    ran = threading.Event()

    def holds_resolve(progress: Progress) -> JobOutput:
        holding.set()
        release.wait(timeout=5)
        return JobOutput({})

    def pure_compute(progress: Progress) -> JobOutput:
        ran.set()
        return JobOutput({})

    one = start_job("extract_audio", {}, holds_resolve, touches_resolve=True)
    assert holding.wait(timeout=5)
    two = start_job("analyze_music", {}, pure_compute)

    assert ran.wait(timeout=5)
    release.set()
    wait_for(one["job_id"])
    wait_for(two["job_id"])


def _never_runs(progress: Progress) -> JobOutput:
    raise AssertionError("a cache hit must not run the work")


def _artifact(name: str) -> Path:
    path = get_config().audio_dir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF")
    return path
