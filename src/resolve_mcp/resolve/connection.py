"""The Resolve connection: lazy singleton, cheap probe, one automatic reconnect.

The server never dies because Resolve is closed — nothing connects until the first
Resolve-touching call. Before handing the singleton out, a ``GetVersion()`` probe checks
the handle is still live; a dead handle costs one reconnect, not a session.

This is also the project's test seam: substitute the connection with one whose
``connect`` returns a fake, and every layer above runs with Resolve closed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from ..errors import ResolveMcpError, ResolveUnavailableError
from ..logging_config import get_logger
from .loader import load_resolve

log = get_logger("connection")

NOT_RUNNING = (
    "DaVinci Resolve is not running, or external scripting is not reachable from this process."
)


class ResolveConnection:
    """Holds at most one Resolve handle and keeps it honest."""

    def __init__(self, connect: Callable[[], Any | None] | None = None) -> None:
        self._connect = connect or load_resolve
        self._handle: Any | None = None
        self._lock = threading.RLock()

    @property
    def connected(self) -> bool:
        """Whether a probed-good handle is currently held (no Resolve call made)."""
        return self._handle is not None

    def invalidate(self) -> None:
        """Drop the handle; the next call reconnects."""
        with self._lock:
            self._handle = None

    def handle(self) -> Any:
        """Return a live Resolve handle, reconnecting once if the held one has died."""
        with self._lock:
            if self._handle is not None:
                if self._probe(self._handle):
                    return self._handle
                log.info("Resolve handle went stale; reconnecting once")
                self._handle = None

            handle, failure = self._connect_once()
            if handle is not None and self._probe(handle):
                self._handle = handle
                return handle

            self._handle = None
            raise failure or ResolveUnavailableError(cause=NOT_RUNNING)

    def _connect_once(self) -> tuple[Any | None, ResolveMcpError | None]:
        try:
            handle = self._connect()
        except ResolveMcpError as exc:
            log.warning("Connecting to Resolve failed: %s", exc.cause)
            return None, exc
        except Exception as exc:  # noqa: BLE001 - any loader failure is "not available"
            log.exception("Connecting to Resolve raised")
            return None, ResolveUnavailableError(cause=f"{type(exc).__name__}: {exc}")
        if handle is None:
            return None, ResolveUnavailableError(cause=NOT_RUNNING)
        return handle, None

    @staticmethod
    def _probe(handle: Any) -> bool:
        """A cheap liveness check: a stale handle raises or returns nothing."""
        try:
            return bool(handle.GetVersion())
        except Exception:  # noqa: BLE001 - a dead handle can fail in any way
            log.debug("Resolve probe failed", exc_info=True)
            return False


_connection: ResolveConnection | None = None
_singleton_lock = threading.Lock()


def get_connection() -> ResolveConnection:
    global _connection
    with _singleton_lock:
        if _connection is None:
            _connection = ResolveConnection()
        return _connection


def set_connection(connection: ResolveConnection) -> None:
    """Substitute the singleton — the seam tests attach the fake Resolve through."""
    global _connection
    with _singleton_lock:
        _connection = connection


def reset_connection() -> None:
    global _connection
    with _singleton_lock:
        _connection = None
