"""The ``run_python`` escape hatch.

Runs scripting-API Python in the server process with the Resolve handles pre-bound, so an
API gap never dead-ends a session. Failures come back as cause/fix like every other
error; the traceback goes to the stderr log.
"""

from __future__ import annotations

import ast
import contextlib
import io
import traceback
from typing import Any

from ..errors import PythonExecutionError
from ..logging_config import get_logger
from .connection import ResolveConnection

log = get_logger("scripting")

FILENAME = "<run_python>"
RESULT_NAME = "result"
MAX_RESULT_CHARS = 20_000
TRUNCATION_NOTE = "… [truncated]"


def run_python(
    connection: ResolveConnection,
    code: str,
    max_result_chars: int = MAX_RESULT_CHARS,
) -> dict[str, Any]:
    """Execute ``code`` and return its value, its stdout, and whether the value was cut short.

    The value is the trailing expression if there is one, else a ``result`` variable if the
    code sets one, else nothing.
    """
    namespace = _namespace(connection)
    body, trailing = _parse(code)
    stdout = io.StringIO()

    try:
        with contextlib.redirect_stdout(stdout):
            if body:
                exec(compile(ast.Module(body=body, type_ignores=[]), FILENAME, "exec"), namespace)
            if trailing is not None:
                value = eval(  # noqa: S307 - executing agent-authored code is the point
                    compile(ast.Expression(trailing), FILENAME, "eval"), namespace
                )
            else:
                value = namespace.get(RESULT_NAME)
    except BaseException as exc:  # noqa: BLE001 - anything the code raises is a tool result
        log.warning("run_python raised", exc_info=True)
        raise PythonExecutionError(
            cause=f"{type(exc).__name__}: {exc}",
            detail={"line": _failing_line(exc), "stdout": stdout.getvalue()},
        ) from exc

    rendered, truncated = _render(value, max_result_chars)
    return {"result": rendered, "stdout": stdout.getvalue(), "truncated": truncated}


def _namespace(connection: ResolveConnection) -> dict[str, Any]:
    resolve = connection.handle()
    manager = resolve.GetProjectManager()
    project = manager.GetCurrentProject() if manager is not None else None
    timeline = project.GetCurrentTimeline() if project is not None else None
    return {
        "__name__": "resolve_mcp_escape_hatch",
        "__builtins__": __builtins__,
        "resolve": resolve,
        "project_manager": manager,
        "project": project,
        "timeline": timeline,
    }


def _parse(code: str) -> tuple[list[ast.stmt], ast.expr | None]:
    """Split the code into statements plus an optional trailing expression to evaluate."""
    try:
        module = ast.parse(code)
    except SyntaxError as exc:
        raise PythonExecutionError(
            cause=f"SyntaxError: {exc.msg}",
            detail={"line": exc.lineno, "offset": exc.offset},
        ) from exc
    if module.body and isinstance(module.body[-1], ast.Expr):
        return module.body[:-1], module.body[-1].value
    return module.body, None


def _failing_line(exc: BaseException) -> int | None:
    """The line of the executed code that raised — not the server's own frames."""
    for frame in reversed(traceback.extract_tb(exc.__traceback__)):
        if frame.filename == FILENAME:
            return frame.lineno
    return None


def _render(value: Any, max_chars: int) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    try:
        rendered = repr(value)
    except Exception as exc:  # noqa: BLE001 - a hostile __repr__ is not worth a failed call
        rendered = f"<unreprable {type(value).__name__}: {exc}>"
    if len(rendered) > max_chars:
        return rendered[:max_chars] + TRUNCATION_NOTE, True
    return rendered, False
