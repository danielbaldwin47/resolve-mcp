#!/usr/bin/env python3
"""PreToolUse hook enforcing CLAUDE.md's context-discipline rules on the shell
tools (Bash and PowerShell — settings.json matches both).

Blocks (exit 2, message to the model):
  1. Noisy runs (NOISY_TOOLS below) that do not land in a file: bare, piped to
     tail/head/cat/Select-Object, or piped anywhere else — the scratch-log+grep
     rule. `--version`, `--help` and `--collect-only` are not runs.
  2. `gh issue view` / `gh pr view` / `gh pr diff` that do not land in a file.
     A `--json … -q` field filter passes unless it pulls the body or the
     comment thread; `gh pr diff --name-only|--stat` passes.
  3. A whole-file dump of a guarded file (guarded_ext.py) by any reader —
     `cat`, `more`, `less`, `type`, `Get-Content`/`gc`, `sed -n` with no range
     or `1,$p`, `head`/`tail` with no count, `head -c` — that is neither piped
     onward nor redirected. Ranged reads (`sed -n 10,40p`, `head -50`,
     `Get-Content -TotalCount 50`) pass. Backslash and drive-letter paths count.
  4. A `for` loop that cats guarded files.

Heredoc / here-string bodies and `--body`/`--message`/`--title` arguments are
data, not commands, and are blanked before any rule looks (the two false
positives measured in the 2026-08-15 audit were `gh pr create --body …` and
`gh issue comment --body …`).

Measured motivation (2026-08 transcript audits): 233 piped harness runs with
zero scratch-file adoption; one 237k-token session that pulled 162KB via `cat`
loops; ~800 unguarded PowerShell calls; 44% of `gh … view` calls unredirected.

starter-version: 2026-08-10 (claude-principles; locally tuned: LAUNCHERS,
PowerShell, dump readers, block messages use this repo's scratch-log names)
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from guarded_ext import GUARDED_EXT_RE  # noqa: E402

SHELL_TOOLS = {"Bash", "PowerShell"}
# The commands whose full output belongs in a scratch log, never in context.
NOISY_TOOLS = r"pytest|mypy|ruff"
# Launcher prefixes those commands may hide behind in this repo.
LAUNCHERS = r"(?:uv\s+run\s+(?:-\S+\s+)*|python3?\s+-m\s+)"

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

if data.get("tool_name") not in SHELL_TOOLS:
    sys.exit(0)

cmd = data.get("tool_input", {}).get("command", "") or ""


def block(message: str) -> None:
    sys.stderr.write(message.rstrip("\n") + "\n")
    sys.exit(2)


# ---------------------------------------------------------------- blanking
# Heredoc bodies are data, not commands; `<<-` terminators may be indented.
HEREDOC = r"<<-?\s*(['\"]?)(\w+)\1[\s\S]*?\n[ \t]*\2\b"
# PowerShell here-strings: @' … '@ and @" … "@ (closing token at line start).
HERESTRING = r"@(['\"])[\s\S]*?\n\1@"
# Prose arguments: what follows --body/--message/--title (and their short
# forms) in quotes is text about commands, never a command.
PROSE_ARG = r"(?i)(?:--body|--message|--title|-[bmt])(?:=|\s+)(?:'[^']*'|\"[^\"]*\")"

blanked = re.sub(HEREDOC, "<<HEREDOC", cmd)
blanked = re.sub(HERESTRING, "@HERESTRING@", blanked)
blanked = re.sub(PROSE_ARG, "--PROSE ''", blanked)
# The noisy/gh rules blank the remaining quoted strings: a commit message
# merely mentioning pytest is prose. The dump rules use a quote-STRIPPED copy
# (quotes removed, content kept): `cat "notes.md"` is still a cat, and
# command-position anchoring is what protects those rules from prose.
scan = re.sub(r"'[^']*'", "''", blanked)
scan = re.sub(r'"[^"]*"', '""', scan)
scan_cat = re.sub(r"[\"']", "", blanked)

# Command position: line start, a separator (`;`, `&`, `|`, `(`, `)`, `{`,
# backtick, newline), a $( substitution, or a then/do/else keyword — with
# env-var assignments (`CI=1 pytest …`) and any chain of launchers
# (`uv run python -m pytest`) allowed before the tool name.
CMD_POS = r"(?:^\s*|[;&|(){`\n]\s*|\$\(\s*|\b(?:then|do|else)\s+)"
# One shell-argument: word chars plus the path characters of both shells —
# `\` and `:` so `C:\repo\a.py` and `.\src\a.py` are seen as one arg.
ARG = r"[\w$./*:\\~{}%+=@,-]+"
GUARDED = r"\.(?:" + GUARDED_EXT_RE + r")\b"


def statement(text: str, start: int) -> str:
    """The rest of the statement from *start*: up to `;`, `&&`, or a newline —
    pipes stay in, so a pipeline's later stages (its sink) are visible."""
    return re.split(r"&&|[;\n]", text[start:], 1)[0]


