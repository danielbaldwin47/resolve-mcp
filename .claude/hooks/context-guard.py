#!/usr/bin/env python3
"""PreToolUse hook enforcing CLAUDE.md's context-discipline rules on the shell
tools (Bash and PowerShell — settings.json matches both).

Blocks (exit 2, message to the model):
  1. Noisy runs (NOISY_TOOLS below) whose output does not land in a file —
     bare, or piped to tail/head/grep. The scratch-log+grep rule.
  2. `gh issue view` / `gh pr view` / `gh pr diff` that do not land in a
     file — issue bodies, comment threads and PR diffs are the largest
     tool-result class measured.
  3. Whole-file dumps of a guarded file straight into context, whichever
     reader fetches them: `cat`, `sed -n p` / `1,$p`, `head`/`tail` with no
     count, `head -c`, `Get-Content` without `-TotalCount`/`-Tail`, `type`,
     and a `for … cat` sweep. Ranged reads (`sed -n 10,40p`, `head -50`,
     `Get-Content -TotalCount 50`) pass, as does anything piped onward or
     redirected. Backslash and drive-letter paths count as paths.

`gh … --body …` (and `--title`, `-m`) arguments are prose and are never
inspected. Heredoc bodies and here-strings are data, not commands.

Measured motivation (2026-08 transcript audits): 233 piped harness runs with
zero scratch-file adoption; one 237k-token session that pulled 162KB via
`cat` loops without a single Read call; ~800 unguarded PowerShell calls; 44%
of `gh … view` calls unredirected; `sed -n`, `head`, `Get-Content` and
Windows paths all walking round the cat rule (#248).

The guarded-extension list lives in guard_ext.py, shared with read-guard.py.

starter-version: 2026-08-10 (claude-principles; locally tuned: LAUNCHERS,
PowerShell, the wider reader set, block messages use this repo's scratch-log
names)
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guard_ext import GUARDED_EXT  # noqa: E402

SHELL_TOOLS = ("Bash", "PowerShell")

# The commands whose full output belongs in a scratch log, never in context.
NOISY_TOOLS = r"pytest|mypy|ruff"
# Launcher prefixes those commands may hide behind in this repo.
LAUNCHERS = r"(?:uv\s+run\s+(?:-\S+\s+)*|python3?\s+-m\s+)"

# Command position: line start, a separator (`;`, `&`, `|`, `(`, `)`, `{`,
# backtick, newline), a $( substitution, or a then/do/else keyword — with
# env-var assignments (`CI=1 pytest …`) and any chain of launchers
# (`uv run python -m pytest`) allowed before the tool name.
CMD_POS = r"(?:^\s*|[;&|(){`\n]\s*|\$\(\s*|\b(?:then|do|else)\s+)"
NOISY = (
    CMD_POS
    + r"(?:\w+=\S+\s+)*"
    + r"(?P<tool>(?:" + LAUNCHERS + r")*"
    + r"(?:" + NOISY_TOOLS + r")\b)"
)

# A path token: POSIX or Windows (`C:\x\y.py`, `.\src\f.py`, `~/f.py`,
# `$env:TMP\f.py`, `${VAR}/f.py`). Quotes were stripped before matching.
PATH_TOKEN = r"[\w$./*:\\~{}+@%,=-]+"
# One of the guarded extensions, as a regex on a (quote-stripped) arg string.
GUARDED_EXT_RE = (
    r"\.(?:" + "|".join(sorted(e[1:] for e in GUARDED_EXT)) + r")(?![\w])"
)

# Heredoc bodies are data, not commands; `<<-` terminators may be indented
# (session note context-guard-blocks-some-heredocs, 2026-08 — now fixed).
HEREDOC = r"<<-?\s*(['\"]?)(\w+)\1[\s\S]*?\n[ \t]*\2\b"
# PowerShell here-strings: @'…'@ / @"…"@, terminator at line start.
HERESTRING = r"@(['\"])[\s\S]*?\n\1@"
# One alternation so an apostrophe inside a double-quoted string is consumed
# with that string, not paired with the next apostrophe on the line — that
# pairing is what turned `--body "…don't … pytest | tail …"` into a false
# positive (#248).
QUOTED = r"'[^']*'|\"(?:[^\"\\]|\\.)*\""
# Prose arguments: never inspected. The value may be a quoted string, a
# $(…) substitution (heredoc already blanked) or a bare word.
PROSE_ARG = (
    r"(?<!\S)(--body|--title|--message|-m|-b|-t)(=|\s+)"
    r"(?:" + QUOTED + r"|\$\([^)]*\))"
)
# Whole-file readers, anchored at command position; args are path-ish tokens.
READER = re.compile(
    CMD_POS
    + r"(?P<cmd>cat|sed|head|tail|type|Get-Content|gc)\b(?P<args>(?:\s+"
    + PATH_TOKEN
    + r")*)"
)

WHOLE_FILE_MSG = (
    "Blocked (context discipline): don't dump whole files into context — the rules govern "
    "content entering context, not which command fetched it (cat, sed -n p, head/tail with no "
    "count, Get-Content, type all cost the same).\n"
    "Use the Read tool (ranged: grep -n first, then offset/limit), a ranged read "
    "(sed -n 10,40p / head -50 / Get-Content -TotalCount 50), or pipe to grep for the "
    "decisive lines.\n"
)
NOISY_MSG = (
    "Blocked (context discipline): a noisy run (pytest/mypy/ruff) lands in a scratch log, never "
    "in context — bare or piped to tail/head/grep, a run's output caps nothing that matters "
    "and runs repeat.\n"
    "Use one bare command, from the session cwd, no chain:\n"
    "    uv run pytest -m 'not live' > pytest.scratch.log 2>&1\n"
    "    uv run mypy src tests > mypy.scratch.log 2>&1\n"
    "    uv run ruff check src tests > ruff.scratch.log 2>&1\n"
    "Then Grep the log for the decisive line (FAILED|passed|error); never cat the log.\n"
)
GH_MSG = (
    "Blocked (context discipline): gh views and diffs land in a file, never straight in context — "
    "issue bodies, comment threads and PR diffs are the largest tool results measured.\n"
    "Use one bare command, no chain:\n"
    "    gh issue view <n> --json body,comments > issue.scratch.log\n"
    "    gh pr view <n> --json body,comments > pr.scratch.log\n"
    "    gh pr diff <n> > pr.scratch.log\n"
    "Then Grep the log for the section you need.\n"
)
CAT_LOOP_MSG = (
    "Blocked (context discipline): a cat loop over source files is a mass whole-file Read.\n"
    "Use the Read tool per file you will edit, or grep for the lines you actually need.\n"
)


def prepare(cmd):
    """Return (scan, scan_cat): the command with data blanked for scanning.

    *scan* has heredocs, here-strings, prose args and quoted strings blanked —
    for the noisy/gh rules, where a commit message merely mentioning pytest is
    prose. *scan_cat* keeps quoted content but drops the quote characters —
    `cat "notes.md"` is still a cat, and command-position anchoring is what
    protects the reader rules from prose.
    """
    blanked = re.sub(HEREDOC, "<<HEREDOC", cmd)
    blanked = re.sub(HERESTRING, "''", blanked)
    blanked = re.sub(PROSE_ARG, r"\1 ''", blanked)
    scan = re.sub(QUOTED, lambda m: "''" if m.group(0)[0] == "'" else '""', blanked)
    scan_cat = re.sub(QUOTED, lambda m: m.group(0)[1:-1], blanked)
    return scan, scan_cat


# A command segment ends at `;`, newline, `&&`, `||`, or a bare `&` — but not
# at the `&` of `2>&1` / `&>`. Pipes are kept: landing is judged per pipeline.
SEG_END = re.compile(r"\|\||&&|[;\n]|(?<![>\d])&(?!>)")


def segment(text, start):
    """The command segment beginning at *start* (up to the next separator)."""
    m = SEG_END.search(text, start)
    return text[start : m.start()] if m else text[start:]


def lands_in_file(seg):
    """True when the segment's output reaches a file, not the context.

    A file redirect in the first pipe stage (a digit before `>` is an fd
    redirect, `2>`, not a landing), or a PowerShell file cmdlet downstream.
    """
    first = seg.split("|", 1)[0]
    if re.search(r"(?<!\d)>\s*\S", first):
        return True
    return re.search(r"\|\s*(?:Out-File|Set-Content|Add-Content)\b", seg) is not None


def piped_or_redirected(seg):
    """True when a reader's output is piped onward or lands in a file."""
    return "|" in seg or lands_in_file(seg)


