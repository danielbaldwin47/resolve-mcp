"""Fake-tier tests for the PreToolUse shell guard in ``.claude/hooks/``.

Like ``test_read_guard.py``: the hook is executable config, so the process
boundary is the seam — each case drives the real script with the tool payload
on stdin and reads the exit code (2 = blocked, message on stderr).

Coverage follows #248: bare and piped noisy runs, ``gh`` views/diffs with and
without a redirect, whole-file vs ranged reads for every reader the rule names,
backslash and drive-letter paths, the ``--body`` false positives, heredoc
forms, the PowerShell tool, and the shared guarded-extension list.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1] / ".claude" / "hooks"
HOOK = HOOKS / "context-guard.py"
SETTINGS = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"


def run_hook(command: str, tool: str = "Bash") -> subprocess.CompletedProcess[str]:
    payload = {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {"command": command},
    }
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={"SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"), "PATH": ""},
    )


def blocked(command: str, tool: str = "Bash") -> str:
    """Assert the command is blocked; return the block message."""
    result = run_hook(command, tool)
    assert result.returncode == 2, f"expected block for {command!r}: {result.stderr}"
    assert result.stderr.startswith("Blocked (context discipline)")
    return result.stderr


def passes(command: str, tool: str = "Bash") -> None:
    result = run_hook(command, tool)
    assert result.returncode == 0, f"expected pass for {command!r}: {result.stderr}"


# --- noisy runs ---------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -m 'not live'",
        "uv run pytest tests/test_config.py -q",
        "pytest",
        "python -m pytest tests",
        "python3 -m pytest -q",
        "uv run mypy src tests",
        "uv run ruff check src",
        "CI=1 uv run pytest",
        "uv run pytest -q 2>&1",
        "cd src && uv run pytest",
    ],
)
def test_bare_noisy_run_is_blocked(command: str) -> None:
    msg = blocked(command)
    assert "pytest.scratch.log" in msg
    assert "> pytest.scratch.log 2>&1" in msg


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -q | tail -20",
        "uv run pytest -q 2>&1 | tail",
        "uv run mypy src | head -30",
        "uv run ruff check src | grep -c error",
        "uv run pytest 2>&1 | Select-Object -Last 20",
    ],
)
def test_piped_noisy_run_is_blocked(command: str) -> None:
    blocked(command)


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest -m 'not live' > pytest.scratch.log 2>&1",
        "uv run pytest tests/test_config.py -q > pytest.scratch.log 2>&1",
        "uv run mypy src tests > mypy.scratch.log 2>&1",
        "uv run ruff check src tests > ruff.scratch.log 2>&1",
        "uv run pytest -q >> pytest.scratch.log 2>&1",
        "uv run pytest -q &> pytest.scratch.log",
        "uv run pytest -q 2>&1 | Out-File pytest.scratch.log",
        "python -m pytest > pytest.scratch.log 2>&1",
    ],
)
def test_redirected_noisy_run_passes(command: str) -> None:
    passes(command)


@pytest.mark.parametrize(
    "command",
    [
        "grep -E 'FAILED|passed|error' pytest.scratch.log",
        "rm pytest.scratch.log ruff.scratch.log",
        "git commit -m 'fix: pytest run was flaky'",
        'git commit -m "run pytest | tail after this"',
        "echo pytest",
        "ls tests/test_pytest_things.py",
    ],
)
def test_prose_and_paths_mentioning_noisy_tools_pass(command: str) -> None:
    passes(command)


# --- gh views and diffs -------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "gh issue view 248",
        "gh issue view 248 --comments",
        "gh issue view 248 --json body -q .body",
        "gh pr view 252",
        "gh pr view 252 --json state,title -q .state",
        "gh pr diff 252",
        "gh pr diff 252 --name-only",
        "gh pr diff 252 | head -100",
        "gh issue view 248 --json body,comments 2>&1",
    ],
)
def test_unredirected_gh_view_or_diff_is_blocked(command: str) -> None:
    msg = blocked(command)
    assert "issue.scratch.log" in msg
    assert "gh pr diff <n> > pr.scratch.log" in msg


@pytest.mark.parametrize(
    "command",
    [
        "gh issue view 248 --json body -q .body > issue.scratch.log",
        "gh issue view 248 --comments > comments.scratch.log 2>&1",
        "gh pr view 252 --json body,comments > pr.scratch.log",
        "gh pr diff 252 > pr.scratch.log",
        "gh pr diff 252 > pr.scratch.log 2>&1",
        "gh pr checks 252",
        "gh pr list --json number,title",
        "gh issue list --label bug",
    ],
)
def test_redirected_gh_view_or_other_gh_passes(command: str) -> None:
    passes(command)


# --- the --body false positives -----------------------------------------------


def test_pr_create_body_mentioning_a_cat_loop_passes() -> None:
    """Observed false positive: the body's prose read as a `for … *.py … cat` sweep."""
    passes(
        "gh pr create --title 'refactor: hooks' --body \"Before: for f in src/*.py; "
        'do cat $f; done pulled 162KB into context. Now blocked."'
    )