def lands_in_file(seg: str) -> bool:
    """True when the statement's stdout reaches a file: a `>`/`>>`/`1>`/`&>`/`*>`
    redirect (a `2>` alone is stderr; `>&` is an fd dup), or a PowerShell sink."""
    if re.search(r"(?<![2-9])>(?!&)\s*[^\s&|;]", seg):
        return True
    return re.search(r"\|\s*(?:Out-File|Set-Content|Add-Content)\b", seg, re.I) is not None


PAGERS = r"tail|head|less|more|cat|tee|Select-Object|select|Out-Host|Tee-Object|Out-String"

# ---------------------------------------------------------------- 1. noisy runs
NOISY = (
    CMD_POS
    + r"(?:\w+=\S+\s+)*"
    + r"(?:" + LAUNCHERS + r")*"
    + r"(?P<tool>" + NOISY_TOOLS + r")\b(?![.-])"
)
for m in re.finditer(NOISY, scan):
    seg = statement(scan, m.end())
    tool = m.group("tool")
    if re.search(r"(?:^|\s)(?:--version|--help|-h|--co|--collect-only)\b", seg):
        continue
    if re.search(r"\|\s*(?:" + PAGERS + r")\b", seg, re.I):
        block(
            "Blocked (context discipline): noisy runs never pipe to tail/head - a tail caps one run, runs repeat.\n"
            f"Use one bare command: uv run {tool} > {tool}.scratch.log 2>&1. Then Grep the log for the decisive line (FAILED|passed|error).\n"
            "On failure, Grep the log for the failing case by name; never cat the log."
        )
    if not lands_in_file(seg):
        block(
            f"Blocked (context discipline): a bare {tool} run puts its whole output in context.\n"
            f"Use one bare command: uv run {tool} > {tool}.scratch.log 2>&1 (a redirect the worktree guard accepts - "
            "no cd, no ;-chain). Then Grep the log for the decisive line (FAILED|passed|error)."
        )

# ---------------------------------------------------------------- 2. gh views
# Issue bodies, comment threads and PR diffs are the largest tool results
# measured (2026-08-05, 2026-08-15). They land in a file, never in context; a
# `--json … -q` field filter is fine unless it is the body or the thread.
GH_VIEW = r"\bgh\s+(?P<sub>issue\s+view|pr\s+view|pr\s+diff)\b"
for m in re.finditer(GH_VIEW, scan):
    seg = statement(scan, m.end())
    args = seg.split("|", 1)[0]
    if lands_in_file(seg):
        continue
    if "diff" in m.group("sub") and re.search(r"--(?:name-only|stat)\b", args):
        continue
    if (
        "diff" not in m.group("sub")
        and re.search(r"--json\b", args)
        and re.search(r"(?:^|\s)(?:-q|--jq)\b", args)
        and not re.search(r"\b(?:body|comments)\b", args)
    ):
        continue
    if "--comments" in args:
        block(
            "Blocked (context discipline): a comment pull lands in a file, never straight in context.\n"
            "Use one bare command: gh issue view <n> --comments > comments.scratch.log. Then Grep the log - or --json comments with a jq filter to a file."
        )
    block(
        "Blocked (context discipline): gh issue/pr view and pr diff land in a file, never straight in context.\n"
        "Use one bare command: gh issue view <n> --json body -q .body > issue.scratch.log "
        "(gh pr diff <n> > pr.scratch.log). Then Grep the log for the section you need; "
        "a --json field filter (-q .title, .state) is fine."
    )

