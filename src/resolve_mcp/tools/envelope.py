"""The shape every tool result has.

Three guarantees live here, so no individual tool has to remember them:

* every result echoes the current project/timeline context, so a project switch is
  visible the moment it happens;
* every failure is ``ok: false`` with a structured ``cause``/``fix`` — never an exception
  across the tool boundary, never a traceback in the payload;
* a tool that hands back a job record replies ``{"job": record}``, whichever tool it is.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from ..errors import InternalError, ResolveMcpError, ResolveUnavailableError
from ..logging_config import get_logger
from ..resolve.connection import get_connection
from ..resolve.session import context

log = get_logger("tools")

Envelope = dict[str, Any]

JOB_RECORD_KEYS = frozenset({"job_id", "kind", "state"})
"""What identifies a returned payload as a job record rather than a result of its own.

The three fields every record has from the moment it is created — and which no other tool
payload carries together — so recognising them costs nothing and cannot be forgotten the
way the ``{"job": ...}`` wrap was at the video tools (#219).
"""


def current_context() -> dict[str, Any]:
    return context(get_connection())


def shaped(payload: dict[str, Any]) -> dict[str, Any]:
    """A job record becomes ``{"job": record}``; every other payload passes through.

    One envelope for "I started a job", built here rather than at each starter: the agent
    polls what ``get_job`` returns, so a starter that spliced the record into the envelope
    top level made the same concept read two ways depending on which tool it came from.
    """
    return {"job": payload} if payload.keys() >= JOB_RECORD_KEYS else payload


def tool[**P](fn: Callable[P, dict[str, Any]]) -> Callable[P, Envelope]:
    """Wrap a wrapper-layer call as a tool: payload in, envelope out."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Envelope:
        try:
            payload = fn(*args, **kwargs)
        except ResolveMcpError as exc:
            log.info("%s failed: %s", fn.__name__, exc.cause)
            return failure(exc)
        except Exception as exc:  # noqa: BLE001 - a bug must not become a raw traceback
            retried = _retry_if_the_handle_died(fn, args, kwargs, exc)
            if retried is not None:
                return retried
            log.exception("%s raised unexpectedly", fn.__name__)
            return failure(InternalError(cause=f"{type(exc).__name__}: {exc}"))
        return {"ok": True, **shaped(payload), "context": current_context()}

    return wrapper


def _retry_if_the_handle_died[**P](
    fn: Callable[P, dict[str, Any]],
    args: Any,
    kwargs: Any,
    original: Exception,
) -> Envelope | None:
    """One retry when Resolve went away *during* the call, else ``None``.

    The connection probes before handing the handle out, but Resolve can quit between
    that probe and the calls the wrapper then makes on it — which surfaces as an
    arbitrary exception from the scripting library. Retrying only once the handle is
    confirmed dead keeps a real bug from being papered over by a second attempt.
    """
    connection = get_connection()
    if not connection.dropped():
        return None

    log.info("%s failed on a dead Resolve handle; reconnecting once", fn.__name__)
    connection.invalidate()
    try:
        payload = fn(*args, **kwargs)
    except ResolveMcpError as exc:
        return failure(exc)
    except Exception:  # noqa: BLE001 - the reconnect did not save it
        log.exception("%s failed again after reconnecting", fn.__name__)
        return failure(
            ResolveUnavailableError(
                cause=f"Resolve dropped the connection mid-call and did not come back "
                f"({type(original).__name__}: {original}).",
            )
        )
    return {"ok": True, **shaped(payload), "context": current_context()}


def failure(error: ResolveMcpError) -> Envelope:
    return {"ok": False, "error": error.payload(), "context": current_context()}
