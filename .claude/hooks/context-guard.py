#!/usr/bin/env python3
"""PreToolUse hook enforcing CLAUDE.md's context-discipline rules on the shell
tools (Bash and PowerShell — settings.json matches both).

Blocks (exit 2, message to the model):
  1. Noisy runs (NOISY_TOOLS below) that do not land in a file: bare, piped to
     tail/head/cat/Select-Object, or piped anywhere else — the scratch-log+grep
     rule. `--version`, `--help` and `--collect-only` are not runs.
  2. `gh issue view` / `gh pr view` / `gh pr diff` that do not land in a file.
     A `--json … -q`/`--template` field filter passes unless it pulls the body
     or the comment thread; `gh pr diff --name-only|--stat` passes.
  3. A whole-file dump of a guarded file (guarded_ext.py) by any reader —
     `cat`, `more`, `less`, `type`, `Get-Content`/`gc`, `sed -n` with no range
     or `1,$p`, `head`/`tail` with no count, `head -c` — that is neither piped
     to a filter nor redirected. Ranged reads (`sed -n 10,40p`, `head -50`,
     `Get-Content -TotalCount 50`) pass. Backslash and drive-letter paths count,
     and so do readers fed by `xargs`, `find -exec`, or a `Get-ChildItem` pipe.
  4. A `for`/`foreach` loop over guarded files whose body dumps them.

Heredoc / here-string bodies and quoted `--body`/`--message`/`--title`
arguments are data, not commands, and are blanked before any rule looks (the
two false positives measured in the 2026-08-15 audit were `gh pr create
--body …` and `gh issue comment --body …`).

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
# Launcher prefixes those commands may hide behind in this repo (`.exe` and
# `py` are the Windows spellings; `uv run --directory X` carries an option value).
LAUNCHERS = (
    r"(?:uv(?:\.exe)?\s+run\s+(?:--?[\w-]+(?:[= ]\S+)?\s+)*"
    r"|uvx(?:\.exe)?\s+"
    r"|py(?:thon3?)?(?:\.exe)?\s+-m\s+)"
)

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
# Prose arguments: what follows --body/--message/--title (and the -b/-m short
# forms) in quotes is text about commands, never a command.
PROSE_ARG = r"(?i)(?:--body|--message|--title|-[bm])(?:=|\s+)(?:'[^']*'|\"[^\"]*\")"

blanked = re.sub(HEREDOC, "<<HEREDOC", cmd)
blanked = re.sub(HERESTRING, "@HERESTRING@", blanked)
blanked = re.sub(PROSE_ARG, "--PROSE ''", blanked)
# The noisy rule blanks the remaining quoted strings: a commit message merely
# mentioning pytest is prose. The gh rule keeps quotes (a jq filter is what it
# reads). The dump rules use a quote-STRIPPED copy (quotes removed, content
# kept): `cat "notes.md"` is still a cat, and command-position anchoring is
# what protects those rules from prose.
scan = re.sub(r"'[^']*'", "''", blanked)
scan = re.sub(r'"[^"]*"', '""', scan)
scan_cat = re.sub(r"[\"']", "", blanked)

# Command position: line start, a separator (`;`, `&`, `|`, `(`, `)`, `{`,
# backtick, newline), a `$(` substitution, an `=` (`$c = Get-Content …`), or a
# then/do/else keyword — with env-var assignments (`CI=1 pytest …`) and any
# chain of launchers (`uv run python -m pytest`) allowed before the tool name.
CMD_POS = r"(?:^\s*|[;&|(){`\n=]\s*|\$\(\s*|\b(?:then|do|else)\s+)"
# One shell argument: word chars plus the path characters of both shells —
# `\` and `:` so `C:\repo\a.py` and `.\src\a.py` are seen as one arg.
ARG = r"[\w$./*:\\~{}%+=@,-]+"
# A guarded extension ends the argument: `foo.py.bak`, `foo.py~`, `x.py.orig`
# are not the file.
GUARDED = r"\.(?:" + GUARDED_EXT_RE + r")(?![\w.~-])"
STATEMENT_SEP = r"&&|\|\||[;\n]"


def statement(text: str, start: int) -> str:
    """The rest of the statement from *start*: up to `;`, `&&`, or a newline —
    pipes stay in, so a pipeline's later stages (its sink) are visible."""
    return re.split(STATEMENT_SEP, text[start:], 1)[0]


