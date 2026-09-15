#!/usr/bin/env python3
"""PreToolUse hook enforcing CLAUDE.md's context-discipline rules on the shell
tools (Bash and PowerShell — settings.json matches both).

Blocks (exit 2, message to the model):
  1. Noisy runs (NOISY_TOOLS below) that neither land in a file nor pipe to a
     filter: bare, piped to tail/head/cat/Select-Object, or piped to anything
     outside FILTERS (`| sort`, `| awk`, `| sed -n p`, `| xargs` re-emit the
     whole run) — the scratch-log+grep rule. A pipe whose every stage is a
     filter (`| grep -c FAILED`, `| wc -l`, `| Select-String x`) passes.
     `--version`, `--help` and `--collect-only` are not runs.
  2. `gh issue view` / `gh pr view` / `gh pr diff` that do not land in a file.
     A `--json … -q`/`--template` field filter passes unless it pulls the body
     or the comment thread; `gh pr diff --name-only|--stat` passes.
  3. `gh issue close` with no `--comment`/`-c` — a ticket's implementation
     record lives on the ticket, and a silent close loses it. A `gh issue
     comment <n>` on the same number in the same command counts as the record.
  4. A whole-file dump of a guarded file (guarded_ext.py) by any reader —
     `cat`, `more`, `less`, `type`, `Get-Content`/`gc`, `sed -n` with no range
     (`p`, `1,$p`, `$!p` — every line but the last is still the file),
     `head`/`tail` with no count, `head -c` with no count or one over
     BYTES_CAP — that is neither piped to a filter nor redirected. Ranged reads
     (`sed -n 10,40p`, `sed -n '$p'`, `head -50`, `head -c 400`,
     `Get-Content -TotalCount 50`) pass. Backslash and drive-letter paths
     count, and so do readers fed by `xargs`, `find -exec`, or a
     `Get-ChildItem` pipe. A dumped `*.scratch.log` gets its own message: the
     Grep tool plus the log's absolute path (resolved against the payload's
     `cwd`, the directory the command would run in), because the blocked
     agent's next move was a Read of `/tmp/<name>`, which is not a directory
     on this box.
  5. A `for`/`foreach` loop over guarded files whose body dumps them.
  6. `tail -f`/`-F` and `Get-Content -Wait` on any file: a follow streams
     forever, so the call never returns and the session stalls — Monitor owns
     that wait. The one escape is `run_in_background`, where not returning is
     the point. This is the only rule not scoped to the guarded extensions: a
     stall does not care what the file is called.

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
# A backgrounded call is detached: a follow that never returns is the point there,
# not a stall, so rule 6 steps aside for it.
background = bool(data.get("tool_input", {}).get("run_in_background"))
# The directory the command runs in. The hook process is started elsewhere (the
# harness's own cwd), so a relative log name resolves against this, not os.getcwd().
command_cwd = data.get("cwd") or os.getcwd()


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
    return re.split(STATEMENT_SEP, text[start:], maxsplit=1)[0]


def lands_in_file(seg: str) -> bool:
    """True when the statement's stdout leaves the context: a `>`/`>>`/`1>`/`&>`/`*>`
    redirect (a `2>` alone is stderr; `>&` is an fd dup), or a PowerShell sink."""
    if re.search(r"(?<![2-9])>(?!&)\s*[^\s&|;]", seg):
        return True
    return re.search(r"\|\s*(?:Out-File|Set-Content|Add-Content|Out-Null)\b", seg, re.I) is not None


# Pipe targets that re-emit rather than filter: after one of these a dump still
# enters context whole.
REEMITTERS = r"less|more|cat|nl|tee|Tee-Object|Out-Host|Out-String|Out-Default|Write-Output|Format-\w+"
# For a noisy run, a capping pipe (tail/head/Select-Object) is no better: it caps
# one run, and runs repeat.
PAGERS = REEMITTERS + r"|tail|head|Select-Object|select(?![\w-])"
# The stages that bound a noisy run the way the log-plus-Grep loop does: a match
# count or a few matching lines. A fixed list, because "not a re-emitter" let
# `| sort`, `| awk`, `| sed -n p` and `| xargs` pass a whole pytest run into
# context (review 2026-09-15, finding 3).
FILTERS = r"grep|egrep|fgrep|rg|wc|Select-String|findstr|Measure-Object"


def stages_after(seg: str) -> list:
    """The pipeline stages after the statement's first command."""
    return seg.split("|")[1:]


