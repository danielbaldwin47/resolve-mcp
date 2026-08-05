"""The escape hatch: scripting-API Python, results or structured errors, never a traceback."""

from __future__ import annotations

from resolve_mcp.tools.escape_hatch import run_python

from .conftest import Attach
from .fakes import studio


def test_returns_the_value_of_a_trailing_expression(attach: Attach) -> None:
    attach(studio())

    result = run_python("1 + 1")

    assert result["ok"] is True
    assert result["result"] == "2"
    assert result["stdout"] == ""


def test_binds_the_resolve_handles_into_the_namespace(attach: Attach) -> None:
    attach(studio(project="sunset-set", timeline="sunset-set v3"))

    result = run_python("[project.GetName(), timeline.GetName(), resolve.GetVersionString()]")

    assert result["result"] == "['sunset-set', 'sunset-set v3', '21.0.3']"


def test_reaches_resolve_through_the_project_manager(attach: Attach) -> None:
    attach(studio(project="sunset-set", extra_projects=("holiday-gig",)))

    result = run_python("sorted(project_manager.GetProjectListInCurrentFolder())")

    assert result["result"] == "['holiday-gig', 'sunset-set']"


def test_captures_stdout_from_multi_statement_code(attach: Attach) -> None:
    attach(studio())

    result = run_python("print('one')\nprint('two')\n40 + 2")

    assert result["stdout"] == "one\ntwo\n"
    assert result["result"] == "42"


def test_a_result_variable_stands_in_for_a_trailing_expression(attach: Attach) -> None:
    attach(studio())

    outcome = run_python("result = [c for c in 'abc']")

    assert outcome["result"] == "['a', 'b', 'c']"


def test_code_with_no_value_returns_no_result(attach: Attach) -> None:
    attach(studio())

    outcome = run_python("x = 1")

    assert outcome["ok"] is True
    assert outcome["result"] is None


def test_a_raised_exception_arrives_as_cause_and_fix(attach: Attach) -> None:
    attach(studio())

    result = run_python("project.NoSuchMethod()")

    assert result["ok"] is False
    assert result["error"]["code"] == "python_error"
    assert "AttributeError" in result["error"]["cause"]
    assert result["error"]["fix"]
    assert "Traceback" not in result["error"]["cause"]


def test_an_error_points_at_the_offending_line(attach: Attach) -> None:
    attach(studio())

    result = run_python("a = 1\nb = 2\nraise ValueError('nope')")

    assert result["error"]["detail"]["line"] == 3


def test_bad_syntax_is_reported_before_anything_runs(attach: Attach) -> None:
    attach(studio())

    result = run_python("print('unclosed'")

    assert result["ok"] is False
    assert "SyntaxError" in result["error"]["cause"]


def test_every_result_echoes_context(attach: Attach) -> None:
    attach(studio(project="sunset-set", timeline="sunset-set v3"))

    assert run_python("1")["context"]["timeline"] == "sunset-set v3"
    assert run_python("boom")["context"]["timeline"] == "sunset-set v3"


def test_runs_with_no_project_open(attach: Attach) -> None:
    attach(studio(project=None))

    result = run_python("[project, timeline]")

    assert result["ok"] is True
    assert result["result"] == "[None, None]"


def test_an_oversized_result_is_truncated_not_dumped(attach: Attach) -> None:
    attach(studio())

    result = run_python("list(range(100000))")

    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["result"]) < 30_000


def test_resolve_being_down_is_reported_before_the_code_runs(attach: Attach) -> None:
    attach(None)

    result = run_python("1 + 1")

    assert result["ok"] is False
    assert result["error"]["code"] == "resolve_unavailable"
