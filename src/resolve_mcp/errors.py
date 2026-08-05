"""Structured errors.

Every failure that reaches the agent arrives as ``cause`` (what went wrong, in one
sentence) plus ``fix`` (what to do about it). Raw tracebacks go to the stderr log, never
into a tool result.
"""

from __future__ import annotations

from typing import Any

RESOLVE_FIX = (
    "Launch DaVinci Resolve Studio, open a project, and make sure "
    "Preferences > System > General > External scripting using is set to Local. "
    "Then retry."
)


class ResolveMcpError(Exception):
    """A failure the agent can act on."""

    code = "error"
    default_fix = "Retry, or use run_python to inspect the scripting API directly."

    def __init__(
        self,
        cause: str,
        fix: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(cause)
        self.cause = cause
        self.fix = fix or self.default_fix
        self.detail = detail or {}

    def payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "cause": self.cause,
            "fix": self.fix,
            "detail": self.detail,
        }


class ResolveUnavailableError(ResolveMcpError):
    """Resolve is not running, not reachable, or dropped its handle mid-session."""

    code = "resolve_unavailable"
    default_fix = RESOLVE_FIX


class UnsupportedInterpreterError(ResolveMcpError):
    """This Python cannot load the Resolve scripting library without crashing."""

    code = "unsupported_interpreter"


class NoProjectOpenError(ResolveMcpError):
    code = "no_project_open"
    default_fix = (
        "Open a project first — list_projects shows what is available, open_project loads one."
    )


class ProjectNotFoundError(ResolveMcpError):
    code = "project_not_found"

    def __init__(self, name: str, available: list[str]) -> None:
        listed = ", ".join(available) if available else "none in the current database folder"
        super().__init__(
            cause=f"No project named {name!r} in the current database folder.",
            fix=f"Use one of these names exactly, or switch database folder: {listed}.",
            detail={"requested": name, "available": available},
        )


class SnapshotFailedError(ResolveMcpError):
    code = "snapshot_failed"
    default_fix = (
        "Check the target directory is writable and that the project is not mid-render, "
        "then retry. A different path can be passed explicitly."
    )


class PythonExecutionError(ResolveMcpError):
    """The escape-hatch code raised. The traceback is logged, not returned."""

    code = "python_error"
    default_fix = (
        "Fix the code and retry. get_status confirms what is currently open; "
        "the namespace holds resolve, project_manager, project and timeline."
    )


class InternalError(ResolveMcpError):
    code = "internal_error"
    default_fix = (
        "This is a bug in resolve-mcp rather than a Resolve state problem. "
        "Retry once; if it persists, work around it with run_python and report it."
    )
