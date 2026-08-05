"""The shape every tool result has.

Two guarantees live here, so no individual tool has to remember them:

* every result echoes the current project/timeline context, so a project switch is
  visible the moment it happens;
* every failure is ``ok: false`` with a structured ``cause``/``fix`` — never an exception
  across the tool boundary, never a traceback in the payload.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, ParamSpec

from ..errors import InternalError, ResolveMcpError
from ..logging_config import get_logger
from ..resolve.connection import get_connection
from ..resolve.session import context

log = get_logger("tools")

Envelope = dict[str, Any]
P = ParamSpec("P")


def current_context() -> dict[str, Any]:
    return context(get_connection())


def tool(fn: Callable[P, dict[str, Any]]) -> Callable[P, Envelope]:
    """Wrap a wrapper-layer call as a tool: payload in, envelope out."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Envelope:
        try:
            payload = fn(*args, **kwargs)
        except ResolveMcpError as exc:
            log.info("%s failed: %s", fn.__name__, exc.cause)
            return failure(exc)
        except Exception as exc:  # noqa: BLE001 - a bug must not become a raw traceback
            log.exception("%s raised unexpectedly", fn.__name__)
            return failure(InternalError(cause=f"{type(exc).__name__}: {exc}"))
        return {"ok": True, **payload, "context": current_context()}

    return wrapper


def failure(error: ResolveMcpError) -> Envelope:
    return {"ok": False, "error": error.payload(), "context": current_context()}
