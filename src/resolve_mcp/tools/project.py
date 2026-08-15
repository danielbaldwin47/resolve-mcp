"""Session and project tools — how the agent orients itself and picks what to work on."""

from __future__ import annotations

from typing import Any

from ..resolve import session
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def get_status() -> dict[str, Any]:
    """Report connection state, Resolve version, and the current project and timeline.

    Call this first in a session, and again whenever you are unsure what Resolve has open.
    The result — like every result from this server — echoes context: connected, Resolve
    version, project, timeline and fps.
    """
    connection = get_connection()
    return {"product": session.product_name(connection)}


@tool
def list_projects() -> dict[str, Any]:
    """List the project names in Resolve's current database folder.

    Use it to find the exact name to hand to load_project — names must match exactly.
    """
    connection = get_connection()
    return {"projects": session.list_projects(connection)}


@tool
def load_project(name: str) -> dict[str, Any]:
    """Load the named project, making it the one every later tool call acts on.

    The name must match exactly; list_projects shows what is available. The result echoes
    the new context, so you can confirm the switch landed.
    """
    connection = get_connection()
    return {"opened": session.load_project(connection, name)}


@tool
def snapshot_project(path: str | None = None) -> dict[str, Any]:
    """Write an opaque .drp backup of the open project, and return where it landed.

    Take a snapshot before any big operation — a build, a bulk media change, a risky
    escape-hatch script — so a mistake is a restore rather than a rebuild. Without a path,
    the snapshot goes to a timestamped file in the cache directory.
    """
    connection = get_connection()
    target, project = session.snapshot_project(connection, path)
    return {"snapshot": str(target), "project": project}


TOOLS: tuple[Any, ...] = (get_status, list_projects, load_project, snapshot_project)

__all__ = ["TOOLS", "get_status", "list_projects", "load_project", "snapshot_project"]
