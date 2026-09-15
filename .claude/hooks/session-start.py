"""SessionStart hook: says, at the first turn, when this checkout is stale, littered or old.

Reports, never blocks - exit 0 always; any failure collapses to one stderr line. It runs
``git fetch -q origin`` with a 10 s timeout and, when that fails (no network, a slow
remote), reports from the last fetch and says how old that is. Then up to three lines,
each only when true, in this order:

    STALE: branch <b> is merged into origin/main (<n> commits behind). git switch main && git pull.
    RESIDUE: <k> worktrees on merged branches, <j> worktree-agent-* with no commits. uv run python scripts/prune_merged.py --apply
    HOOKS: .claude/hooks differs from origin/main - this checkout enforces old rules.

plus a trailing ``FETCH:`` line when the fetch fell back. Silent when everything is fresh.

"Merged" and "worktree with no commits" are ``scripts/_merged.py``'s decisions - the same
ones ``scripts/prune_merged.py`` removes on - so the report and the sweep cannot disagree.
Every command goes through a ``Runner`` (``scripts/_run.py``), which is the seam
``tests/test_session_start.py`` drives on a fake. Stdlib only.
"""

import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts._merged import (  # noqa: E402
    BASE,
    NO_COMMITS,
    MergeFacts,
    gather_facts,
    merge_decision,
    on_main,
    parse_worktrees,
    worktree_decision,
)
from scripts._run import CommandError  # noqa: E402

FETCH_TIMEOUT = 10  # seconds; the ticket's budget for a session-start network call
COMMAND_TIMEOUT = 30  # seconds; gh and git never hang a session start


def timed_runner(argv):
    """The real Runner: a timeout becomes a CommandError like any other failure."""
    argv = list(argv)
    timeout = FETCH_TIMEOUT if argv[:2] == ["git", "fetch"] else COMMAND_TIMEOUT
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise CommandError(f"{' '.join(argv)} timed out after {timeout}s") from e
    if proc.returncode != 0:
        raise CommandError(f"{' '.join(argv)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def format_age(seconds):
    seconds = max(0, int(seconds))
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def last_fetch_age(run, now):
    """Age of the last fetch, from FETCH_HEAD's mtime in the common git dir; None if never."""
    common = Path(run(["git", "rev-parse", "--git-common-dir"]).strip())
    fetch_head = common / "FETCH_HEAD"
    if not fetch_head.exists():
        return None
    return format_age(now - fetch_head.stat().st_mtime)


def fetch(run, now):
    """Fetch origin; on failure, the FETCH line naming the fallback, else None."""
    try:
        run(["git", "fetch", "-q", "origin"])
        return None
    except (CommandError, OSError):
        age = last_fetch_age(run, now)
        when = f"reporting from the last fetch, {age} ago" if age else "no earlier fetch on record"
        return f"FETCH: git fetch origin failed or timed out; {when}."


def stale_line(run, branch, local_on_main, facts):
    if not branch:  # detached HEAD
        return None
    # The session's own worktree may be locked; that must not hide its branch being merged.
    own = dataclasses.replace(facts, held=facts.held - {branch})
    merged, _ = merge_decision(branch, run(["git", "rev-parse", "HEAD"]).strip(), local_on_main, own)
    if not merged:
        return None
    behind = int(run(["git", "rev-list", "--count", f"HEAD..origin/{BASE}"]).strip() or 0)
    if behind == 0:  # a fresh branch at origin/main is not stale, only unstarted
        return None
    return (
        f"STALE: branch {branch} is merged into origin/{BASE} ({behind} commits behind). "
        f"git switch {BASE} && git pull."
    )


def residue_line(run, worktrees, local_on_main, facts):
    root = worktrees[0].path.rstrip("/")
    merged = empty = 0
    for wt in worktrees[1:]:
        ok, why = worktree_decision(run, wt, root, local_on_main, facts)
        if not ok:
            continue
        if why == NO_COMMITS:
            empty += 1
        else:
            merged += 1
    if not (merged or empty):
        return None
    return (
        f"RESIDUE: {merged} worktrees on merged branches, {empty} worktree-agent-* with no "
        f"commits. uv run python scripts/prune_merged.py --apply"
    )


def hooks_line(run):
    drift = run(["git", "diff", "--name-only", f"origin/{BASE}", "--", ":(top).claude/hooks"])
    if not drift.strip():
        return None
    return f"HOOKS: .claude/hooks differs from origin/{BASE} - this checkout enforces old rules."


def report(run, now=None):
    """The lines to print for the checkout ``run`` answers about; empty when fresh."""
    now = time.time() if now is None else now
    fetch_note = fetch(run, now)
    worktrees = parse_worktrees(run(["git", "worktree", "list", "--porcelain"]))
    if not worktrees:
        raise CommandError("git worktree list returned nothing - not inside a repo?")
    local_on_main = on_main(run)
    try:
        facts = gather_facts(run, worktrees)
    except (CommandError, OSError):  # gh missing or offline: ancestry still decides
        facts = MergeFacts.ancestry_only(worktrees)
    branch = run(["git", "branch", "--show-current"]).strip()
    candidates = [
        stale_line(run, branch, local_on_main, facts),
        residue_line(run, worktrees, local_on_main, facts),
        hooks_line(run),
        fetch_note,
    ]
    return [line for line in candidates if line]


def main():
    try:
        raw = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        if cwd:
            os.chdir(cwd)
        for line in report(timed_runner):
            print(line)
    except Exception as e:  # noqa: BLE001 - a session start is never the hook's to fail
        print(f"session-start: skipped ({type(e).__name__}: {e})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