def lands_in_file(seg: str) -> bool:
    """True when the statement's stdout leaves the context: a `>`/`>>`/`1>`/`&>`/`*>`
    redirect (a `2>` alone is stderr; `>&` is an fd dup), or a PowerShell sink."""
    if re.search(r"(?<![2-9])>(?!&)\s*[^\s&|;]", seg):
        return True
    return re.search(r"\|\s*(?:Out-File|Set-Content|Add-Content|Out-Null)\b", seg, re.I) is not None


# Pipe targets that re-emit rather than filter: after one of these a dump still
# enters context whole.
REEMITTERS = r"less|more|cat|nl|tee|Tee-Object|Out-Host|Out-String"
# For a noisy run, a capping pipe (tail/head/Select-Object) is no better: it caps
# one run, and runs repeat.
PAGERS = REEMITTERS + r"|tail|head|Select-Object|select(?![\w-])"


def piped_to_filter(seg: str) -> bool:
    """True when the statement pipes onward to something that bounds or filters
    it (grep, head -50, Select-Object -First 30, wc …) rather than re-emits it."""
    stages = seg.split("|")[1:]
    return bool(stages) and not all(
        re.match(r"\s*(?:" + REEMITTERS + r")(?![\w-])", st, re.I) for st in stages
    )


# ---------------------------------------------------------------- 1. noisy runs
NOISY = (
    CMD_POS
    + r"(?:\w+=\S+\s+)*"
    + r"(?:" + LAUNCHERS + r")*"
    + r"(?P<tool>" + NOISY_TOOLS + r")(?:\.exe)?\b(?![.-])"
)
for m in re.finditer(NOISY, scan):
    seg = statement(scan, m.end())
    tool = m.group("tool")
    if re.search(r"(?:^|\s)(?:--version|--help|-h|--co|--collect-only)\b", seg):
        continue
    if re.search(r"\|\s*(?:" + PAGERS + r")(?![\w-])", seg, re.I):
        block(
            "Blocked (context discipline): noisy runs never pipe to tail/head - a tail caps one run, runs repeat.\n"
            f"Use one bare command: uv run {tool}{' check' if tool == 'ruff' else ''} > {tool}.scratch.log 2>&1. "
            "Then Grep the log for the decisive line (FAILED|passed|error).\n"
            "On failure, Grep the log for the failing case by name; never cat the log."
        )
    if lands_in_file(seg):
        continue
    block(
        f"Blocked (context discipline): a bare {tool} run puts its whole output in context.\n"
        f"Use one bare command: uv run {tool}{' check' if tool == 'ruff' else ''} > {tool}.scratch.log 2>&1 "
        "(a redirect the worktree guard accepts - no cd, no ;-chain). "
        "Then Grep the log for the decisive line (FAILED|passed|error)."
    )

