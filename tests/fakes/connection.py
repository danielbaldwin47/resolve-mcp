"""The root of the object graph: ``FakeResolve`` and the ``FakeConnector`` that hands it out."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .core import DroppedHandleError
from .project import FakeProjectManager

if TYPE_CHECKING:
    from .project import FakeProject


EXPORT_TYPES: tuple[str, ...] = (
    # First in the tuple, so its value is 0 — the export constants are plain numbers and
    # one of them is falsy, which is the trap a "constant or default" lookup falls into.
    "EXPORT_AAF",
    "EXPORT_DRT",
    "EXPORT_EDL",
    "EXPORT_FCP_7_XML",
    "EXPORT_FCPXML_1_3",
    "EXPORT_FCPXML_1_4",
    "EXPORT_FCPXML_1_5",
    "EXPORT_FCPXML_1_6",
    "EXPORT_FCPXML_1_7",
    "EXPORT_FCPXML_1_8",
    "EXPORT_FCPXML_1_9",
    "EXPORT_FCPXML_1_10",
    "EXPORT_OTIO",
)


class FakeResolve:
    """A stand-in for the object ``scriptapp("Resolve")`` returns."""

    def __init__(
        self,
        projects: dict[str, FakeProject] | None = None,
        current: str | None = None,
        version: list[Any] | None = None,
        export_types: Sequence[str] | None = None,
    ) -> None:
        self.projects: dict[str, FakeProject] = projects or {}
        self.current_project: FakeProject | None = self.projects.get(current or "")
        self.version = version or [21, 0, 3, 15, ""]
        # Export types are attributes on the app object itself, and an older build simply
        # does not have the newer ones — so a narrowed tuple models that build exactly.
        self.export_types = tuple(EXPORT_TYPES if export_types is None else export_types)
        for value, export_type in enumerate(self.export_types):
            setattr(self, export_type, value)
        self.alive = True
        self.probe_count = 0
        self.fail_version_string = False
        self._calls_left: int | None = None
        self._project_manager = FakeProjectManager(self)

    def drop(self) -> None:
        """Simulate Resolve quitting: every call through this handle now fails."""
        self.alive = False

    def die_after(self, calls: int) -> None:
        """Survive ``calls`` more calls, then die — Resolve quitting mid-operation.

        With ``calls=1`` the handle passes the connection's probe and dies on the very
        next call, which is the case a probe alone cannot catch.
        """
        self._calls_left = calls

    def _check(self) -> None:
        if self._calls_left is not None:
            if self._calls_left <= 0:
                self.alive = False
            else:
                self._calls_left -= 1
        if not self.alive:
            raise DroppedHandleError("The object is no longer valid")

    def GetVersion(self) -> list[Any]:  # noqa: N802
        self.probe_count += 1
        self._check()
        return list(self.version)

    def GetVersionString(self) -> str:  # noqa: N802
        self._check()
        if self.fail_version_string:
            raise RuntimeError("version string unavailable")
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
