#!/usr/bin/env python3
"""Read guards, all halves in one script (dispatched on hook_event_name).

PostToolUse on Edit/Write/MultiEdit: record the edited path, keyed by session.
PreToolUse on Read, blocking (exit 2) on either rule:
  1. Re-read: a whole-file Read of a path this session already edited — the
     content is in context and Edit/Write fail loudly on a miss, so the re-read
     buys nothing.
  2. Big first read: a whole-file Read of a repo file over BIG_FILE_LINES —
     CLAUDE.md's "ranged (grep first) on big files", which prose alone did not
     hold under load (one session full-read a 656-line module three times).
A ranged Read (offset/limit present) is allowed by both: needing a different
section of a big file is legitimate, and a deliberate `offset: 1, limit: <n>`
remains the escape hatch, so this is a speed bump rather than a wall.

Measured motivation: read-after-edit ran ~46% of sessions pre-rules and ~39%
after prose alone (2026-08-05 transcript audit) — the one axis prose never moved.
"""
import json
import os
import sys
import tempfile

BIG_FILE_LINES = 400

# Text formats where a whole-file pull is the thing being rationed. Markdown is
# deliberately absent: reading a whole ADR or CONTEXT.md is the intended use.
# Anything unlisted (images, PDFs, notebooks, extensionless files) passes — a
# line count is meaningless there.
GUARDED_EXT = {
    ".py",
    ".pyi",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".sh",
    ".ini",
    ".cfg",
    ".conf",
    ".txt",
    ".log",
}


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
    if "offset" in tool_input or "limit" in tool_input:
        sys.exit(0)
    path = tool_input.get("file_path", "")
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
        )
        sys.exit(2)

    if os.path.splitext(path)[1].lower() in GUARDED_EXT and in_project(path):
        lines = line_count(path)
        if lines is not None and lines > BIG_FILE_LINES:
            sys.stderr.write(
                "Blocked (context discipline): {} is {} lines — over the {}-line whole-file limit.\n".format(
                    os.path.basename(path), lines, BIG_FILE_LINES
                )
                + "Grep for the symbol you need first, then Read with offset/limit around the hit.\n"
                "If you truly need the whole file, say so with an explicit range: offset 1, limit {}.\n".format(
                    lines
                )
            )
            sys.exit(2)

sys.exit(0)
