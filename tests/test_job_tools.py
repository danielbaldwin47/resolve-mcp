"""get_job and list_jobs through the tool seam — what a restarted session actually sees.

These call the tool functions directly, envelope and all. Resolve is attached as "not
running" throughout: polling a job must work whether or not Resolve is up, because a job
that outlived the application is exactly the one worth asking about.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from resolve_mcp.config import get_config
from resolve_mcp.jobs import store
from resolve_mcp.jobs.runner import JobOutput, Progress, start_job, wait_for
from resolve_mcp.tools.jobs import get_job, list_jobs

from .conftest import Attach


def test_polling_a_finished_job_returns_its_result(attach: Attach) -> None:
    attach(None)
    started = start_job("extract_audio", {"scope": "timeline"}, _produces({"path": "mix.wav"}))
    wait_for(started["job_id"])

    reply = get_job(started["job_id"])

    assert reply["ok"] is True
    assert reply["job"]["state"] == "completed"
    assert reply["job"]["result"] == {"path": "mix.wav"}
    assert reply["job"]["kind"] == "extract_audio"
    assert reply["context"]["connected"] is False


def test_polling_a_job_that_never_existed_is_a_structured_failure(attach: Attach) -> None:
    attach(None)

    reply = get_job("extract_audio-000000000000")

    assert reply["ok"] is False
    assert reply["error"]["code"] == "job_not_found"


def test_listing_shows_the_newest_job_first(attach: Attach) -> None:
    attach(None)
    first = start_job("extract_audio", {}, _produces({}))
    wait_for(first["job_id"])
    second = start_job("analyze_music", {}, _produces({}))
    wait_for(second["job_id"])

    reply = list_jobs()

    assert [one["job_id"] for one in reply["jobs"]] == [second["job_id"], first["job_id"]]
    assert reply["total"] == 2


def test_listing_filters_by_state_and_rejects_a_state_that_is_not_one(attach: Attach) -> None:
    attach(None)
    done = start_job("extract_audio", {}, _produces({}))
    wait_for(done["job_id"])

    assert [one["job_id"] for one in list_jobs(state="completed")["jobs"]] == [done["job_id"]]
    assert list_jobs(state="failed")["jobs"] == []

    refused = list_jobs(state="finished")
    assert refused["ok"] is False
    assert refused["error"]["code"] == "invalid_request"
    assert "running" in refused["error"]["fix"]


def test_the_listing_caps_at_the_limit_but_still_says_how_many_there_are(
    attach: Attach,
) -> None:
    attach(None)
    for _ in range(3):
        wait_for(start_job("extract_audio", {}, _produces({}))["job_id"])

    reply = list_jobs(limit=2)

    assert reply["count"] == 2
    assert reply["total"] == 3


def test_a_job_the_previous_server_left_running_comes_back_interrupted(attach: Attach) -> None:
    """The restart case: the record outlives the process, the worker thread does not."""
    attach(None)
    started = start_job("separate_stems", {"scope": "timeline"}, _produces({}))
    wait_for(started["job_id"])
    _rewrite_as_a_previous_server_left_it(started["job_id"])

    listed = list_jobs()["jobs"]
    polled = get_job(started["job_id"])["job"]

    assert [one["state"] for one in listed] == ["failed"]
    assert polled["error"]["code"] == "job_interrupted"
    assert polled["params"] == {"scope": "timeline"}
    assert polled["kind"] == "separate_stems"


def _produces(result: dict[str, Any]) -> Any:
    def work(progress: Progress) -> JobOutput:
        return JobOutput(result)

    return work


def _rewrite_as_a_previous_server_left_it(job_id: str) -> None:
    """Put the record back the way a server that died mid-job left it behind."""
    path = Path(get_config().job_dir) / f"{job_id}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(
        {
            "state": store.RUNNING,
            "session": "a-server-that-has-since-exited",
            "result": None,
            "finished_at": None,
            "progress": 0.3,
        }
    )
    path.write_text(json.dumps(raw), encoding="utf-8")
