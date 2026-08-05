"""The on-disk job records — the only thing that survives a server restart.

Every assertion here is about what a *new* process sees, because that is the whole
point of writing records to disk: a job the agent started before a restart has to be
findable, and one that was still running has to be honest about having died.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resolve_mcp.config import get_config
from resolve_mcp.errors import JobNotFoundError
from resolve_mcp.jobs import store


def test_a_new_job_starts_running_and_lands_on_disk() -> None:
    record = store.new_job("extract_audio", {"scope": "timeline"})

    assert record.state == "running"
    assert record.kind == "extract_audio"
    assert record.job_id.startswith("extract_audio-")
    assert store.load(record.job_id).params == {"scope": "timeline"}


def test_progress_reads_back_from_disk_not_from_memory() -> None:
    record = store.new_job("separate_stems", {})
    record.progress = 0.4
    record.step = "separating"
    store.save(record)

    reloaded = store.load(record.job_id)

    assert reloaded.progress == pytest.approx(0.4)
    assert reloaded.step == "separating"


def test_an_unknown_job_id_names_the_tool_that_lists_them() -> None:
    with pytest.raises(JobNotFoundError) as raised:
        store.load("extract_audio-nosuchjob")

    assert "list_jobs" in raised.value.fix


def test_jobs_come_back_newest_first_and_filter_by_state() -> None:
    first = store.new_job("render", {})
    second = store.new_job("render", {})
    store.finish(first, result={"path": "a.wav"})

    listed = store.load_all()

    assert [one.job_id for one in listed] == [second.job_id, first.job_id]
    assert [one.job_id for one in store.load_all(state="completed")] == [first.job_id]
    assert [one.job_id for one in store.load_all(state="running")] == [second.job_id]


def test_two_jobs_started_in_the_same_clock_tick_still_list_in_order() -> None:
    """The Windows clock is coarser than two starts in a row, so time alone cannot order them."""
    first = store.new_job("render", {})
    second = store.new_job("render", {})
    for record in (first, second):
        record.started_at = "2026-08-05T12:00:00.000000+00:00"
        store.save(record)

    assert [one.job_id for one in store.load_all()] == [second.job_id, first.job_id]


def test_a_job_left_running_by_a_previous_server_reads_as_interrupted() -> None:
    record = store.new_job("transcribe_audio", {})
    _pretend_a_previous_server_wrote_it(record.job_id)

    recovered = store.load(record.job_id)

    assert recovered.state == "failed"
    assert recovered.error is not None
    assert recovered.error["code"] == "job_interrupted"
    assert "cache" in recovered.error["fix"]


def test_the_interruption_is_written_back_so_the_verdict_is_reached_once() -> None:
    record = store.new_job("transcribe_audio", {})
    _pretend_a_previous_server_wrote_it(record.job_id)

    store.load(record.job_id)

    on_disk = json.loads(_record_path(record.job_id).read_text(encoding="utf-8"))
    assert on_disk["state"] == "failed"
    assert on_disk["session"] == store.SESSION


def test_a_finished_job_from_a_previous_server_is_left_alone() -> None:
    record = store.new_job("render", {})
    store.finish(record, result={"path": "a.wav"})
    _pretend_a_previous_server_wrote_it(record.job_id)

    assert store.load(record.job_id).state == "completed"


def test_a_half_written_record_is_skipped_rather_than_sinking_the_listing() -> None:
    good = store.new_job("render", {})
    (get_config().job_dir / "render-truncated.json").write_text('{"job_id": "ren', encoding="utf-8")

    assert [one.job_id for one in store.load_all()] == [good.job_id]


def test_the_listing_is_empty_before_any_job_has_ever_run() -> None:
    assert store.load_all() == []


def _record_path(job_id: str) -> Path:
    return get_config().job_dir / f"{job_id}.json"


def _pretend_a_previous_server_wrote_it(job_id: str) -> None:
    """Rewrite the record under a session id that is not this process's."""
    path = _record_path(job_id)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["session"] = "a-server-that-has-since-exited"
    path.write_text(json.dumps(raw), encoding="utf-8")