def piped_to_filter(seg: str) -> bool:
    """True when every stage after the command is one of FILTERS: what a noisy run
    may pipe to instead of landing in a scratch log."""
    stages = stages_after(seg)
    return bool(stages) and all(
        re.match(r"\s*(?:" + FILTERS + r")(?![\w-])", st, re.I) for st in stages
    )


def piped_onward(seg: str) -> bool:
    """True when the statement pipes to anything that is not a plain re-emitter -
    the dump rules' escape, wider than the noisy rule's because `| head -50` or
    `| Select-Object -First 30` bounds a file dump (it caps a run, which repeats)."""
    stages = stages_after(seg)
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
    # A pipe of filters only (`| grep -c FAILED`, `| wc -l`) already bounds the run
    # the way the log-plus-Grep loop does; bare, or piped to anything else, the
    # run floods context.
    if lands_in_file(seg) or piped_to_filter(seg):
        continue
    block(
        f"Blocked (context discipline): a {tool} run that is neither redirected nor piped only to "
        "filters (grep, rg, wc, Select-String, findstr, Measure-Object) puts its whole output in "
        "context.\n"
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
    if lands_in_file(seg) or re.search(r"(?<!\S)(?:--web|-w)(?!\S)", args):
        continue  # --web opens the browser; nothing enters context
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
            "Use one bare command: gh issue view <n> --comments > comments-<n>.scratch.log. "
            "Then Grep the log - or --json comments with a jq filter to a file."
        )
    block(
        "Blocked (context discipline): gh issue/pr view and pr diff land in a file, never straight in context.\n"
        "Use one bare command: gh issue view <n> --json body -q .body > issue-<n>.scratch.log "
        "(gh pr diff <n> > pr-<n>.scratch.log) - the ticket number in the name keeps two "
        "sessions in one checkout from reading each other's log. Then Grep the log for the "
        "section you need; "
        "a --json field filter (-q .title, .state) is fine."
    )

# ---------------------------------------------------------------- 3. silent closes
# A ticket's implementation record belongs on the ticket: what landed, which
# live ACs ran, what the close needs from the human (CLAUDE.md step 8). The
# 2026-08-15 audit found #167-#178, #184, #164 and #139 bulk-closed with no
# comment at all, and #219 closed with zero comments — its live record survives
# only on PR #243, where nobody reading the ticket will find it. A `gh issue
# comment <n>` on the same number in the same command is that record.
GH_CLOSE = r"\bgh\s+issue\s+close\b(?P<args>[^;&|\n]*)"
# A flag with an *empty* value is no record: `--comment ""` closes silently.
COMMENT_FLAG = r"(?<!\S)(?:--comment|-c)(?:=|\s+)(?:'[^']+'|\"[^\"]+\"|[^'\"\s]\S*)"
# A record is prose and carries `;` and `|` of its own ("landed as PR #260; live
# ACs run"), which would end the statement early: quoted runs collapse to one
# placeholder character, keeping only whether they are empty.
scan_close = re.sub(r"'([^']*)'", lambda q: "'X'" if q.group(1) else "''", blanked)
scan_close = re.sub(r"\"([^\"]*)\"", lambda q: '"X"' if q.group(1) else '""', scan_close)
for m in re.finditer(GH_CLOSE, scan_close):
    args = m.group("args")
    if re.search(COMMENT_FLAG, args):
        continue
    number = re.search(r"(?<!\S)#?(\d+)(?!\S)", args)
    if number and re.search(r"\bgh\s+issue\s+comment\s+#?" + number.group(1) + r"\b", scan_close):
        continue  # the record was posted in the same command, then the ticket closed
    block(
        "Blocked (workflow): a ticket closed with no comment loses its implementation record "
        "- what landed, the PR link, which live ACs ran, and the ## Needs from you section.\n"
        "Close with the record: gh issue close <n> --comment \"...\" (a long record can go up "
        "first as gh issue comment <n> -F - <<'EOF' ... EOF in the same command). If the comment "
        "already went up in an earlier call, name it here: --comment \"see the comment above\"."
    )

# ---------------------------------------------------------------- 4. whole-file dumps
DUMP_MSG = (
    "Blocked (context discipline): a whole-file dump of {name} puts the whole file in context - "
    "the rules govern content entering context, not which tool fetched it.\n"
    "Grep for the lines you need first, then the Read tool with offset/limit around the hit - or a ranged read "
    "(sed -n 10,40p, head -50, Get-Content -TotalCount 50); a dump piped to grep or redirected to a file passes."
)