# ---------------------------------------------------------------- 2. gh views
# Issue bodies, comment threads and PR diffs are the largest tool results
# measured (2026-08-05, 2026-08-15). They land in a file, never in context; a
# `--json … -q`/`--template` field filter is fine unless it is the body or the
# thread. Quotes are intact here so the filter expression can be read.
GH_VIEW = r"\bgh\s+(?P<sub>issue\s+view|pr\s+view|pr\s+diff)\b"
FILTER = r"(?:-q|--jq|-t|--template)(?:=|\s+)(?:'([^']*)'|\"([^\"]*)\"|(\S+))"
for m in re.finditer(GH_VIEW, blanked):
    seg = statement(blanked, m.end())
    args = seg.split("|", 1)[0]
    if lands_in_file(seg):
        continue
    if "diff" in m.group("sub") and re.search(r"--(?:name-only|stat)\b", args):
        continue
    f = re.search(FILTER, args)
    if "diff" not in m.group("sub") and re.search(r"--json\b", args) and f:
        expr = next(g for g in f.groups() if g is not None)
        if not re.search(r"\b(?:body(?:Text|HTML)?|comments)\b", expr):
            continue
    if "--comments" in args:
        block(
            "Blocked (context discipline): a comment pull lands in a file, never straight in context.\n"
            "Use one bare command: gh issue view <n> --comments > comments.scratch.log. "
            "Then Grep the log - or --json comments with a jq filter to a file."
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


def dump_block(names: list, seg: str) -> None:
    """Block when *names* is non-empty and the statement neither pipes to a
    filter nor lands in a file."""
    if names and not lands_in_file(seg) and not piped_to_filter(seg):
        block(DUMP_MSG.format(name=names[0]))


# A loop over guarded files whose body dumps them: `for f in src/*.py; do cat
# $f; done`, `foreach ($f in ls *.py) { cat $f }`, `Get-ChildItem *.py | % { gc $_ }`.
# The loop variable carries no extension, so the header (or the feeding pipe)
# is where the extension is seen; the body is judged like any statement.
READERS = r"(?:cat|more|less|type|Get-Content|gc)"
LOOP = (
    r"(?:\bfor\b|\bforeach\b|\bwhile\b|\bForEach-Object\b|\|\s*%)(?P<head>[^;{\n]*)"
    r"(?:[;\n]\s*do\b(?P<do>[\s\S]*?)\bdone\b|\{(?P<brace>[\s\S]*?)\})"
)
BODY_POS = r"(?:^|[;{|&\n]|\$\(|\b(?:then|do|else))\s*"
for m in re.finditer(LOOP, scan_cat):
    header = scan_cat[: m.start()].rsplit("\n", 1)[-1] + m.group("head")
    if not guarded_names(header):
        continue
    after = statement(scan_cat, m.end())
    if lands_in_file(after) or piped_to_filter(after):
        continue  # `for …; done > all.txt` / `… done | grep x`: the loop's output is bounded
    body = m.group("do") if m.group("do") is not None else m.group("brace")
    body = body.replace("||", ";")  # `cat $f || true` is not a pipe
    for r in re.finditer(BODY_POS + READERS + r"\b(?P<rest>[^;}\n]*)", body, re.I):
        if not lands_in_file(r.group("rest")) and not piped_to_filter(r.group("rest")):
            block(
                "Blocked (context discipline): a cat loop over source files is a mass whole-file Read.\n"
                "Use the Read tool per file you will edit, or grep for the lines you actually need."
            )

# cat / more / less / type / Get-Content / gc: every plain arg is a file. Options
# and PowerShell parameters are stepped over; `-TotalCount`/`-Tail`/`-Head`/
# `-First`/`-Last` bound the read and clear it, as does indexing or counting
# the result (`(Get-Content f)[10..40]`, `.Count`). `cat < x.py` counts too. A
# reader with no file arg fed by `xargs`, `find -exec`, or a lister's pipe
# (`ls`, `find`, `Get-ChildItem`) reads what that stage named — `git diff
# a.py | cat` is a no-pager idiom, not a dump, so only listers feed.
READER_POS = r"(?:" + CMD_POS + r"|\bxargs\s+(?:-\S+\s+)*|-(?:exec|x|X)\s+)"
LISTERS = r"(?:ls|find|fd|dir|Get-ChildItem|gci)"
FED_BY = (
    r"(?:\bxargs\s+(?:-\S+\s+)*|-(?:exec|x|X)\s+|(?:^|[;&(|])\s*" + LISTERS + r"\b[^|]*\|\s*)"
    + READERS + r"$"
)
for stmt in re.split(STATEMENT_SEP, scan_cat):
    for m in re.finditer(READER_POS + READERS + r"\b(?P<rest>[^|;&\n]*)", stmt, re.I):
        rest = m.group("rest")
        seg = stmt[m.start("rest"):]
        if re.search(r"-(?:TotalCount|Tail|Head|First|Last)\b", rest, re.I):
            continue
        if re.search(r"\)\s*(?:\.\w+|\[)", rest):
            continue
        names = guarded_names(rest)
        if not names:
            # The position match swallowed the `|` / `xargs` / `-exec` that feeds
            # this reader; look at everything before the reader's own name.
            fed = stmt[: m.start("rest")]
            if re.search(FED_BY, fed, re.I):
                names = guarded_names(fed)
        dump_block(names, seg)

# sed: only an unbounded script (`p`, `1,$p`, or empty) is a dump; any range,
# address or substitution is a targeted read or an edit. Quotes intact here so
# the script token is one group; a `\$` is a `$`.
SED = (
    CMD_POS
    + r"sed\b(?P<flags>(?:\s+--?[a-zA-Z-]+)*)\s+"
    + r"(?:'(?P<sq>[^']*)'|\"(?P<dq>[^\"]*)\"|(?P<bare>\S+))"
    + r"(?P<args>(?:\s+" + ARG + r")+)"
)
for m in re.finditer(SED, blanked):
    script = next(s for s in (m.group("sq"), m.group("dq"), m.group("bare")) if s is not None)
    if script.replace("\\", "").strip() in ("", "p", "1,$p"):
        dump_block(guarded_names(m.group("args")), statement(blanked, m.end("args")))

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
        dump_block(guarded_names(m.group("args")), statement(scan_cat, m.end("args")))

sys.exit(0)
