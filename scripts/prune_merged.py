"""Prune the residue merged work leaves behind: remote branches, local branches, worktrees.

Dry-run by default — prints what it would remove and why. ``--apply`` removes it.

    uv run python -m scripts.prune_merged            # list
    uv run python -m scripts.prune_merged --apply    # remove

What counts as merged is ``scripts/_merged.py``'s decision, shared with the SessionStart
hook that reports the same residue (``.claude/hooks/session-start.py``): a tip that is on
``origin/main``, or a PR **into main** squashed from exactly that tip; a PR merged into any
other base does not count (the stacked-PR trap, CLAUDE.md step 6).

Never touched: ``main`` / ``HEAD``; any branch that has an open PR; a branch whose tip
carries commits ``origin/main`` does not (a merged PR followed by new commits included); a
worktree that is locked (a running session holds it), dirty, or on a detached HEAD; the
branch a locked worktree holds, local and remote; and a local branch still checked out in
any worktree that survives.

Everything the script learns comes through one ``Runner`` (a callable from argv to stdout),
so the fake tier drives it on fixtures of the ``gh`` and ``git`` output
(``tests/test_prune_merged.py``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from scripts._merged import (
    branch_decision,
    gather_facts,
    merge_decision,
    on_main,
    parse_worktrees,
    refs,
    worktree_decision,
)
from scripts._run import CommandError, Runner, subprocess_runner


@dataclass(frozen=True)
class Plan:
    worktrees: list[str] = field(default_factory=list)  # paths to remove
    local_branches: list[str] = field(default_factory=list)
    remote_branches: list[str] = field(default_factory=list)  # names without ``origin/``
    reasons: dict[str, str] = field(default_factory=dict)  # item -> why it is in the plan
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (item, why not)

    @property
    def empty(self) -> bool:
        return not (self.worktrees or self.local_branches or self.remote_branches)


def _record(plan: Plan, bucket: list[str], item: str, label: str, ok: bool, why: str) -> None:
    if ok:
        bucket.append(item)
        plan.reasons[label] = why
    elif why != "protected":
        plan.skipped.append((label, why))


def build_plan(run: Runner) -> Plan:
    worktrees = parse_worktrees(run(["git", "worktree", "list", "--porcelain"]))
    if not worktrees:
        raise CommandError("git worktree list returned nothing - not inside a repo?")
    root = worktrees[0].path.rstrip("/")

    facts = gather_facts(run, worktrees)
    local_refs = refs(run, "refs/heads")
    remote_refs = {
        name.removeprefix("origin/"): sha
        for name, sha in refs(run, "refs/remotes/origin").items()
        if name != "origin/HEAD"
    }
    local_on_main = on_main(run)
    remote_on_main = {n.removeprefix("origin/") for n in on_main(run, "-r")}

    plan = Plan()
    surviving_checkouts: dict[str, str] = {}  # branch -> worktree path that keeps it
    if worktrees[0].branch:
        surviving_checkouts[worktrees[0].branch] = worktrees[0].path

    for wt in worktrees[1:]:
        label = f"worktree {wt.path}"
        ok, why = worktree_decision(run, wt, root, local_on_main, facts)
        if ok and run(["git", "-C", wt.path, "status", "--porcelain"]).strip():
            ok, why = False, "dirty (uncommitted or untracked files)"
        _record(plan, plan.worktrees, wt.path, label, ok, why)
        if not ok and wt.branch:
            surviving_checkouts[wt.branch] = wt.path

    for name, sha in sorted(local_refs.items()):
        label = f"local {name}"
        if name in surviving_checkouts:
            ok, why = False, f"checked out in {surviving_checkouts[name]}"
        else:
            ok, why = branch_decision(run, name, sha, local_on_main, facts)
        _record(plan, plan.local_branches, name, label, ok, why)

    for name, sha in sorted(remote_refs.items()):
        ok, why = merge_decision(name, sha, remote_on_main, facts)
        _record(plan, plan.remote_branches, name, f"remote origin/{name}", ok, why)
    return plan


# --- doing it -------------------------------------------------------------------------------


def apply_plan(run: Runner, plan: Plan) -> list[str]:
    """Worktrees first (they pin branches), then local branches, then remote branches.

    Each removal is its own command so one refusal cannot abort the rest; returns the
    failure messages.
    """
    failures: list[str] = []

    def attempt(argv: list[str]) -> None:
        try:
            run(argv)
        except CommandError as e:
            failures.append(str(e))

    for path in plan.worktrees:
        attempt(["git", "worktree", "remove", path])
    for name in plan.local_branches:
        attempt(["git", "branch", "-D", name])
    for i in range(0, len(plan.remote_branches), 50):
        attempt(["git", "push", "origin", "--delete", *plan.remote_branches[i : i + 50]])
    return failures


def render(plan: Plan, *, verbose: bool) -> str:
    lines = [f"remove  {label}  - {why}" for label, why in plan.reasons.items()]
    if verbose:
        lines.extend(f"keep    {label}  - {why}" for label, why in plan.skipped)
    lines.append(
        f"{len(plan.worktrees)} worktree(s), {len(plan.local_branches)} local branch(es), "
        f"{len(plan.remote_branches)} remote branch(es) to remove; {len(plan.skipped)} kept"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, run: Runner = subprocess_runner) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="remove instead of listing")
    ap.add_argument("--no-fetch", action="store_true", help="skip `git fetch --prune origin`")
    ap.add_argument("-v", "--verbose", action="store_true", help="also list what is kept and why")
    args = ap.parse_args(argv)
    try:
        if not args.no_fetch:
            run(["git", "fetch", "--prune", "origin"])
        run(["git", "worktree", "prune"])  # drop admin files of worktrees whose dir is gone
        plan = build_plan(run)
    except CommandError as e:
        print(e, file=sys.stderr)
        return 1
    print(render(plan, verbose=args.verbose))
    if plan.empty:
        return 0
    if not args.apply:
        print("dry run - re-run with --apply to remove.")
        return 0
    failures = apply_plan(run, plan)
    for msg in failures:
        print(f"FAILED  {msg}", file=sys.stderr)
    print(f"applied; {len(failures)} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
