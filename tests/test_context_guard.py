"""Fake-tier tests for the PreToolUse shell guard in ``.claude/hooks/context-guard.py`` (#249).

Same seam as ``test_read_guard.py``: the hook is executable config, driven as a
subprocess with the tool payload on stdin; exit 2 plus a stderr message is a
block. Every rule, every documented bypass and both measured false positives
have a case here — a rule without a test is the one that gets regex-tightened
into silence.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_read_guard import hook_env

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
        env=hook_env(HOOKS),
    )


def blocked(command: str, tool: str = "Bash") -> str:
    """The block message, or '' when the command passes."""
    r = run_hook(command, tool)
    assert r.returncode in (0, 2), r.stderr
    return r.stderr if r.returncode == 2 else ""


# ------------------------------------------------------------ 1. noisy runs

BARE_NOISY = [
    "uv run pytest -m 'not live'",
    "pytest tests/test_x.py -q",
    "uv run mypy",
    "ruff check src",
    "python -m pytest tests",
    "CI=1 uv run pytest",
    "uv run pytest tests 2>&1",  # stderr dup is not a landing
    "uv run pytest tests 2> err.log",  # stderr only
    "uv run pytest tests | grep -E 'passed|FAILED'",  # a filter still floods the log
    "py -m pytest tests",
    "python.exe -m pytest",
    "uv.exe run pytest",
    "pytest.exe tests",
    "uv run --directory C:\\repo pytest",
    "uvx ruff check src",
    "$x = uv run pytest",
    "uv run pytest 2>&1 | Select-String passed",  # a filter, not a pager: still bare
]


@pytest.mark.parametrize("cmd", BARE_NOISY)
def test_bare_noisy_run_is_blocked_with_redirect_shape(cmd: str) -> None:
    msg = blocked(cmd)
    assert msg, cmd
    assert re.search(r"uv run (?:pytest|mypy|ruff check) > \w+\.scratch\.log 2>&1", msg), msg


PIPED_NOISY = [
    "uv run pytest | tail -20",
    "uv run pytest 2>&1 | head -50",
    "pytest tests | cat",
    "uv run mypy | tee mypy.log",
    "uv run pytest | tail -5 > pytest.scratch.log",  # tail before the landing
]


@pytest.mark.parametrize("cmd", PIPED_NOISY)
def test_noisy_piped_to_a_pager_is_blocked_with_the_tail_message(cmd: str) -> None:
    msg = blocked(cmd)
    assert "never pipe to tail/head" in msg, cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "uv run pytest 2>&1 | Select-Object -Last 20",
        "uv run pytest | select -First 5",
        "uv run mypy | Out-Host",
    ],
)
def test_powershell_pagers_count_as_pagers(cmd: str) -> None:
    assert "never pipe to tail/head" in blocked(cmd, "PowerShell"), cmd


LANDED_NOISY = [
    "uv run pytest -m 'not live' > pytest.scratch.log 2>&1",
    "uv run mypy > mypy.scratch.log 2>&1",
    "uv run ruff check > ruff.scratch.log 2>&1",
    "uv run pytest tests/test_x.py 1> pytest.scratch.log 2>&1",
    "uv run pytest tests &> pytest.scratch.log",
    "uv run pytest > pytest.scratch.log 2>&1; uv run mypy > mypy.scratch.log 2>&1",
    "uv run pytest tests/test_x.py >> pytest.scratch.log 2>&1",
]


@pytest.mark.parametrize("cmd", LANDED_NOISY)
def test_noisy_run_landing_in_a_file_passes(cmd: str) -> None:
    assert blocked(cmd) == "", cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "uv run pytest *> pytest.scratch.log",
        "uv run pytest 2>&1 | Out-File pytest.scratch.log",
        "uv run mypy | Set-Content mypy.scratch.log",
        "uv run pytest | Out-Null",
    ],
)
def test_powershell_landings_pass(cmd: str) -> None:
    assert blocked(cmd, "PowerShell") == "", cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "uv run pytest --version",
        "pytest --help",
        "uv run pytest --collect-only -q tests/test_x.py > co.scratch.log",
        "grep -E 'FAILED|passed' pytest.scratch.log",  # the log's name is not a run
        "git commit -m 'run pytest before pushing'",  # prose in quotes
        "echo pytest",  # not at command position
    ],
)
def test_noisy_lookalikes_pass(cmd: str) -> None:
    assert blocked(cmd) == "", cmd


def test_second_statement_without_landing_is_still_caught() -> None:
    assert blocked("uv run pytest > pytest.scratch.log 2>&1 && uv run mypy")


# ------------------------------------------------------------ 2. gh views

GH_BLOCKED = [
    "gh issue view 249",
    "gh issue view 249 --comments",
    "gh pr view 243",
    "gh pr diff 243",
    "gh issue view 249 --json body -q .body",  # the body, unredirected
    "gh issue view 249 --json comments -q '.comments[].body'",
    "gh pr view 5 --json body,title",  # no filter at all
    "gh pr diff 243 | grep '^+'",  # a filter is not a landing
    "gh issue view 249 --json title,body --template '{{.body}}'",
    "gh issue view 249 --json bodyText -q .bodyText",
]


@pytest.mark.parametrize("cmd", GH_BLOCKED)
def test_gh_view_without_landing_is_blocked(cmd: str) -> None:
    msg = blocked(cmd)
    assert msg, cmd
    assert "issue.scratch.log" in msg or "comments.scratch.log" in msg, msg


GH_PASSES = [
    "gh issue view 249 --json body -q .body > issue.scratch.log",
    "gh issue view 249 --comments > comments.scratch.log",
    "gh pr diff 243 > pr.scratch.log",
    "gh pr view 243 --json state -q .state",  # a field filter
    "gh issue view 249 --json title,labels -q '.title'",
    "gh pr diff 243 --name-only",
    "gh pr diff 243 --stat",
    "gh issue view 249 --json body,title -q .title",  # the filter picks a small field
    "gh issue view 249 --json title,url --template '{{.title}}'",
    "gh pr view 5 -t '{{.state}}' --json state",
    "gh pr list --state merged --json headRefName",  # not a view
    "gh issue comment 249 --body 'see gh issue view 5'",  # prose
    "gh issue view 248 --web",  # opens the browser (#248 probe set)
    "gh pr view 252 -w",
]


@pytest.mark.parametrize("cmd", GH_PASSES)
def test_gh_view_landing_or_filtered_passes(cmd: str) -> None:
    assert blocked(cmd) == "", cmd


# ------------------------------------------------------ 3. silent ticket closes

CLOSE_BLOCKED = [
    "gh issue close 251",
    "gh issue close 251 --reason completed",
    "gh issue close #251",
    "gh issue close 251 -R danielbaldwin47/resolve-mcp",
    "gh issue comment 249 --body 'done' && gh issue close 251",  # a different ticket
    "for n in 167 168; do gh issue close $n; done",
    'gh issue close 251 --comment ""',  # an empty record is no record
    "gh issue close 251 -c ''",
]


@pytest.mark.parametrize("cmd", CLOSE_BLOCKED)
def test_closing_a_ticket_without_a_comment_is_blocked(cmd: str) -> None:
    msg = blocked(cmd)
    assert msg, cmd
    assert "implementation record" in msg and "--comment" in msg, msg


def test_powershell_tool_silent_close_is_blocked() -> None:
    assert "implementation record" in blocked("gh issue close 251", "PowerShell")


CLOSE_PASSES = [
    'gh issue close 251 --comment "landed as PR #260; live ACs run"',
    "gh issue close 251 -c 'see PR #260'",
    "gh issue close 251 --comment='see PR #260'",
    'gh issue close 251 --reason completed --comment "see PR #260"',
    # The long record posted first, then the close, in one command.
    "gh issue comment 251 -F - <<'EOF'\n## What landed\n...\nEOF\ngh issue close 251",
    "gh issue comment 251 --body 'ready; do not gh issue close 251 yet'",  # prose
    "gh pr close 260",  # a PR, not a ticket
    "gh issue list --state closed",
]


@pytest.mark.parametrize("cmd", CLOSE_PASSES)
def test_a_close_carrying_its_record_passes(cmd: str) -> None:
    assert blocked(cmd) == "", cmd


# ------------------------------------------------------------ 4. whole-file dumps

WHOLE_FILE_DUMPS = [
    "cat src/resolve_mcp/config.py",
    "cat -n tests/conftest.py",
    "cat notes.md",
    "cat pytest.scratch.log",
    "cat 'src/my file.py'",
    "cat < src/config.py",
    "ls; cat pyproject.toml",
    "cat src/a.py 2>&1",
    "cat src/a.py | cat -n",  # a re-emitter is not a filter
    "more README.md",
    "less src/config.py",
    "type src\\config.py",
    "sed -n p src/config.py",
    "sed -n '1,$p' src/config.py",
    "sed -n \"1,$p\" src/config.py",
    "sed '' src/config.py",
    "head src/config.py",
    "tail src/config.py",
    "tail -f pytest.scratch.log",
    "head -c 4000 src/config.py",
    "head --bytes=4000 src/config.py",
    "Get-Content src/config.py",
    "Get-Content -Path .claude/settings.json",
    "gc src/config.py -Raw",
    "Get-Content src/config.py | Out-String",
    "cat src/config.py | nl",
    "$c = Get-Content src/config.py; $c",
    "cat src/config.py || true",  # `||` is not a pipe
    "sed --quiet p src/config.py",
    'sed -n "1,\\$p" src/config.py',
    "tail -n +10 src/config.py",  # an offset with no end is not a range
    # From the #248 probe set (merged alongside #249):
    "cat src/X.PY",  # extension match is case-insensitive
    r"cat SRC\CONFIG.JSON",
    r"Get-Content SRC\X.PY",
    "sed -n '1,$ p' src/x.py",
    "sed -n '$!p' src/x.py",
    "Get-Content src/x.py | Write-Output",  # pass-through, not a filter
    "Get-Content src/x.py | Format-Table",
    "gc src/x.py -tal 5",  # not a prefix of any bounding parameter
    "gc src/x.py -t 5",  # a single letter is ambiguous, not a bound
]


@pytest.mark.parametrize("cmd", WHOLE_FILE_DUMPS)
def test_whole_file_dump_of_guarded_file_is_blocked(cmd: str) -> None:
    msg = blocked(cmd)
    assert msg, cmd
    assert "whole-file dump" in msg and "offset/limit" in msg, msg


def test_powershell_tool_get_content_is_blocked() -> None:
    assert "whole-file dump" in blocked("Get-Content x.py", "PowerShell")


def test_powershell_tool_cat_alias_and_type_are_blocked() -> None:
    assert blocked("cat x.py", "PowerShell")
    assert blocked("type x.py", "PowerShell")


RANGED_READS = [
    "sed -n 10,40p src/config.py",
    "sed -n '120,160p' src/config.py",
    "sed -n 5p src/config.py",
    "sed -n '/def main/,/^$/p' src/config.py",
    "sed -i 's/a/b/' src/config.py",  # an edit, not a read
    "head -50 src/config.py",
    "head -n 50 src/config.py",
    "head -n50 src/config.py",
    "head --lines=50 src/config.py",
    "tail -n 20 pytest.scratch.log",
    "tail -20 pytest.scratch.log",
    "Get-Content src/config.py -TotalCount 50",
    "Get-Content -TotalCount 50 src/config.py",
    "Get-Content src/config.py -Tail 20",
    "gc src/config.py -Head 20",
    "Get-Content src/config.py | Select-Object -First 30",
    "Get-Content src/config.py | Select-String -Pattern def",
    "(Get-Content src/config.py).Count",
    "(Get-Content src/config.py)[10..40]",
    "gc src/x.py -tot 5",  # PowerShell accepts any unambiguous parameter prefix
    "Get-Content src/x.py -Total 5",
]


@pytest.mark.parametrize("cmd", RANGED_READS)
def test_ranged_read_passes(cmd: str) -> None:
    assert blocked(cmd) == "", cmd
    assert blocked(cmd, "PowerShell") == "", cmd


DUMP_ESCAPES = [
    "cat src/config.py | grep -n def",
    "cat src/config.py | wc -l",
    "cat src/a.py src/b.py > combined.txt",
    "cat notes.md >> all.md",
    "cat > new.py <<'EOF'\nprint('hi')\nEOF",
    "cat <<EOF > out.txt\ncat src/config.py\nEOF",  # a cat inside a heredoc body
    "cat image.png",  # unguarded extension
    "cat file_without_extension",
    "wc -l src/config.py",
    "grep -n def src/config.py",
    "git diff origin/main -- src/config.py",
    "python scripts/prune_merged.py > prune.scratch.log 2>&1",
    "type python",  # bash `type` on a command name
    "echo 'cat src/config.py'",
    "head -20 pytest.scratch.log | grep FAILED",
    "cat src/config.py.bak",  # a backup, not the file
    "cat src/config.py.orig src/config.py.rej",
    "cat src/config.py~",
    "cat notes.md.gz | zcat | grep x",
    "for f in a.py b.py; do cat $f > $f.bak; done",  # each cat lands
    "for f in src/*.py; do cat $f; done > all.txt",  # the loop lands
    "for f in src/*.py; do grep -c def $f; done # cat",
    "for f in *.py; do wc -l $f; done",
    "for f in src/*.py; do cat $f; done | grep -c import",  # the loop's output is filtered
    "git diff src/a.py | cat",  # the no-pager idiom: nothing is dumped
    "git log --oneline -- src/a.py | cat",
    "grep -n def src/a.py | cat -n",
    "head -50 src/a.py | cat -A",
    "python -c \"print('%s' % 'a.py')\" && echo '{ cat }'",
    "if [ -f src/a.py ]; then echo hi; fi",
]


@pytest.mark.parametrize("cmd", DUMP_ESCAPES)
def test_dump_lookalikes_and_landings_pass(cmd: str) -> None:
    assert blocked(cmd) == "", cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "cat C:\\Users\\me\\repo\\src\\config.py",
        "cat .\\src\\config.py",
        "type C:\\repo\\pyproject.toml",
        "Get-Content C:\\repo\\.claude\\settings.json",
        "cat 'C:\\Program Files\\x\\config.py'",
        "head C:/repo/src/config.py",
    ],
)
def test_backslash_and_drive_letter_paths_are_seen(cmd: str) -> None:
    assert "whole-file dump" in blocked(cmd), cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "for f in src/*.py; do cat $f; done",
        "for f in src/*.py; do cat $f | cat -n; done",
        "foreach ($f in ls *.py) { cat $f }",
        "Get-ChildItem *.py | % { gc $_ }",
        "Get-ChildItem -r *.py | ForEach-Object { Get-Content $_ }",
        "for f in src/*.py; do echo ${f}; cat $f; done",
        "for f in src/*.py; do if grep -q foo $f; then cat $f; fi; done",
        "for f in src/*.py; do echo \"$f: $(cat $f)\"; done",
        "for f in src/*.py; do cat $f || true; done",
        "for f in src/*.py\ndo\n  cat $f\ndone",
        "find src -name '*.py' | while read f; do cat $f; done",
    ],
)
def test_loop_over_source_files_is_blocked(cmd: str) -> None:
    assert "cat loop" in blocked(cmd), cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "ls src/*.py | xargs cat",
        "find src -name '*.py' -exec cat {} +",
        "find src -name '*.py' -exec cat {} \\;",
        "Get-ChildItem *.py | Get-Content",
        "gci -r *.py | gc",
    ],
)
def test_reader_fed_by_pipe_xargs_or_exec_is_blocked(cmd: str) -> None:
    assert "whole-file dump" in blocked(cmd), cmd


@pytest.mark.parametrize(
    "cmd",
    [
        "ls src/*.py | xargs cat | grep def",
        "find src -name '*.py' -exec cat {} + > all.txt",
        "Get-ChildItem *.py | Get-Content | Select-String def",
        "ls src/*.py | xargs wc -l",
    ],
)
def test_reader_fed_by_pipe_but_filtered_or_landing_passes(cmd: str) -> None:
    assert blocked(cmd) == "", cmd


# ------------------------------------------------------------ false positives

FALSE_POSITIVES = [
    # Measured 2026-08-15: a PR body that talks about cat/tail is prose.
    "gh pr create --title 'x' --body 'Review: run cat src/a.py | tail -5 first'",
    "gh issue comment 249 --body \"never cat pytest.scratch.log | tail\"",
    "gh pr create --body='cat foo.py'",
    "git commit -m 'sed -n p src/config.py was the bug'",
    "gh issue create --title 'cat src/a.py hangs' --body 'see head -c 10 x.py'",
]


@pytest.mark.parametrize("cmd", FALSE_POSITIVES)
def test_body_and_message_arguments_are_never_inspected(cmd: str) -> None:
    assert blocked(cmd) == "", cmd


HEREDOC_FORMS = [
    "gh pr create --body-file - <<'EOF'\ncat src/a.py | tail -3\nuv run pytest\nEOF",
    "gh issue comment 249 -F - <<EOF\ngh issue view 5\nEOF",
    "cat <<-EOF\n\tcat src/a.py\n\tEOF",
    "python - <<'PY'\nprint(open('src/a.py').read())\nPY",
    "git commit -F - <<'MSG'\nfix: cat src/config.py no longer used\nMSG",
]


@pytest.mark.parametrize("cmd", HEREDOC_FORMS)
def test_heredoc_bodies_are_data(cmd: str) -> None:
    assert blocked(cmd) == "", cmd


def test_powershell_here_string_body_is_data() -> None:
    cmd = "gh pr create --body @'\ncat src/a.py | tail -3\nuv run pytest\n'@"
    assert blocked(cmd, "PowerShell") == ""


def test_command_after_a_heredoc_is_still_checked() -> None:
    cmd = "cat > x.txt <<'EOF'\nhello\nEOF\ncat src/config.py"
    assert "whole-file dump" in blocked(cmd)


# ------------------------------------------------------------ plumbing


def test_other_tools_pass() -> None:
    assert blocked("cat src/config.py", tool="Read") == ""
    assert blocked("uv run pytest", tool="Edit") == ""


def test_malformed_stdin_passes() -> None:
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        env=hook_env(HOOKS),
    )
    assert r.returncode == 0


def test_settings_match_both_shell_tools_for_the_guard() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    entries = [
        e
        for e in settings["hooks"]["PreToolUse"]
        if any("context-guard.py" in h["command"] for h in e["hooks"])
    ]
    assert entries, "context-guard.py is not wired as a PreToolUse hook"
    for tool in ("Bash", "PowerShell"):
        assert any(re.fullmatch(e["matcher"], tool) for e in entries), tool


def test_guarded_extension_list_is_defined_once_and_shared() -> None:
    shared = HOOKS / "guarded_ext.py"
    assert shared.exists()
    for hook in ("context-guard.py", "read-guard.py"):
        src = (HOOKS / hook).read_text(encoding="utf-8")
        assert re.search(r"^from guarded_ext import ", src, re.M), hook
        assert not re.search(r"^(?:GUARDED_EXT|SRC_EXT)\s*=", src, re.M), (
            f"{hook} carries its own extension list"
        )
    # And the two hooks agree by construction: run each module's view of the list.
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from guarded_ext import GUARDED_EXT, GUARDED_EXT_RE, SIZE_RULE_EXEMPT_NAMES; "
        "print(sorted(GUARDED_EXT)); print(GUARDED_EXT_RE); print(sorted(SIZE_RULE_EXEMPT_NAMES))"
    )
    r = subprocess.run(
        [sys.executable, "-c", probe, str(HOOKS)], capture_output=True, text=True, check=True
    )
    exts, alternation, exempt = r.stdout.splitlines()
    assert ".md" in exts and ".toml" in exts and ".py" in exts
    assert set(alternation.split("|")) == {e.lstrip(".") for e in ast.literal_eval(exts)}
    assert exempt == "['claude.md']"  # #247: markdown counts; only CLAUDE.md is exempt
