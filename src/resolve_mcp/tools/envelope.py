"""The shape every tool result has.

Four guarantees live here, so no individual tool has to remember them:

* a ``@tool`` is handed the connection as its first argument and never fetches one itself —
  the retry below reconnects, so acquisition has exactly one owner
  (``@tool_without_connection`` is the same envelope for the tools that answer without
  Resolve);

* every result echoes the current project/timeline context, so a project switch is
  visible the moment it happens;
* every failure is ``ok: false`` with a structured ``cause``/``fix`` — never an exception
  across the tool boundary, never a traceback in the payload;
* a tool that hands back a job record replies ``{"job": record}``, whichever tool it is.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, Concatenate

from ..errors import InternalError, ResolveMcpError, ResolveUnavailableError
from ..logging_config import get_logger
from ..resolve.connection import ResolveConnection, get_connection
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


def tool[**P](
    fn: Callable[Concatenate[ResolveConnection, P], dict[str, Any]],
) -> Callable[P, Envelope]:
    """Wrap a Resolve-touching call as a tool: payload in, envelope out.

    The connection is the first parameter and the decorator hands it in — the envelope
    already fetches one for its reconnect-retry path, so the tool body has no getter to
    remember, and no way to hold a handle the retry has since replaced. The parameter is
    the decorator's, not the agent's: it never reaches the transport.

    A tool that answers without Resolve takes ``tool_without_connection`` instead.
    """
    injected = _the_declared_connection(fn)

    def call(args: Any, kwargs: Any) -> dict[str, Any]:
        # Fetched per attempt, never captured: after a reconnect the singleton hands back
        # the new handle, and a body holding the old one would be working on a corpse.
        return fn(get_connection(), *args, **kwargs)

    wrapper: Callable[P, Envelope] = _wrap_as_tool(fn, call, retries_a_dead_handle=True)
    _hide(injected, on=wrapper, of=fn)
    return wrapper


def tool_without_connection[**P](fn: Callable[P, dict[str, Any]]) -> Callable[P, Envelope]:
    """The same envelope for a tool that needs no Resolve at all.

    A schema, a cut file read back as words, a job record from the store: these answer from
    documents, and declaring a connection they never use would say the opposite. Two
    decorators rather than one that guesses, because which one a tool takes is a fact about
    the tool that should read off its ``def`` line — and because a body that never holds a
    handle cannot have lost one, so it is not retried.
    """
    return _wrap_as_tool(fn, lambda args, kwargs: fn(*args, **kwargs), retries_a_dead_handle=False)


def _the_declared_connection(fn: Callable[..., dict[str, Any]]) -> inspect.Parameter:
    """The parameter ``tool`` will fill in, or a refusal at import time.

    Injection is positional, so a ``@tool`` that forgot the parameter would bind a
    connection to the author's first real argument and drop that argument from the MCP
    schema — a wrong value with nothing raised. Checked here, where the fix is one line
    away, rather than left to whatever the body does with a ResolveConnection.
    """
    first = next(iter(inspect.signature(fn).parameters.values()), None)
    if first is None or first.annotation not in (ResolveConnection, "ResolveConnection"):
        raise TypeError(
            f"@tool {fn.__name__} must declare `connection: ResolveConnection` as its first "
            f"parameter — the decorator hands it in. A tool that needs no Resolve takes "
            f"@tool_without_connection instead."
        )
    return first


def _hide(
    parameter: inspect.Parameter,
    *,
    on: Callable[..., Envelope],
    of: Callable[..., dict[str, Any]],
) -> None:
    """Present the tool's own parameters and nothing else.

    ``functools.wraps`` leaves the wrapped function's signature and annotations showing, and
    that is what MCP registration reads to build the input schema — so without this the
    injected parameter would arrive at the agent as an argument to fill in.
    """
    signature = inspect.signature(of)
    on.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=[p for p in signature.parameters.values() if p.name != parameter.name]
    )
    on.__annotations__ = {
        name: annotation
        for name, annotation in on.__annotations__.items()
        if name != parameter.name
    }


def _wrap_as_tool[**P](
    fn: Callable[..., dict[str, Any]],
    call: Callable[[Any, Any], dict[str, Any]],
    *,
    retries_a_dead_handle: bool,
) -> Callable[P, Envelope]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Envelope:
        try:
            payload = call(args, kwargs)
        except ResolveMcpError as exc:
            log.info("%s failed: %s", fn.__name__, exc.cause)
            return failure(exc)
        except Exception as exc:  # noqa: BLE001 - a bug must not become a raw traceback
            retried = (
                _retry_if_the_handle_died(fn.__name__, lambda: call(args, kwargs), exc)
                if retries_a_dead_handle
                else None
            )
            if retried is not None:
                return retried
            log.exception("%s raised unexpectedly", fn.__name__)
            return failure(InternalError(cause=f"{type(exc).__name__}: {exc}"))
        return {"ok": True, **shaped(payload), "context": current_context()}

    return wrapper


def _retry_if_the_handle_died(
    name: str,
    invoke: Callable[[], dict[str, Any]],
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

    log.info("%s failed on a dead Resolve handle; reconnecting once", name)
    connection.invalidate()
    try:
        payload = invoke()
    except ResolveMcpError as exc:
        return failure(exc)
    except Exception:  # noqa: BLE001 - the reconnect did not save it
        log.exception("%s failed again after reconnecting", name)
        return failure(
            ResolveUnavailableError(
                cause=f"Resolve dropped the connection mid-call and did not come back "
                f"({type(original).__name__}: {original}).",
            )
        )
    return {"ok": True, **shaped(payload), "context": current_context()}


def failure(error: ResolveMcpError) -> Envelope:
    return {"ok": False, "error": error.payload(), "context": current_context()}
