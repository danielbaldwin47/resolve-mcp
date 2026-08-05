"""The lazy-singleton connection: probe before use, one automatic reconnect, then error."""

from __future__ import annotations

import pytest

from resolve_mcp.errors import ResolveMcpError
from resolve_mcp.resolve import connection as connection_module

from .conftest import Attach
from .fakes import studio


def test_does_not_touch_resolve_until_the_first_call(attach: Attach) -> None:
    connector = attach(studio())

    assert connector.attempts == 0

    connection_module.get_connection().handle()

    assert connector.attempts == 1


def test_reuses_one_handle_across_calls(attach: Attach) -> None:
    fake = studio()
    connector = attach(fake)
    conn = connection_module.get_connection()

    first = conn.handle()
    second = conn.handle()

    assert first is second is fake
    assert connector.attempts == 1


def test_probes_the_handle_before_handing_it_out(attach: Attach) -> None:
    fake = studio()
    attach(fake)
    conn = connection_module.get_connection()

    conn.handle()
    conn.handle()

    assert fake.probe_count == 2


def test_dropped_handle_costs_one_reconnect_not_a_session(attach: Attach) -> None:
    dead, alive = studio(), studio()
    connector = attach(dead, alive)
    conn = connection_module.get_connection()
    assert conn.handle() is dead

    dead.drop()

    assert conn.handle() is alive
    assert connector.attempts == 2


def test_reconnect_is_attempted_once_then_it_is_an_error(attach: Attach) -> None:
    dead = studio()
    connector = attach(dead, None, studio())
    conn = connection_module.get_connection()
    conn.handle()
    dead.drop()

    with pytest.raises(ResolveMcpError) as excinfo:
        conn.handle()

    assert connector.attempts == 2
    assert excinfo.value.code == "resolve_unavailable"


def test_unreachable_resolve_raises_a_cause_and_a_fix(attach: Attach) -> None:
    attach(None)

    with pytest.raises(ResolveMcpError) as excinfo:
        connection_module.get_connection().handle()

    error = excinfo.value
    assert error.code == "resolve_unavailable"
    assert "Resolve" in error.cause
    assert error.fix
    assert "Traceback" not in error.cause


def test_a_failed_connect_does_not_poison_the_next_call(attach: Attach) -> None:
    fake = studio()
    connector = attach(None, fake)
    conn = connection_module.get_connection()
    with pytest.raises(ResolveMcpError):
        conn.handle()

    assert conn.handle() is fake
    assert connector.attempts == 2


def test_every_connection_state_change_logs_a_line(
    attach: Attach, caplog: pytest.LogCaptureFixture
) -> None:
    """Attach, reconnect, handle death, invalidate: each leaves a log line, because a
    live failure on a machine no one is at gets diagnosed from the log or not at all."""
    first, second, third = studio(), studio(), studio()
    attach(first, second, third)
    conn = connection_module.get_connection()

    with caplog.at_level("INFO", logger=connection_module.log.name):
        conn.handle()
        first.drop()
        conn.handle()  # stale handle noticed inside handle()
        conn.invalidate()
        conn.handle()  # the envelope's recovery path: invalidate, then retry

    assert [record.getMessage() for record in caplog.records] == [
        "Resolve attached",
        "Resolve handle went stale; reconnecting once",
        "Resolve reconnected",
        "Resolve handle invalidated; next call reconnects",
        "Resolve reconnected",
    ]


def test_reports_whether_it_currently_holds_a_handle(attach: Attach) -> None:
    fake = studio()
    attach(fake)
    conn = connection_module.get_connection()
    assert conn.connected is False

    conn.handle()
    assert conn.connected is True

    fake.drop()
    conn.invalidate()
    assert conn.connected is False
