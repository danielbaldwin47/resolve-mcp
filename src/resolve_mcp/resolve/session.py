"""Session and project wrappers.

Thin, testable, and MCP-free: these take a connection and talk to Resolve. The tool layer
above adds the envelope; nothing here knows about FastMCP.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Config, get_config
from ..errors import NoProjectOpenError, ProjectNotFoundError, SnapshotFailedError
from ..logging_config import get_logger
from ..naming import timestamped_name
from .connection import ResolveConnection

log = get_logger("session")

FPS_SETTING = "timelineFrameRate"

Context = dict[str, Any]

DISCONNECTED: Context = {
    "connected": False,
    "resolve_version": None,
    "project": None,
    "timeline": None,
    "fps": None,
}


def context(connection: ResolveConnection) -> Context:
    """The context every tool result echoes. Best-effort: this never raises.

    ``connected`` answers one question only — is there a live handle. A field that cannot
    be read comes back as ``None``; it must not make a live session claim it is
    disconnected, because that is the one thing the agent trusts this echo for.
    """
    try:
        resolve = connection.handle()
    except Exception:  # noqa: BLE001 - context decorates a result, it never is one
        log.debug("Could not reach Resolve for context", exc_info=True)
        return dict(DISCONNECTED)

    reading = dict(DISCONNECTED)
    reading["connected"] = True
    reading["resolve_version"] = _read(resolve.GetVersionString)
    try:
        project = _current_project(resolve)
        timeline = project.GetCurrentTimeline() if project is not None else None
    except Exception:  # noqa: BLE001
        log.debug("Could not read the current project or timeline", exc_info=True)
        return reading

    reading["project"] = _read(project.GetName) if project is not None else None
    reading["timeline"] = _read(timeline.GetName) if timeline is not None else None
    reading["fps"] = frame_rate(project, timeline)
    return reading


def _read(getter: Any) -> str | None:
    try:
        value = getter()
    except Exception:  # noqa: BLE001 - one unreadable field is not a lost session
        log.debug("Could not read %s", getattr(getter, "__name__", getter), exc_info=True)
        return None
    return None if value is None else str(value)


def current_project(
    connection: ResolveConnection,
    cause: str = "No project is open.",
) -> Any:
    """The open project, or a failure saying so. ``cause`` names what wanted it."""
    manager = connection.handle().GetProjectManager()
    project = manager.GetCurrentProject() if manager is not None else None
    if project is None:
        raise NoProjectOpenError(cause=cause)
    return project


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

    The project is saved first: ``ExportProject`` serialises what is in the database, so
    without a save the restore point silently omits everything done in this session —
    which is exactly the work a snapshot is being taken to protect.
    """
    config = config or get_config()
    manager = connection.handle().GetProjectManager()
    project = manager.GetCurrentProject()
    if project is None:
        raise NoProjectOpenError(cause="No project is open, so there is nothing to snapshot.")

    name = str(project.GetName())
    if not manager.SaveProject():
        log.warning("SaveProject() returned false before snapshotting %s", name)
    target = (
        Path(path)
        if path is not None
        else config.snapshot_dir / timestamped_name(name, ".drp", "project")
    )
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


def _current_project(resolve: Any) -> Any | None:
    manager = resolve.GetProjectManager()
    return manager.GetCurrentProject() if manager is not None else None


def frame_rate(project: Any, timeline: Any) -> float | None:
    """Frames per second, timeline setting first — it is what a cut is measured in.

    Either side may be ``None``: a timeline read on its own still knows its own rate, and a
    project with nothing open still knows the rate its timelines are created at.
    """
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
