#!/usr/bin/env python3
"""PreToolUse hook enforcing CLAUDE.md's context-discipline rules on Bash.

Blocks (exit 2, message to the model):
  1. Noisy runs (pytest, mypy, ruff) piped to tail/head/cat — the mktemp+grep
     rule.
  2. `cat` of source/config files straight into context — that is a whole-file
     Read with a worse interface; use the Read tool or pipe to grep.

Measured motivation (2026-08 transcript audit): 233 piped harness runs with
zero mktemp adoption, and one 237k-token session that pulled 162KB via `cat`
loops without a single Read call.
"""
import json
import re
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if data.get("tool_name") != "Bash":
    sys.exit(0)

cmd = data.get("tool_input", {}).get("command", "") or ""

# Anchored at command position (line start, `;`, `&`, `|`, `(`, or a backtick/
# $( substitution), optionally behind a `uv run` / `python -m` launcher — a bare
# word match false-positived on commit messages and PR bodies that merely
# mention pytest/mypy/ruff in quoted text.
NOISY = (
    r"(?:^|[;&|(`\n]\s*|\$\(\s*)"
    r"(?:uv\s+run\s+(?:-\S+\s+)*|python3?\s+-m\s+)?"
    r"(?:pytest|mypy|ruff)\b"
)
if re.search(NOISY + r"[^|]*\|\s*(tail|head|less|more|cat)\b", cmd):
    sys.stderr.write(
        "Blocked (context discipline): noisy runs never pipe to tail/head — a tail caps one run, runs repeat.\n"
        'Use: log=$(mktemp); <cmd> >"$log" 2>&1; grep -E \'FAIL|Totals\' "$log"\n'
        "On failure, grep the log for the failing case by name; never cat the log.\n"
    )
    sys.exit(2)

# unbounded gh comment pulls: `gh issue view N --comments | tail -80` drags 5-7KB
# per call and repeats (measured 2026-08-05); the tail caps nothing that matters.
if re.search(r"\bgh (issue|pr) view\b[^|]*--comments[^|]*\|\s*(tail|head)\b", cmd):
    sys.stderr.write(
        "Blocked (context discipline): an unbounded comment pull piped to tail repeats its full cost every call.\n"
        "Use --json comments with a jq filter for the fields you need, or write to $(mktemp) and grep.\n"
    )
    sys.exit(2)

SRC_EXT = r"\.(md|sh|py|js|ts|json|yml|yaml|txt|log|conf|ini)\b"

# for-loop cat sweep: `for f in src/*.py; do cat $f; done`
if re.search(r"\bfor\b[^;]*" + SRC_EXT + r".*\bcat\b", cmd):
    sys.stderr.write(
        "Blocked (context discipline): a cat loop over source files is a mass whole-file Read.\n"
        "Use the Read tool per file you will edit, or grep for the lines you actually need.\n"
    )
    sys.exit(2)

# bare cat of a source file, not piped onward and not a redirect/heredoc
for m in re.finditer(r"\bcat\b(?:\s+-[\w]+)*((?:\s+[\w$./*\"'-]+)+)", cmd):
    args = m.group(1)
    rest = cmd[m.end():]
    if re.search(SRC_EXT, args) and not re.match(r"\s*[|>]", rest):
        sys.stderr.write(
            "Blocked (context discipline): don't cat files into context — the rules govern content entering "
            "context, not which tool fetched it.\n"
            "Use the Read tool (ranged: grep -n first, then offset/limit), or pipe to grep for the decisive lines.\n"
        )
        sys.exit(2)

sys.exit(0)