def check(tool_name, cmd):
    """The block message for *cmd*, or None when it passes."""
    if tool_name not in SHELL_TOOLS:
        return None
    scan, scan_cat = prepare(cmd or "")

    # 1. Noisy runs land in a file.
    for m in re.finditer(NOISY, scan):
        if not lands_in_file(segment(scan, m.start("tool"))):
            return NOISY_MSG

    # 2. gh views/diffs land in a file.
    for m in re.finditer(r"\bgh\s+(?:issue\s+view|pr\s+view|pr\s+diff)\b", scan):
        if not lands_in_file(segment(scan, m.start())):
            return GH_MSG

    # 3a. for-loop cat sweep: `for f in src/*.py; do cat $f; done`
    if re.search(r"\bfor\b[^;]*" + GUARDED_EXT_RE + r".*\bcat\b", scan_cat):
        return CAT_LOOP_MSG

    # 3b. Whole-file readers.
    for m in READER.finditer(scan_cat):
        name = m.group("cmd")
        args = m.group("args")
        if not re.search(GUARDED_EXT_RE, args):
            continue
        if piped_or_redirected(segment(scan_cat, m.start("cmd"))):
            continue
        if whole_file(name, args):
            return WHOLE_FILE_MSG

    return None


def whole_file(name, args):
    """True when reader *name* with *args* (guarded path present) dumps it all."""
    tokens = args.split()
    if name in ("cat", "type", "Get-Content", "gc"):
        if name == "cat":
            return True
        # Get-Content and type (its alias) — bounded by -TotalCount / -Tail
        # (aliases -Head/-First/-Last).
        return not any(
            re.match(r"-(?:TotalCount|Tail|Head|First|Last)\b", t, re.I) for t in tokens
        )
    if name in ("head", "tail"):
        if any(re.match(r"-c\b|--bytes\b", t) for t in tokens):
            return True
        return not any(re.match(r"-n(?:\d|$)|-\d+$|--lines(?:=|$)", t) for t in tokens)
    if name == "sed":
        # The script is the first non-flag token (quotes already stripped);
        # `p`, `1,$p` and no script at all (`sed '' file`) are the whole file.
        flags = [t for t in tokens if t.startswith("-")]
        if any(t in ("-e", "--expression") for t in flags):
            idx = next(i for i, t in enumerate(tokens) if t in ("-e", "--expression"))
            script = tokens[idx + 1] if idx + 1 < len(tokens) else ""
        else:
            rest = [t for t in tokens if not t.startswith("-")]
            script = rest[0] if len(rest) > 1 else ""
        return script.replace("\\", "") in ("", "p", "1,$p")
    return False


if __name__ == "__main__":
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    msg = check(
        data.get("tool_name"), (data.get("tool_input", {}) or {}).get("command", "")
    )
    if msg:
        sys.stderr.write(msg)
        sys.exit(2)
    sys.exit(0)