# ---------------------------------------------------------------- 3. whole-file dumps
DUMP_MSG = (
    "Blocked (context discipline): a whole-file dump of {name} puts the whole file in context - "
    "the rules govern content entering context, not which tool fetched it.\n"
    "Grep for the lines you need first, then Read with offset/limit around the hit - or a ranged read "
    "(sed -n 10,40p, head -50, Get-Content -TotalCount 50); a dump piped to grep or redirected to a file passes."
)


def guarded_names(args: str) -> list:
    return [a for a in re.findall(ARG, args) if re.search(GUARDED, a)]


def dump_block(args: str, seg: str) -> None:
    """Block when *args* name a guarded file and the statement neither pipes
    onward nor lands in a file."""
    names = guarded_names(args)
    if not names or lands_in_file(seg):
        return
    # A pipe to a filter (grep, wc, jq, Select-String…) bounds the read; a pipe
    # to a pager or a re-emitter (`| cat -n`, `| Out-String`) does not.
    stages = seg.split("|")[1:]
    filtered = bool(stages) and not all(
        re.match(r"\s*(?:cat|more|less|tee|Tee-Object|Out-Host|Out-String)\b", st, re.I)
        for st in stages
    )
    if not filtered:
        block(DUMP_MSG.format(name=names[0]))


# for-loop cat sweep: `for f in src/*.py; do cat $f; done`
if re.search(r"\bfor\b[^;]*" + GUARDED + r".*\bcat\b", scan_cat):
    block(
        "Blocked (context discipline): a cat loop over source files is a mass whole-file Read.\n"
        "Use the Read tool per file you will edit, or grep for the lines you actually need."
    )

# cat / more / less / type / Get-Content / gc: every plain arg is a file. Options
# and PowerShell parameters are stepped over; `-TotalCount`/`-Tail`/`-Head`/
# `-First`/`-Last` bound the read and clear it. `cat < x.py` counts too.
READERS = r"(?:cat|more|less|type|Get-Content|gc)"
for m in re.finditer(CMD_POS + READERS + r"\b(?P<rest>[^|;&\n]*)", scan_cat, re.I):
    rest = m.group("rest")
    if re.search(r"-(?:TotalCount|Tail|Head|First|Last)\b", rest, re.I):
        continue
    dump_block(rest, statement(scan_cat, m.start("rest")))

# sed: only an unbounded script (`p`, `1,$p`, or empty) is a dump; any range,
# address or substitution is a targeted read or an edit. Quotes intact here so
# the script token is one group.
SED = (
    CMD_POS
    + r"sed\b(?P<flags>(?:\s+-[a-zA-Z]+)*)\s+"
    + r"(?:'(?P<sq>[^']*)'|\"(?P<dq>[^\"]*)\"|(?P<bare>\S+))"
    + r"(?P<args>(?:\s+" + ARG + r")+)"
)
for m in re.finditer(SED, blanked):
    script = next(s for s in (m.group("sq"), m.group("dq"), m.group("bare")) if s is not None)
    if script.strip() in ("", "p", "1,$p"):
        dump_block(m.group("args"), statement(blanked, m.end("args")))

# head / tail: a count bounds the read; `-c` (bytes) is a dump by another name.
HEADTAIL = (
    CMD_POS
    + r"(?:head|tail)\b(?P<flags>(?:\s+(?:-n\s*\d+|-\d+|--lines(?:=|\s+)\d+|-c\s*\d*|"
    + r"--bytes(?:=|\s+)\S+|-[a-zA-Z]+))*)"
    + r"(?P<args>(?:\s+(?:<\s*)?" + ARG + r")+)"
)
for m in re.finditer(HEADTAIL, scan_cat):
    flags = m.group("flags")
    bounded = re.search(r"-n\s*\d|-\d|--lines", flags)
    bytes_dump = re.search(r"-c\b|--bytes", flags)
    if bytes_dump or not bounded:
        dump_block(m.group("args"), statement(scan_cat, m.end("args")))

sys.exit(0)