# A dumped scratch log is the one case where the generic message measurably
# misfires: the 2026-09-15 retro counted 13 blocks followed by a Read of
# `/tmp/<name>` — a path that does not exist on Windows and, when it resolves at
# all, resolves into another agent's worktree. So the message names the tool,
# the pattern and the log's absolute path instead of describing the rule.
SCRATCH_MSG = (
    "Blocked (context discipline): a whole-file dump of {name} puts the whole log in context.\n"
    "Read it back with the Grep tool:\n"
    "  pattern FAILED|passed|error, path {path}\n"
    "/tmp is not a directory on this box; scratch logs live in the cwd that wrote them."
)


def scratch_path(name: str) -> str:
    """*name* as an absolute path, resolved against the payload's `cwd` — the
    directory the blocked command would have written the log into. Deliberately
    not CLAUDE_PROJECT_DIR, which names the main checkout while a worktree
    session writes its logs beside itself; and not the hook process's own cwd,
    which is wherever the harness started it (finding 9)."""
    return name if os.path.isabs(name) else os.path.normpath(os.path.join(command_cwd, name))


def guarded_names(args: str) -> list:
    # Case-insensitive: Windows paths spell `SRC\\CONFIG.JSON` and `X.PY` too.
    return [a for a in re.findall(ARG, args) if re.search(GUARDED, a, re.I)]


def dump_block(names: list, seg: str) -> None:
    """Block when *names* is non-empty and the statement neither pipes to a
    filter nor lands in a file."""
    if not names or lands_in_file(seg) or piped_onward(seg):
        return
    scratch = next((n for n in names if n.lower().endswith(".scratch.log")), "")
    if scratch:
        block(SCRATCH_MSG.format(name=scratch, path=scratch_path(scratch)))
    else:
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
    if lands_in_file(after) or piped_onward(after):
        continue  # `for …; done > all.txt` / `… done | grep x`: the loop's output is bounded
    body = m.group("do") if m.group("do") is not None else m.group("brace")
    body = body.replace("||", ";")  # `cat $f || true` is not a pipe
    for r in re.finditer(BODY_POS + READERS + r"\b(?P<rest>[^;}\n]*)", body, re.I):
        if not lands_in_file(r.group("rest")) and not piped_onward(r.group("rest")):
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
# PowerShell accepts any unambiguous prefix of a parameter name (`-tot 5`,
# `-Total 5`, `-Fi 3`), so every prefix of the bounding parameters clears a
# read; a single letter does not (`-t` is ambiguous, `-h`/`-f`/`-l` read like
# Unix flags).
BOUNDING_PARAM = (
    r"-(?:"
    + "|".join(
        name[:n]
        for name in ("TotalCount", "Tail", "Head", "First", "Last")
        for n in range(len(name), 1, -1)  # `-t`/`-h`/`-f`/`-l` alone do not clear
    )
    + r")\b"
)
READER_POS = r"(?:" + CMD_POS + r"|\bxargs\s+(?:-\S+\s+)*|-(?:exec|x|X)\s+)"
LISTERS = r"(?:ls|find|fd|dir|Get-ChildItem|gci)"
FED_BY = (
    r"(?:\bxargs\s+(?:-\S+\s+)*|-(?:exec|x|X)\s+|(?:^|[;&(|])\s*" + LISTERS + r"\b[^|]*\|\s*)"
    + READERS + r"$"
)
# `Get-Content -Wait` (any unambiguous prefix: `-W`, `-Wa`, `-Wai`) is tail -f by
# another name: the read never returns. Same rule, same message, same escape.
WAIT_PARAM = r"(?<![\w-])-W(?:a(?:i(?:t)?)?)?(?![\w-])"


def follow_block(what: str, target: str) -> None:
    """Rule 6's block for a follow of *target* by *what* (`tail -f`, `Get-Content -Wait`)."""
    block(
        f"Blocked: {what} on {target} streams forever; the call never returns and the "
        "session stalls.\n"
        "Use Monitor on the file to wait for a condition, or read what is there now with the "
        "Grep tool.\n"
        "A follow you mean to leave running belongs in a run_in_background call."
    )