def test_issue_comment_body_mentioning_a_tail_pipe_passes() -> None:
    """Observed false positive: an apostrophe inside the double-quoted body
    paired with a later one, unquoting `pytest … | tail` in between."""
    passes(
        'gh issue comment 248 --body "Don\'t run uv run pytest -q | tail -20; '
        "it caps one run. That's the rule.\""
    )


def test_pr_create_body_from_heredoc_passes() -> None:
    passes(
        "gh pr create --title 'x' --body \"$(cat <<'EOF'\n"
        "## Summary\n"
        "for f in tests/*.py; do cat $f; done — was the pattern.\n"
        "uv run pytest | tail is also gone. gh pr diff 1 too.\n"
        "EOF\n)\""
    )


def test_git_commit_message_mentioning_pytest_and_cat_passes() -> None:
    passes("git commit -m 'test: cat src/x.py and pytest | tail no more'")


# --- whole-file readers -------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "cat src/resolve_mcp/config.py",
        "cat -n src/resolve_mcp/config.py",
        "cat CLAUDE.md",
        "cat pyproject.toml",
        "cat pytest.scratch.log",
        "cat .claude/settings.json",
        "cat 'src/resolve_mcp/config.py'",
        'cat "docs/notes.md"',
        "cat src/a.py src/b.py",
        "cat src/x.py 2>&1",
        "cd src && cat x.py",
        "ls; cat src/x.py",
        "if true; then cat src/x.py; fi",
    ],
)
def test_cat_of_guarded_file_is_blocked(command: str) -> None:
    msg = blocked(command)
    assert "Read tool" in msg
    assert "offset/limit" in msg


@pytest.mark.parametrize(
    "command",
    [
        "cat src/x.py | grep -n def",
        "cat src/x.py | head -20",
        "cat src/x.py > /tmp/copy.py",
        "cat src/x.py >> combined.py",
        "cat > out.py <<'EOF'\nprint(1)\nEOF",
        "cat <<'EOF' > out.py\nprint(1)\nEOF",
        "cat <<EOF\nnot a file\nEOF",
        "cat frame.png",
        "cat Makefile",
        "cat",
        "echo cat src/x.py",
        "git cat-file -p HEAD:src/x.py",
    ],
)
def test_cat_piped_redirected_or_not_a_guarded_file_passes(command: str) -> None:
    passes(command)


def test_for_loop_cat_sweep_is_blocked() -> None:
    msg = blocked("for f in src/resolve_mcp/*.py; do cat $f; done")
    assert "cat loop" in msg


@pytest.mark.parametrize(
    "command",
    [
        "sed -n p src/x.py",
        "sed -n '1,$p' src/x.py",
        'sed -n "1,$p" src/x.py',
        "sed -n 1,\\$p src/x.py",
        "sed p src/x.py",
        "sed '' src/x.py",
        "sed -e p src/x.py",
        "sed -n p CLAUDE.md",
    ],
)
def test_sed_whole_file_is_blocked(command: str) -> None:
    blocked(command)


