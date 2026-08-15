"""One envelope shape for "I started a job" (#219).

Analysis, render and stems used to wrap their record as ``{"job": record}`` while the video
tools spliced the same record into the envelope top level — the same concept in two shapes,
depending on which tool the agent had called. The wrap now happens once, in the decorator,
so what is pinned here is the recognition itself and that every family comes back the same:
including on the reconnect path, which is a second return statement and so a second place
the shape can drift.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from resolve_mcp.resolve.connection import ResolveConnection, get_connection
from resolve_mcp.tools.envelope import shaped, tool, tool_without_connection

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

    @tool_without_connection
    def start_one() -> dict[str, Any]:
        return a_record()

    envelope = start_one()

    assert envelope["ok"] is True
    assert envelope["job"] == a_record()
    assert "job_id" not in envelope
    assert "context" in envelope


def test_a_tool_that_returns_a_result_of_its_own_is_left_alone(attach: Attach) -> None:
    attach(studio())

    @tool_without_connection
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
    def start_one(connection: ResolveConnection) -> dict[str, Any]:
        connection.handle().GetProjectManager()
        return a_record()

    envelope = start_one()

    assert envelope["ok"] is True, envelope.get("error")
    assert connector.attempts == 2
    assert envelope["job"] == a_record()
    assert "job_id" not in envelope


# --- the decorator hands the connection in (#229) -----------------------------------------------


def test_a_tool_that_declares_a_connection_is_handed_the_live_one(attach: Attach) -> None:
    """The one line every tool body used to open with is now the decorator's job."""
    attach(studio())

    @tool
    def reads_it(connection: ResolveConnection) -> dict[str, Any]:
        return {"is_the_singleton": connection is get_connection()}

    envelope = reads_it()

    assert envelope["ok"] is True, envelope.get("error")
    assert envelope["is_the_singleton"] is True


def test_the_tool_s_own_arguments_still_arrive_beside_the_connection(attach: Attach) -> None:
    """The injected parameter is first; everything the caller passes lands where it was."""
    attach(studio())

    @tool
    def takes_both(connection: ResolveConnection, name: str, count: int = 2) -> dict[str, Any]:
        return {"name": name, "count": count, "connected": connection.connected}

    positional = takes_both("cam_a")
    keywords = takes_both(name="cam_b", count=5)

    assert (positional["name"], positional["count"]) == ("cam_a", 2)
    assert (keywords["name"], keywords["count"]) == ("cam_b", 5)


def test_a_connectionless_tool_is_handed_nothing_and_keeps_its_signature(attach: Attach) -> None:
    """A tool that answers from documents takes the other decorator, and is left alone."""
    attach(studio())

    @tool_without_connection
    def pure(schema: str = "cut") -> dict[str, Any]:
        return {"schema": schema}

    assert pure()["schema"] == "cut"
    assert list(inspect.signature(pure).parameters) == ["schema"]


def test_the_injected_connection_is_invisible_to_a_caller(attach: Attach) -> None:
    """What the transport reads is the signature: the tool's own parameters, and no more."""
    attach(studio())

    @tool
    def takes_both(connection: ResolveConnection, name: str) -> dict[str, Any]:
        return {"name": name}

    assert list(inspect.signature(takes_both).parameters) == ["name"]
    assert "connection" not in takes_both.__annotations__


def test_the_retried_call_is_handed_a_connection_that_reconnects(attach: Attach) -> None:
    """The handle the retry works on is fresh — the dead one is never handed in twice."""
    dying = studio()
    dying.die_after(1)  # passes the connection's probe, dies on the very next call
    connector = attach(dying, studio())
    seen: list[Any] = []

    @tool
    def touches_resolve(connection: ResolveConnection) -> dict[str, Any]:
        handle = connection.handle()
        seen.append(handle)
        handle.GetProjectManager()
        return {"touched": True}

    envelope = touches_resolve()

    assert envelope["ok"] is True, envelope.get("error")
    assert connector.attempts == 2
    assert len(seen) == 2 and seen[0] is not seen[1]


def test_a_tool_that_forgot_the_connection_is_refused_at_decoration() -> None:
    """Injection is positional: a forgotten parameter would silently eat the first argument.

    The tool would still register — minus one parameter — and run with a ResolveConnection
    where its caller's value belongs. Loud at import beats wrong at call time.
    """
    with pytest.raises(TypeError, match="connection: ResolveConnection"):

        @tool  # type: ignore[arg-type]  # the mistake under test
        def forgot(clip: str) -> dict[str, Any]:
            return {"clip": clip}

    with pytest.raises(TypeError, match="connection: ResolveConnection"):

        @tool  # type: ignore[arg-type]  # the mistake under test
        def declared_nothing() -> dict[str, Any]:
            return {}


def test_a_body_that_holds_no_handle_is_never_retried(attach: Attach) -> None:
    """A dead handle cannot be why a document-only tool failed, so it does not run twice."""
    dying = studio()
    dying.die_after(1)  # the handle dies while the tool is running, as it does for any tool
    attach(dying, studio())
    calls: list[int] = []

    @tool_without_connection
    def reads_a_document() -> dict[str, Any]:
        calls.append(1)
        get_connection().handle().GetProjectManager()
        raise RuntimeError("a bug of its own")

    envelope = reads_a_document()

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "internal_error"
    assert calls == [1]  # a second run would repeat whatever the body had already written
