"""A fake DaVinci Resolve scripting API.

This is the project's single test seam: it substitutes at the point the connection
manager hands out the Resolve singleton, so every layer above it — wrappers, tools —
is exercised with Resolve closed.

The fakes mimic the real API's shape, including its quirks: getters return ``None``
rather than raising, ``LoadProject`` returns ``None`` for an unknown name, and settings
come back as strings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class DroppedHandleError(RuntimeError):
    """What a stale Resolve handle raises when the app has gone away."""


class FakeTimeline:
    def __init__(self, name: str, fps: str = "59.94") -> None:
        self._name = name
        self._fps = fps

    def GetName(self) -> str:  # noqa: N802 - mirrors the Resolve API
        return self._name

    def GetSetting(self, key: str) -> str | None:  # noqa: N802
        return self._fps if key == "timelineFrameRate" else None


class FakeProject:
    def __init__(
        self,
        name: str,
        timeline: FakeTimeline | None = None,
        fps: str = "24",
    ) -> None:
        self._name = name
        self._timeline = timeline
        self._fps = fps

    def GetName(self) -> str:  # noqa: N802
        return self._name

    def GetCurrentTimeline(self) -> FakeTimeline | None:  # noqa: N802
        return self._timeline

    def GetSetting(self, key: str) -> str | None:  # noqa: N802
        return self._fps if key == "timelineFrameRate" else None


class FakeProjectManager:
    def __init__(self, owner: FakeResolve) -> None:
        self._owner = owner
        self.exports: list[tuple[str, str, bool]] = []
        self.export_result = True

    def _check(self) -> None:
        self._owner._check()

    def GetProjectListInCurrentFolder(self) -> list[str]:  # noqa: N802
        self._check()
        return list(self._owner.projects)

    def GetCurrentProject(self) -> FakeProject | None:  # noqa: N802
        self._check()
        return self._owner.current_project

    def LoadProject(self, name: str) -> FakeProject | None:  # noqa: N802
        self._check()
        if name not in self._owner.projects:
            return None
        self._owner.current_project = self._owner.projects[name]
        return self._owner.current_project

    def ExportProject(  # noqa: N802
        self,
        project_name: str,
        file_path: str,
        with_stills_and_luts: bool = False,
    ) -> bool:
        self._check()
        self.exports.append((project_name, file_path, with_stills_and_luts))
        if self.export_result:
            Path(file_path).write_bytes(b"fake-drp")
        return self.export_result


class FakeResolve:
    """A stand-in for the object ``scriptapp("Resolve")`` returns."""

    def __init__(
        self,
        projects: dict[str, FakeProject] | None = None,
        current: str | None = None,
        version: list[Any] | None = None,
    ) -> None:
        self.projects: dict[str, FakeProject] = projects or {}
        self.current_project: FakeProject | None = self.projects.get(current or "")
        self.version = version or [21, 0, 3, 15, ""]
        self.alive = True
        self.probe_count = 0
        self._project_manager = FakeProjectManager(self)

    def drop(self) -> None:
        """Simulate Resolve quitting: every call through this handle now fails."""
        self.alive = False

    def _check(self) -> None:
        if not self.alive:
            raise DroppedHandleError("The object is no longer valid")

    def GetVersion(self) -> list[Any]:  # noqa: N802
        self.probe_count += 1
        self._check()
        return list(self.version)

    def GetVersionString(self) -> str:  # noqa: N802
        self._check()
        return ".".join(str(part) for part in self.version[:3])

    def GetProductName(self) -> str:  # noqa: N802
        self._check()
        return "DaVinci Resolve Studio"

    def GetProjectManager(self) -> FakeProjectManager:  # noqa: N802
        self._check()
        return self._project_manager


class FakeConnector:
    """A ``connect`` callable that hands out fakes and counts attempts.

    ``handles`` is consumed one entry per connect attempt; ``None`` models a failed
    connect (Resolve not running), which is exactly what ``scriptapp`` returns.
    """

    def __init__(self, *handles: FakeResolve | None) -> None:
        self._handles: list[FakeResolve | None] = list(handles)
        self.attempts = 0

    def __call__(self) -> FakeResolve | None:
        self.attempts += 1
        if not self._handles:
            return None
        return self._handles.pop(0)


def studio(
    project: str | None = "sunset-set",
    timeline: str | None = "sunset-set v3",
    fps: str = "59.94",
    extra_projects: tuple[str, ...] = ("holiday-gig",),
) -> FakeResolve:
    """A conventional fake: Studio running, one project open, one timeline current."""
    projects: dict[str, FakeProject] = {}
    for name in extra_projects:
        projects[name] = FakeProject(name, fps=fps)
    if project is not None:
        projects[project] = FakeProject(
            project,
            FakeTimeline(timeline, fps) if timeline else None,
            fps=fps,
        )
    return FakeResolve(projects, current=project)