@pytest.mark.parametrize(
    "command",
    [
        "sed -n 10,40p src/x.py",
        "sed -n '10,40p' src/x.py",
        "sed -n 130,175p pr252.scratch.log",
        "sed -n '/^def /,/^$/p' src/x.py",
        "sed -n 5p src/x.py",
        "sed -i 's/old/new/' src/x.py",
        "sed -i '' 's/old/new/' src/x.py",
        "sed 's/a/b/' src/x.py > out.py",
        "sed -n p src/x.py | grep def",
        "sed -e 's/a/b/' -e 's/c/d/' src/x.py",
    ],
)
def test_sed_ranged_or_editing_passes(command: str) -> None:
    passes(command)


@pytest.mark.parametrize(
    "command",
    [
        "head src/x.py",
        "tail src/x.py",
        "head -c 4000 src/x.py",
        "head --bytes=400 src/x.py",
        "tail -f pytest.scratch.log",
        "head CLAUDE.md",
        "tail pytest.scratch.log",
    ],
)
def test_head_tail_without_count_is_blocked(command: str) -> None:
    blocked(command)


@pytest.mark.parametrize(
    "command",
    [
        "head -50 src/x.py",
        "head -n 50 src/x.py",
        "head -n50 src/x.py",
        "head --lines=50 src/x.py",
        "tail -20 pytest.scratch.log",
        "tail -n 20 pytest.scratch.log",
        "tail -n +5 src/x.py",
        "grep -n def src/x.py | head -5",
        "git log --oneline | head -20",
        "head src/x.py | grep import",
        "head src/x.py > first.txt",
    ],
)
def test_head_tail_with_count_or_no_file_passes(command: str) -> None:
    passes(command)


@pytest.mark.parametrize(
    "command",
    [
        "Get-Content src/x.py",
        "Get-Content -Path src\\resolve_mcp\\config.py",
        "Get-Content C:\\Users\\Daniel\\repos\\resolve-mcp\\src\\x.py",
        "Get-Content .\\CLAUDE.md",
        "Get-Content 'C:\\Users\\Daniel\\repos\\resolve-mcp\\pyproject.toml'",
        "Get-Content src/x.py -Raw",
        "gc src/x.py",
        "type src\\x.py",
        "type pytest.scratch.log",
        "cat .\\src\\x.py",
        "Get-Content $env:CLAUDE_JOB_DIR\\tmp\\out.log",
    ],
)
def test_powershell_whole_file_readers_are_blocked(command: str) -> None:
    blocked(command, tool="PowerShell")


@pytest.mark.parametrize(
    "command",
    [
        "Get-Content src/x.py -TotalCount 50",
        "Get-Content -TotalCount 50 src\\x.py",
        "Get-Content src/x.py -Tail 20",
        "Get-Content src/x.py -Head 20",
        "Get-Content src/x.py -First 20",
        "Get-Content src/x.py -Last 20",
        "Get-Content C:\\repo\\src\\x.py -TotalCount 50",
        "Get-Content src/x.py | Select-String def",
        "Get-Content src/x.py | Select-Object -Last 20",
        "Get-Content src/x.py > copy.py",
        "type python3",
        "Get-Content frame.png -Encoding Byte",
        "gc",
    ],
)
def test_powershell_ranged_or_piped_readers_pass(command: str) -> None:
    passes(command, tool="PowerShell")


def test_same_rules_apply_under_both_shell_tools() -> None:
    """A PowerShell command (`Get-Content x.py`) is blocked in the fake tier — #248 AC."""
    blocked("Get-Content x.py", tool="PowerShell")
    blocked("Get-Content x.py", tool="Bash")
    blocked("cat x.py", tool="PowerShell")
    blocked("uv run pytest -q", tool="PowerShell")
    blocked("gh pr diff 252", tool="PowerShell")


