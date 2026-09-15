"""SessionStart hook: says, at the first turn, when this checkout is stale, littered or old.

Reports, never blocks - exit 0 always; any failure collapses to one stderr line. It runs
``git fetch -q origin`` with a 10 s timeout and, when that fails (no network, a slow
remote), reports from the last fetch and says how old that is. Then up to three lines,
each only when true, in this order:

    STALE: branch <b> is merged into origin/main (<n> commits behind). <remedy>
    RESIDUE: <k> worktrees on merged branches, <j> worktree-agent-* with no commits[, <d> dirty skipped]. uv run python scripts/prune_merged.py --apply
    RESIDUE: 0 removable, <d> dirty (clean or remove by hand): <paths>
    HOOKS: .claude/hooks differs from origin/main - this checkout enforces old rules.

plus a trailing ``FETCH:`` line when the fetch fell back. Silent when everything is fresh.

The STALE remedy follows where the session sits: in the main checkout it is ``git switch
main`` then ``git pull`` (two commands - the worktree guard refuses a ``&&`` chain); in a
linked worktree under ``.claude/worktrees/`` a switch is refused, so the remedy is to leave
the worktree and prune it from the main checkout.

RESIDUE counts what ``prune_merged.py --apply`` would remove: the session's own worktree
(the payload ``cwd``) is never residue, and a candidate with uncommitted or untracked
files is skipped and counted as dirty, the same gate the sweep applies. When every
candidate is dirty the sweep would remove nothing, and the second form names those paths:
a human has to clean or delete them, so silence would only let them pile up. The two ``gh pr
list`` calls (~35 s each on this box) run only when some branch could be merged by squash;
on ``main`` with no worktrees, ancestry alone decides and the start is not delayed.

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
    PROTECTED,
    WORKTREE_DIR,
    MergeFacts,
    dirty,
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


def same_path(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def in_linked_worktree(cwd, root):
    """Whether *cwd* sits under the checkout's ``.claude/worktrees/``."""
    prefix = os.path.normcase(os.path.realpath(root)) + os.sep
    prefix += os.path.normcase(WORKTREE_DIR.rstrip("/").replace("/", os.sep)) + os.sep
    return os.path.normcase(os.path.realpath(cwd)).startswith(prefix)


def stale_remedy(cwd, root):
    if in_linked_worktree(cwd, root):
        return (
            "This worktree's branch is merged: leave it (ExitWorktree) and run "
            "uv run python scripts/prune_merged.py --apply from the main checkout."
        )
    return f"Run git switch {BASE}, then git pull (two commands)."


def stale_line(run, branch, local_on_main, facts, cwd, root):
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
        + stale_remedy(cwd, root)
    )


def residue_line(run, worktrees, local_on_main, facts, cwd):
    """What ``prune_merged.py --apply`` would remove, counted the way it decides: never
    the worktree this session runs in, and a dirty candidate skipped (and counted).

    When every candidate is dirty the sweep would remove nothing, so the line names the
    paths instead: they are residue a human has to clean or delete, and staying silent
    about them is how they accumulate."""
    root = worktrees[0].path.rstrip("/")
    merged = empty = 0
    soiled = []
    for wt in worktrees[1:]:
        if same_path(wt.path, cwd):
            continue
        ok, why = worktree_decision(wt, root, local_on_main, facts)
        if not ok:
            continue
        if dirty(run, wt):
            soiled.append(wt.path)
        elif why == NO_COMMITS:
            empty += 1
        else:
            merged += 1
    if not (merged or empty):
        if not soiled:
            return None
        return (
            f"RESIDUE: 0 removable, {len(soiled)} dirty (clean or remove by hand): "
            + ", ".join(sorted(soiled))
        )
    dirty_note = f", {len(soiled)} dirty skipped" if soiled else ""
    return (
        f"RESIDUE: {merged} worktrees on merged branches, {empty} worktree-agent-* with no "
        f"commits{dirty_note}. uv run python scripts/prune_merged.py --apply"
    )


def squash_candidates(branch, worktrees):
    """The branches a ``gh pr list`` answer could change a verdict on: the session's own
    and every unlocked worktree's inside ``.claude/worktrees/``. Empty on a bare
    ``main`` checkout, where the forge has nothing to add to ancestry."""
    root = worktrees[0].path.rstrip("/")
    names = {branch} if branch and branch not in PROTECTED else set()
    for wt in worktrees[1:]:
        if wt.branch and not wt.locked and wt.path.startswith(root + "/" + WORKTREE_DIR):
            names.add(wt.branch)
    return names


def hooks_line(run):
    drift = run(["git", "diff", "--name-only", f"origin/{BASE}", "--", ":(top).claude/hooks"])
    if not drift.strip():
        return None
    return f"HOOKS: .claude/hooks differs from origin/{BASE} - this checkout enforces old rules."


def report(run, now=None, cwd=None):
    """The lines to print for the checkout ``run`` answers about, as seen from the
    session's *cwd* (the process cwd when None); empty when fresh."""
    now = time.time() if now is None else now
    cwd = os.getcwd() if cwd is None else cwd
    fetch_note = fetch(run, now)
    worktrees = parse_worktrees(run(["git", "worktree", "list", "--porcelain"]))
    if not worktrees:
        raise CommandError("git worktree list returned nothing - not inside a repo?")
    root = worktrees[0].path.rstrip("/")
    local_on_main = on_main(run)
    branch = run(["git", "branch", "--show-current"]).strip()
    facts = MergeFacts.ancestry_only(worktrees)
    if squash_candidates(branch, worktrees):
        try:
            facts = gather_facts(run, worktrees)
        except (CommandError, OSError):  # gh missing or offline: ancestry still decides
            pass
    candidates = [
        stale_line(run, branch, local_on_main, facts, cwd, root),
        residue_line(run, worktrees, local_on_main, facts, cwd),
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
        for line in report(timed_runner, cwd=cwd):
            print(line)
    except Exception as e:  # noqa: BLE001 - a session start is never the hook's to fail
        print(f"session-start: skipped ({type(e).__name__}: {e})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
