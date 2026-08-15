"""Fake-tier tests for the PreToolUse ``Read`` guard in ``.claude/hooks/``.

The hook is executable config: it runs as a subprocess with the tool payload on
stdin and signals a block with exit 2 plus a message on stderr. That process
boundary *is* the seam, so these tests drive the real script the same way the
harness does — no import, no monkeypatching.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "read-guard.py"


def hook_env(tmp_path: Path, project_dir: Path | None = None) -> dict[str, str]:
    """A hermetic environment for one test.

    The temp vars matter: the hook keeps its per-session edited-paths state under
    ``tempfile.gettempdir()``, so pointing them at ``tmp_path`` is what keeps one
    test's session file from leaking into the next — and off the developer's real
    temp dir. ``SYSTEMROOT`` is what a bare Python needs to start on Windows.
    """
    env = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        "PATH": "",
        "TMPDIR": str(tmp_path),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    }
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    return env


def run_hook(
    payload: dict[str, object],
    project_dir: Path,
    tmp_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the hook exactly as the harness does: JSON on stdin, exit code out."""
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=hook_env(tmp_path if tmp_path is not None else project_dir, project_dir),
    )


def read_event(path: Path, session: str = "s1", **extra: object) -> dict[str, object]:
    return {
        "session_id": session,
        "hook_event_name": "PreToolUse",
        "tool_name": "Read",
        "tool_input": {"file_path": str(path), **extra},
    }


def edit_event(path: Path, session: str) -> dict[str, object]:
    return {
        "session_id": session,
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path)},
    }


def write_lines(path: Path, count: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"line {i}\n" for i in range(count)), encoding="utf-8")
    return path


def test_blocks_whole_file_read_of_big_source_file(tmp_path: Path) -> None:
    big = write_lines(tmp_path / "src" / "timeline.py", 656)

    result = run_hook(read_event(big), tmp_path)

    assert result.returncode == 2
    assert "656" in result.stderr
    assert "grep" in result.stderr.lower()
    assert "offset" in result.stderr


def test_ranged_read_of_big_file_passes(tmp_path: Path) -> None:
    big = write_lines(tmp_path / "src" / "timeline.py", 656)

    result = run_hook(read_event(big, offset=120, limit=60), tmp_path)

    assert result.returncode == 0


def test_deliberate_whole_file_range_is_the_escape_hatch(tmp_path: Path) -> None:
    """``offset: 1, limit: <n>`` stays possible — the hook is a speed bump."""
    big = write_lines(tmp_path / "src" / "timeline.py", 656)

    result = run_hook(read_event(big, offset=1, limit=656), tmp_path)

    assert result.returncode == 0


def test_bare_offset_is_not_an_escape(tmp_path: Path) -> None:
    """``offset`` alone still pulls Read's 2000-line default — the whole file."""
    big = write_lines(tmp_path / "src" / "timeline.py", 656)

    result = run_hook(read_event(big, offset=1), tmp_path)

    assert result.returncode == 2


def test_small_source_file_passes(tmp_path: Path) -> None:
    small = write_lines(tmp_path / "src" / "config.py", 400)

    result = run_hook(read_event(small), tmp_path)

    assert result.returncode == 0


def test_big_markdown_blocks_like_code(tmp_path: Path) -> None:
    """The map cannot silently regrow: markdown over the limit is a ranged read."""
    doc = write_lines(tmp_path / "CONTEXT.md", 523)

    result = run_hook(read_event(doc), tmp_path)

    assert result.returncode == 2
    assert "523 lines" in result.stderr


def test_markdown_at_the_limit_passes(tmp_path: Path) -> None:
    """Same limit as code: 400 lines is in, 401 is out."""
    doc = write_lines(tmp_path / "CONTEXT.md", 400)

    result = run_hook(read_event(doc), tmp_path)

    assert result.returncode == 0


def test_ranged_read_of_big_markdown_passes(tmp_path: Path) -> None:
    doc = write_lines(tmp_path / "docs" / "context" / "analysis.md", 900)

    result = run_hook(read_event(doc, offset=40, limit=30), tmp_path)

    assert result.returncode == 0


@pytest.mark.parametrize("name", ["CLAUDE.md", "claude.md"])
def test_claude_md_is_exempt_at_any_size(tmp_path: Path, name: str) -> None:
    """Read whole at session start by design; the exemption is by name, any case."""
    doc = write_lines(tmp_path / name, 900)

    result = run_hook(read_event(doc), tmp_path)

    assert result.returncode == 0


def test_out_of_repo_path_passes(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    project.mkdir()
    outside = write_lines(tmp_path / "jobdir" / "tmp" / "dump.py", 900)

    result = run_hook(read_event(outside), project)

    assert result.returncode == 0


def test_missing_project_dir_env_falls_back_to_cwd(tmp_path: Path) -> None:
    """With ``CLAUDE_PROJECT_DIR`` unset the hook still guards the tree it runs in."""
    big = write_lines(tmp_path / "src" / "timeline.py", 656)
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(read_event(big)),
        capture_output=True,
        text=True,
        env=hook_env(tmp_path),
        cwd=str(tmp_path),
    )

    assert result.returncode == 2


def test_unreadable_file_passes(tmp_path: Path) -> None:
    result = run_hook(read_event(tmp_path / "src" / "gone.py"), tmp_path)

    assert result.returncode == 0


def test_binary_extension_passes(tmp_path: Path) -> None:
    """Images and PDFs have no line concept — the size rule must not touch them."""
    blob = tmp_path / "assets" / "frame.png"
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200_000)

    result = run_hook(read_event(blob), tmp_path)

    assert result.returncode == 0


@pytest.mark.parametrize("name", ["pytest.scratch.log", "pyproject.toml", "ci.yml", "hook.js"])
def test_other_guarded_extensions_block_when_big(tmp_path: Path, name: str) -> None:
    big = write_lines(tmp_path / name, 500)

    result = run_hook(read_event(big), tmp_path)

    assert result.returncode == 2


def test_edited_file_rule_still_blocks_whole_file_reread(tmp_path: Path) -> None:
    """The pre-existing read-after-edit behaviour is unchanged."""
    small = write_lines(tmp_path / "src" / "config.py", 20)
    session = "session-reread"

    record = run_hook(edit_event(small, session), tmp_path)
    assert record.returncode == 0

    result = run_hook(read_event(small, session=session), tmp_path)

    assert result.returncode == 2
    assert "already edited" in result.stderr


def test_edited_file_ranged_reread_still_passes(tmp_path: Path) -> None:
    small = write_lines(tmp_path / "src" / "config.py", 20)
    session = "session-ranged"

    run_hook(edit_event(small, session), tmp_path)
    result = run_hook(read_event(small, session=session, offset=5, limit=5), tmp_path)

    assert result.returncode == 0


def test_malformed_stdin_passes(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        env=hook_env(tmp_path, tmp_path),
    )

    assert result.returncode == 0
