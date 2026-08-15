"""One envelope shape for "I started a job" (#219).

Analysis, render and stems used to wrap their record as ``{"job": record}`` while the video
tools spliced the same record into the envelope top level — the same concept in two shapes,
depending on which tool the agent had called. The wrap now happens once, in the decorator,
so what is pinned here is the recognition itself and that every family comes back the same:
including on the reconnect path, which is a second return statement and so a second place
the shape can drift.
"""

from __future__ import annotations

from typing import Any

from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools.envelope import shaped, tool

from .conftest import Attach
from .fakes import studio


def a_record(**overrides: Any) -> dict[str, Any]:
    """A job record as ``store.JobRecord.payload`` hands one back, trimmed to what matters."""
    return {
        "job_id": "scene-cuts-0123456789ab",
        "kind": "scene_cuts",
        "state": "running",
        "progress": 0.0,
        "result": None,
        **overrides,
    }


# --- recognising a record ----------------------------------------------------------------------


def test_a_returned_job_record_is_wrapped_as_the_job() -> None:
    assert shaped(a_record()) == {"job": a_record()}


def test_a_payload_that_is_not_a_record_passes_straight_through() -> None:
    payload = {"frames": [], "clip": "cam_a", "state": "ok"}

    assert shaped(payload) is payload


def test_a_record_is_recognised_in_every_state_it_can_come_back_in() -> None:
    """A cache hit replies completed and a refusal replies failed — all of them are jobs."""
    for state in ("running", "completed", "failed"):
        assert shaped(a_record(state=state)) == {"job": a_record(state=state)}


# --- through the decorator ---------------------------------------------------------------------


def test_a_tool_that_starts_a_job_replies_with_the_record_under_job(attach: Attach) -> None:
    attach(studio())

    @tool
    def start_one() -> dict[str, Any]:
        return a_record()

    envelope = start_one()

    assert envelope["ok"] is True
    assert envelope["job"] == a_record()
    assert "job_id" not in envelope
    assert "context" in envelope


def test_a_tool_that_returns_a_result_of_its_own_is_left_alone(attach: Attach) -> None:
    attach(studio())

    @tool
    def read_one() -> dict[str, Any]:
        return {"frames": ["a.jpg"]}

    envelope = read_one()

    assert envelope["frames"] == ["a.jpg"]
    assert "job" not in envelope


def test_a_job_started_after_a_reconnect_comes_back_in_the_same_envelope(
    attach: Attach,
) -> None:
    """The retry is a second return, and a second return is where one shape becomes two."""
    dying = studio()
    dying.die_after(1)  # passes the connection's probe, dies on the very next call
    connector = attach(dying, studio())

    @tool
    def start_one() -> dict[str, Any]:
        get_connection().handle().GetProjectManager()
        return a_record()

    envelope = start_one()

    assert envelope["ok"] is True, envelope.get("error")
    assert connector.attempts == 2
    assert envelope["job"] == a_record()
    assert "job_id" not in envelope
