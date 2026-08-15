#!/usr/bin/env python3
"""Read guards, all three in one script (dispatched on hook_event_name).

PostToolUse on Edit/Write/MultiEdit: record the edited path, keyed by session.
PreToolUse on Read, blocking (exit 2) on either rule:
  1. Re-read: a whole-file Read of a path this session already edited — the
     content is in context and Edit/Write fail loudly on a miss, so the re-read
     buys nothing. Any offset/limit is the escape: wanting a different section
     of a big file is legitimate.
  2. Big first read: a whole-file Read of a guarded-extension repo file
     (guarded_ext.py, shared with context-guard.py) over BIG_FILE_LINES — CLAUDE.md's "ranged (grep first)
     on big files". Here the escape is `limit`, since a bare `offset` still
     pulls Read's 2000-line default. A deliberate `offset: 1, limit: <n>` passes,
     so this is a speed bump rather than a wall.

Measured motivation (2026-08-05 transcript audit): read-after-edit ran ~46% of
sessions pre-rules and ~39% after prose alone — the one axis prose never moved;
and one session full-read a 656-line module three times under the grep-first
prose rule.

starter-version: 2026-08-10 (claude-principles; matches the starter — this
docstring's framing is local, and the formatter-hook hint was added to the
re-read message in the same sync)
"""
import json
import os
import sys
import tempfile

BIG_FILE_LINES = 400

# The guarded-extension list is shared with context-guard.py (guarded_ext.py):
# text formats where a whole-file pull is the thing being rationed. Markdown is
# guarded there (a cat of a doc is still a whole-file read) but exempt from the
# size rule here: reading a whole ADR or CONTEXT.md is the intended use.
# Anything unlisted (images, PDFs, notebooks, extensionless files) passes — a
# line count is meaningless there.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guarded_ext import GUARDED_EXT, SIZE_RULE_EXEMPT  # noqa: E402

SIZE_GUARDED_EXT = GUARDED_EXT - SIZE_RULE_EXEMPT


def in_project(path):
    """True when *path* sits inside the project root (job tmp dirs are outside)."""
    root = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        root = os.path.normcase(os.path.realpath(root))
        target = os.path.normcase(os.path.realpath(path))
    except OSError:
        return False
    return target == root or target.startswith(root + os.sep)


def line_count(path):
    """Lines in *path*, or None when it cannot be counted (missing, binary, …)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

session = data.get("session_id") or ""
state_dir = os.path.join(tempfile.gettempdir(), "claude-read-guard")
state_file = os.path.join(state_dir, session) if session else ""
event = data.get("hook_event_name", "")
tool = data.get("tool_name", "")
tool_input = data.get("tool_input", {}) or {}

if event == "PostToolUse" and tool in ("Edit", "Write", "MultiEdit"):
    path = tool_input.get("file_path", "")
    if path and state_file:
        os.makedirs(state_dir, exist_ok=True)
        with open(state_file, "a") as f:
            f.write(path + "\n")
    sys.exit(0)

if event == "PreToolUse" and tool == "Read":
    path = tool_input.get("file_path", "")

    # The re-read rule's escape is unchanged: any offset/limit means the model is
    # after a section, not the file.
    if "offset" not in tool_input and "limit" not in tool_input:
        edited = set()
        if state_file:
            try:
                with open(state_file) as f:
                    edited = set(f.read().splitlines())
            except OSError:
                edited = set()
        if path in edited:
            sys.stderr.write(
                "Blocked (context discipline): this session already edited that file — its content is in your "
                "context, and Edit/Write fail loudly on a miss, so a whole-file re-read buys nothing.\n"
                "If you need a specific section, grep -n for it and Read with offset/limit.\n"
                "(If Edit just failed with 'modified since read' — a formatter hook rewrote it — re-read the "
                "changed region with offset/limit.)\n"
            )
            sys.exit(2)

    # The size rule wants a *bounded* read, so it takes `limit` alone as the
    # escape: a bare `offset` still pulls Read's 2000-line default, which is the
    # whole of any file this rule covers.
    if (
        "limit" not in tool_input
        and os.path.splitext(path)[1].lower() in SIZE_GUARDED_EXT
        and in_project(path)
    ):
        lines = line_count(path)
        if lines is not None and lines > BIG_FILE_LINES:
            sys.stderr.write(
                f"Blocked (context discipline): {os.path.basename(path)} is {lines} lines — "
                f"over the {BIG_FILE_LINES}-line whole-file limit.\n"
                f"Grep for the symbol you need first, then Read with offset/limit around the hit.\n"
                f"If you truly need the whole file, say so with an explicit range: offset 1, limit {lines}.\n"
            )
            sys.exit(2)

sys.exit(0)
