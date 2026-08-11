#!/usr/bin/env python3
"""PreToolUse hook enforcing CLAUDE.md's context-discipline rules on Bash.

Blocks (exit 2, message to the model):
  1. Noisy runs (NOISY_TOOLS below) piped to tail/head/cat — the
     scratch-log+grep rule.
  2. `gh` comment pulls that don't land in a file — the full thread is the
     largest tool-result class measured.
  3. `cat` of source/config files straight into context — that is a whole-file
     Read with a worse interface; use the Read tool or pipe to grep.

Measured motivation (2026-08 transcript audit): 233 piped harness runs with
zero scratch-file adoption, and one 237k-token session that pulled 162KB via
`cat` loops without a single Read call.

starter-version: 2026-08-10 (claude-principles; locally tuned: LAUNCHERS,
block messages use this repo's scratch-log names)
"""
import json
import re
import sys

# The commands whose full output belongs in a scratch log, never in context.
NOISY_TOOLS = r"pytest|mypy|ruff"
# Launcher prefixes those commands may hide behind in this repo.
LAUNCHERS = r"(?:uv\s+run\s+(?:-\S+\s+)*|python3?\s+-m\s+)"

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if data.get("tool_name") != "Bash":
    sys.exit(0)

cmd = data.get("tool_input", {}).get("command", "") or ""

# Heredoc bodies are data, not commands; `<<-` terminators may be indented
# (session note context-guard-blocks-some-heredocs, 2026-08 — now fixed).
HEREDOC = r"<<-?\s*(['\"]?)(\w+)\1[\s\S]*?\n[ \t]*\2\b"
blanked = re.sub(HEREDOC, "<<HEREDOC", cmd)
# The noisy/gh rules also blank quoted strings: a commit message merely
# mentioning pytest is prose. The cat rules instead use a quote-STRIPPED
# copy (quotes removed, content kept): `cat "notes.md"` is still a cat, and
# command-position anchoring is what protects those rules from prose.
scan = re.sub(r"'[^']*'", "''", blanked)
scan = re.sub(r'"[^"]*"', '""', scan)
scan_cat = re.sub(r"[\"']", "", blanked)

# Command position: line start, a separator (`;`, `&`, `|`, `(`, `)`, `{`,
# backtick, newline), a $( substitution, or a then/do/else keyword — with
# env-var assignments (`CI=1 pytest …`) and any chain of launchers
# (`uv run python -m pytest`) allowed before the tool name.
CMD_POS = r"(?:^\s*|[;&|(){`\n]\s*|\$\(\s*|\b(?:then|do|else)\s+)"
NOISY = (
    CMD_POS
    + r"(?:\w+=\S+\s+)*"
    + r"(?:" + LAUNCHERS + r")*"
    + r"(?:" + NOISY_TOOLS + r")\b"
)
if re.search(NOISY + r"[^|]*\|\s*(tail|head|less|more|cat)\b", scan):
    sys.stderr.write(
        "Blocked (context discipline): noisy runs never pipe to tail/head — a tail caps one run, runs repeat.\n"
        "Use one bare command: <cmd> > pytest.scratch.log 2>&1. Then Grep the log for the decisive line (FAILED|passed|error).\n"
        "On failure, Grep the log for the failing case by name; never cat the log.\n"
    )
    sys.exit(2)

# gh comment pulls: the full thread is 5-7KB per call and repeats (measured
# 2026-08-05). Piping to tail caps nothing that matters, and dropping the pipe
# is worse — so the rule is: comments land in a file (or a --json filter),
# never straight in context. Checked per pull, within that pull's own command
# segment; a digit before `>` is an fd redirect (2>), not a landing.
for m in re.finditer(r"\bgh\s+(issue|pr)\s+view\b[^|;&]*--comments", scan):
    seg = re.split(r"[|;&]", scan[m.end():], 1)[0]
    if not re.search(r"(?<!\d)>\s*\S", seg):
        sys.stderr.write(
            "Blocked (context discipline): a comment pull lands in a file, never straight in context.\n"
            "Use one bare command: gh issue view <n> --comments > comments.scratch.log. Then Grep the log — or --json comments with a jq filter.\n"
        )
        sys.exit(2)

SRC_EXT = r"\.(md|sh|py|js|ts|json|yml|yaml|txt|log|conf|ini)\b"

# for-loop cat sweep: `for f in src/*.py; do cat $f; done`
if re.search(r"\bfor\b[^;]*" + SRC_EXT + r".*\bcat\b", scan_cat):
    sys.stderr.write(
        "Blocked (context discipline): a cat loop over source files is a mass whole-file Read.\n"
        "Use the Read tool per file you will edit, or grep for the lines you actually need.\n"
    )
    sys.exit(2)

# bare cat of a source file, not piped onward and not a redirect/heredoc —
# anchored at command position so prose mentioning "cat" never matches
for m in re.finditer(CMD_POS + r"cat\b(?:\s+-[\w]+)*((?:\s+[\w$./*-]+)+)", scan_cat):
    args = m.group(1)
    rest = scan_cat[m.end():]
    if re.search(SRC_EXT, args) and not re.match(r"\s*[|>]", rest):
        sys.stderr.write(
            "Blocked (context discipline): don't cat files into context — the rules govern content entering "
            "context, not which tool fetched it.\n"
            "Use the Read tool (ranged: grep -n first, then offset/limit), or pipe to grep for the decisive lines.\n"
        )
        sys.exit(2)

sys.exit(0)