@pytest.mark.parametrize(
    "command",
    [
        "cat C:\\Users\\Daniel\\repos\\resolve-mcp\\src\\x.py",
        "cat .\\src\\x.py",
        "cat src\\x.py",
        "head C:/Users/Daniel/repos/resolve-mcp/src/x.py",
        "sed -n p C:\\repo\\src\\x.py",
    ],
)
def test_backslash_and_drive_letter_paths_are_recognised(command: str) -> None:
    blocked(command)


# --- heredoc and here-string forms --------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "python3 - <<'EOF'\nimport subprocess\nsubprocess.run(['pytest'])\nEOF",
        "cat <<'EOF' > tests/new.py\nfor f in src/*.py:\n    cat = 1\nEOF",
        "cat <<-EOF > x.md\n\tuv run pytest | tail\n\tEOF",
        "git commit -m @'\nrun pytest | tail\ncat src/x.py\n'@",
        'git commit -F - <<EOF\ncat src/x.py\ngh pr diff 3\nEOF',
    ],
)
def test_heredoc_and_herestring_bodies_are_data(command: str) -> None:
    passes(command)


def test_command_after_a_heredoc_is_still_checked() -> None:
    blocked("cat <<'EOF' > x.md\nhello\nEOF\ncat src/x.py")


# --- unrelated tools and payloads ---------------------------------------------


def test_other_tools_pass() -> None:
    passes("cat src/x.py", tool="Read")
    passes("uv run pytest", tool="Grep")


def test_garbage_payload_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        env={"SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"), "PATH": ""},
    )
    assert result.returncode == 0


def test_empty_command_passes() -> None:
    passes("")


# --- the shared guarded-extension list ----------------------------------------


def _load_guard_ext() -> frozenset[str]:
    spec = importlib.util.spec_from_file_location("guard_ext", HOOKS / "guard_ext.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ext: frozenset[str] = module.GUARDED_EXT
    return ext


def test_guarded_extension_list_is_defined_once_and_imported_by_both_hooks() -> None:
    """The one list lives in guard_ext.py; neither hook keeps its own literal."""
    ext = _load_guard_ext()
    assert {".py", ".toml", ".json", ".log", ".md"} <= ext
    for hook in ("context-guard.py", "read-guard.py"):
        source = (HOOKS / hook).read_text(encoding="utf-8")
        assert "from guard_ext import GUARDED_EXT" in source, hook
        assert not re.search(r"^GUARDED_EXT\s*=", source, re.M), hook
        assert not re.search(r"^SRC_EXT\s*=", source, re.M), hook


@pytest.mark.parametrize("ext", sorted(_load_guard_ext() - {".md"}))
def test_every_shared_extension_is_guarded_by_both_hooks(ext: str, tmp_path: Path) -> None:
    """Identical lists in practice: each extension blocks a `cat` here and a big
    whole-file Read in the Read guard (markdown is that guard's documented
    size-rule exemption, checked separately)."""
    blocked(f"cat src/file{ext}")

    big = tmp_path / f"file{ext}"
    big.write_text("".join(f"line {i}\n" for i in range(500)), encoding="utf-8")
    payload = {
        "session_id": "s-ext",
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(big)},
    }
    result = subprocess.run(
        [sys.executable, str(HOOKS / "read-guard.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env={
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
            "PATH": "",
            "TMPDIR": str(tmp_path),
            "TEMP": str(tmp_path),
            "TMP": str(tmp_path),
            "CLAUDE_PROJECT_DIR": str(tmp_path),
        },
    )
    assert result.returncode == 2, result.stderr


def test_markdown_is_cat_guarded_but_read_size_exempt(tmp_path: Path) -> None:
    blocked("cat CONTEXT.md")
    passes("cat CONTEXT.md | grep -n seam")


# --- settings.json wiring -----------------------------------------------------


def test_settings_match_both_shell_tools_for_the_context_guard() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    guards = [
        entry
        for entry in settings["hooks"]["PreToolUse"]
        if any("context-guard.py" in h["command"] for h in entry["hooks"])
    ]
    assert len(guards) == 1
    matcher = guards[0]["matcher"]
    assert re.fullmatch(matcher, "Bash")
    assert re.fullmatch(matcher, "PowerShell")
