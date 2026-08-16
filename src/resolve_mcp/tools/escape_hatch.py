"""The escape hatch, so an API gap never dead-ends a session."""

from __future__ import annotations

from typing import Any

from ..resolve import scripting
from ..resolve.connection import ResolveConnection
from .envelope import tool


@tool
def run_python(connection: ResolveConnection, code: str) -> dict[str, Any]:
    """Run DaVinci Resolve scripting-API Python in the server process.

    Prefer the real tools: they wrap known API footguns, echo context, and return
    structured results. Reach for this only where no tool fits — an API corner the catalog
    does not cover yet, or a one-off inspection.

    The namespace has `resolve`, `project_manager`, `project` and `timeline` pre-bound.
    The returned value is the trailing expression, or a `result` variable if you set one;
    stdout is captured and returned alongside it. Long values are truncated.
    """
    return scripting.run_python(connection, code)


TOOLS: tuple[Any, ...] = (run_python,)

__all__ = ["TOOLS", "run_python"]
