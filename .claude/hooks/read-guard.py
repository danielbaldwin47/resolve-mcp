#!/usr/bin/env python3
"""Read-after-edit guard, both halves in one script (dispatched on hook_event_name).

PostToolUse on Edit/Write/MultiEdit: record the edited path, keyed by session.
PreToolUse on Read: block (exit 2) a whole-file Read of a path this session
already edited — the content is in context and Edit/Write fail loudly on a
miss, so the re-read buys nothing. A ranged Read (offset/limit present) is
allowed: needing a different section of a big file is legitimate.

Measured motivation: read-after-edit ran ~46% of sessions pre-rules and ~39%
after prose alone (2026-08-05 transcript audit) — the one axis prose never moved.
"""
import json
import os
import sys
import tempfile

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

session = data.get("session_id") or ""
if not session:
    sys.exit(0)

state_dir = os.path.join(tempfile.gettempdir(), "claude-read-guard")
state_file = os.path.join(state_dir, session)
event = data.get("hook_event_name", "")
tool = data.get("tool_name", "")
tool_input = data.get("tool_input", {}) or {}

if event == "PostToolUse" and tool in ("Edit", "Write", "MultiEdit"):
    path = tool_input.get("file_path", "")
    if path:
        os.makedirs(state_dir, exist_ok=True)
        with open(state_file, "a") as f:
            f.write(path + "\n")
    sys.exit(0)

if event == "PreToolUse" and tool == "Read":
    if "offset" in tool_input or "limit" in tool_input:
        sys.exit(0)
    path = tool_input.get("file_path", "")
    try:
        with open(state_file) as f:
            edited = set(f.read().splitlines())
    except OSError:
        sys.exit(0)
    if path in edited:
        sys.stderr.write(
            "Blocked (context discipline): this session already edited that file — its content is in your "
            "context, and Edit/Write fail loudly on a miss, so a whole-file re-read buys nothing.\n"
            "If you need a specific section, grep -n for it and Read with offset/limit.\n"
        )
        sys.exit(2)

sys.exit(0)
