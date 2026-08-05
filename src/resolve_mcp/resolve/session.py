"""Session and project wrappers.

Thin, testable, and MCP-free: these take a connection and talk to Resolve. The tool layer
above adds the envelope; nothing here knows about FastMCP.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..errors import NoProjectOpenError, ProjectNotFoundError, SnapshotFailedError
from ..logging_config import get_logger
from .connection import ResolveConnection

log = get_logger("session")

FPS_SETTING = "timelineFrameRate"
UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")

Context = dict[str, Any]

DISCONNECTED: Context = {
    "connected": False,
    "resolve_version": None,
    "project": None,
    "timeline": None,
    "fps": None,
}


def context(connection: ResolveConnection) -> Context:
    """The context every tool result echoes. Best-effort: this never raises."""
    try:
        resolve = connection.handle()
        project = _current_project(resolve)
        timeline = project.GetCurrentTimeline() if project is not None else None
        return {
            "connected": True,
            "resolve_version": str(resolve.GetVersionString()),
            "project": str(project.GetName()) if project is not None else None,
            "timeline": str(timeline.GetName()) if timeline is not None else None,
            "fps": _fps(project, timeline),
        }
    except Exception:  # noqa: BLE001 - context is decoration, never the failure itself
        log.debug("Could not read Resolve context", exc_info=True)
        return dict(DISCONNECTED)


def product_name(connection: ResolveConnection) -> str | None:
    """Which Resolve edition is attached. Raises if Resolve is unreachable."""
    resolve = connection.handle()
    try:
        return str(resolve.GetProductName())
    except Exception:  # noqa: BLE001 - an edition that lacks the getter is not a failure
        log.debug("Could not read the Resolve product name", exc_info=True)
        return None


def list_projects(connection: ResolveConnection) -> list[str]:
    """Project names in the current database folder."""
    manager = connection.handle().GetProjectManager()
    return [str(name) for name in (manager.GetProjectListInCurrentFolder() or [])]


def open_project(connection: ResolveConnection, name: str) -> str:
    """Load a project by name. Returns the loaded project's own name."""
    manager = connection.handle().GetProjectManager()
    project = manager.LoadProject(name)
    if project is None:
        available = [str(item) for item in (manager.GetProjectListInCurrentFolder() or [])]
        raise ProjectNotFoundError(name, available)
    return str(project.GetName())


def snapshot_project(
    connection: ResolveConnection,
    path: str | Path | None = None,
    config: Config | None = None,
) -> tuple[Path, str]:
    """Export the open project to an opaque ``.drp`` backup. Returns (path, project name).

    Opaque by design: the snapshot is a restore point, not something to read or edit.
    """
    config = config or get_config()
    manager = connection.handle().GetProjectManager()
    project = manager.GetCurrentProject()
    if project is None:
        raise NoProjectOpenError(cause="No project is open, so there is nothing to snapshot.")

    name = str(project.GetName())
    target = Path(path) if path is not None else config.snapshot_dir / _snapshot_filename(name)
    if target.suffix.lower() != ".drp":
        target = target.with_suffix(".drp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SnapshotFailedError(cause=f"Could not create {target.parent}: {exc}") from exc

    if not manager.ExportProject(name, str(target), False):
        raise SnapshotFailedError(
            cause=f"Resolve refused to export {name!r} to {target}.",
        )
    log.info("Snapshotted %s to %s", name, target)
    return target, name


def _snapshot_filename(project: str, now: datetime | None = None) -> str:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    slug = UNSAFE_IN_FILENAME.sub("-", project).strip("-") or "project"
    return f"{slug}-{stamp}.drp"


def _current_project(resolve: Any) -> Any | None:
    manager = resolve.GetProjectManager()
    return manager.GetCurrentProject() if manager is not None else None


def _fps(project: Any, timeline: Any) -> float | None:
    """Frames per second, timeline setting first — it is what a cut is measured in."""
    for source in (timeline, project):
        if source is None:
            continue
        try:
            value = source.GetSetting(FPS_SETTING)
        except Exception:  # noqa: BLE001
            continue
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            log.debug("Unparseable %s: %r", FPS_SETTING, value)
    return None