for stmt in re.split(STATEMENT_SEP, scan_cat):
    for m in re.finditer(
        READER_POS + r"(?P<reader>" + READERS + r")\b(?P<rest>[^|;&\n]*)", stmt, re.I
    ):
        rest = m.group("rest")
        seg = stmt[m.start("rest"):]
        if m.group("reader").lower() in ("get-content", "gc") and re.search(WAIT_PARAM, rest, re.I):
            # Judged on any file, like tail -f: the stall is the parameter, not the file.
            targets = [a for a in re.findall(ARG, rest) if not a.startswith("-")]
            if targets and not background:
                follow_block("Get-Content -Wait", targets[0])
            continue
        if re.search(BOUNDING_PARAM, rest, re.I):
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

# sed: an unbounded script (`p`, `1,$p`, `1,$ p`, `$!p`, or empty) is a dump; any
# range, address or substitution is a targeted read or an edit. `$!p` is an
# address in form only: every line but the last is the whole file, so the #274
# retro was wrong to unblock it (review 2026-09-15, finding 4); `$p` — the last
# line alone — is the bounded read. Quotes intact here so the script token is one
# group; a `\$` is a `$`.
SED = (
    CMD_POS
    + r"sed\b(?P<flags>(?:\s+--?[a-zA-Z-]+)*)\s+"
    + r"(?:'(?P<sq>[^']*)'|\"(?P<dq>[^\"]*)\"|(?P<bare>\S+))"
    + r"(?P<args>(?:\s+" + ARG + r")+)"
)
for m in re.finditer(SED, blanked):
    script = next(s for s in (m.group("sq"), m.group("dq"), m.group("bare")) if s is not None)
    if re.sub(r"[\\\s]", "", script) in ("", "p", "1,$p", "$!p"):
        dump_block(guarded_names(m.group("args")), statement(blanked, m.end("args")))

# head / tail: a count bounds the read, in lines (`-50`, `-n 50`) or in bytes
# (`-c 400`, up to BYTES_CAP — `head -c 900000` is the file; finding 5); a
# countless `-c` is a dump by another name. `--bytes` stays a dump by spelling
# alone: the long form is what a scripted whole-file pull reaches for, and the
# short `-c <n>` is the interactive peek #274 unblocked.
BYTES_CAP = 4096
HEADTAIL = (
    CMD_POS
    + r"(?P<cmd>head|tail)\b(?P<flags>(?:\s+(?:-n\s*\d+|-\d+|--lines(?:=|\s+)\d+|-c\s*\d*|"
    + r"--bytes(?:=|\s+)\S+|-[a-zA-Z]+))*)"
    + r"(?P<args>(?:\s+(?:<\s*)?" + ARG + r")+)"
)
# `-f`/`-F`/`--follow`, alone or bundled (`-qf`, `-fn 20`): the flag that never
# returns. No other short flag of head/tail carries an f, so any letter bundle
# holding one is a follow.
FOLLOW = r"(?<![\w-])-(?:[a-zA-Z]*[fF][a-zA-Z]*|-follow\b)"
for m in re.finditer(HEADTAIL, scan_cat):
    flags = m.group("flags")
    names = guarded_names(m.group("args"))
    # `--follow` is no alternative in the flags group, so it lands among the args:
    # every dash-led token counts as a flag for this one check, never a file name.
    words = m.group("args").split()
    dashed = flags + " " + " ".join(a for a in words if a.startswith("-"))
    # A count that the flags group left behind (`-n 20`, `-c +10`) is no file name,
    # and neither is the `<` of a stdin redirect (`tail -f < app.log`).
    targets = [
        a for a in words if a != "<" and not a.startswith("-") and not re.fullmatch(r"\+?\d+", a)
    ]
    # A follow streams into whatever it is piped or redirected into, so it is
    # judged before the dump rules and takes no landing or filter escape — and
    # it is judged on any file, guarded extension or not, because what stalls
    # the session is the flag, not the file.
    follows = m.group("cmd").lower() == "tail" and re.search(FOLLOW, dashed)
    if follows and targets and background:
        continue  # a detached follow is a stream nobody waits on, not a dump
    if follows and targets:
        follow_block("tail -f", targets[0])
    byte_count = re.search(r"-c\s*(\d+)", flags)
    bytes_bounded = byte_count is not None and int(byte_count.group(1)) <= BYTES_CAP
    bounded = bytes_bounded or re.search(r"-n\s*\d|-\d|--lines", flags)
    bytes_dump = re.search(r"-c(?!\s*\d)|--bytes", flags) or (byte_count and not bytes_bounded)
    if bytes_dump or not bounded:
        dump_block(names, statement(scan_cat, m.end("args")))

sys.exit(0)
