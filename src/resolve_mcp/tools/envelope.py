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
from typing import Any

from ..errors import InternalError, ResolveMcpError, ResolveUnavailableError
from ..logging_config import get_logger
from ..resolve.connection import get_connection
from ..resolve.session import context

log = get_logger("tools")

Envelope = dict[str, Any]


def current_context() -> dict[str, Any]:
    return context(get_connection())


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
        return {"ok": True, **payload, "context": current_context()}

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
    return {"ok": True, **payload, "context": current_context()}


def failure(error: ResolveMcpError) -> Envelope:
    return {"ok": False, "error": error.payload(), "context": current_context()}
