"""Prune the residue merged work leaves behind: remote branches, local branches, worktrees.

Dry-run by default — prints what it would remove and why. ``--apply`` removes it.

    uv run python scripts/prune_merged.py            # list
    uv run python scripts/prune_merged.py --apply    # remove

What counts as merged, for a branch ``N`` whose tip is ``T``:

* ``T`` is already on ``origin/main`` (``git branch --merged origin/main``), or
* a merged PR's head was ``N`` at exactly ``T`` — the squash case, where the content landed
  but the commits themselves never became ancestors of main.

Never touched: ``main`` / ``HEAD``; any branch that has an open PR; a branch whose tip
carries commits ``origin/main`` does not (a merged PR followed by new commits included); a
worktree that is locked (a running session holds it), dirty, or on a detached HEAD; and a
local branch still checked out in a worktree that survives.

Everything the script learns comes through one ``Runner`` (a callable from argv to stdout),
so the fake tier drives it on fixtures of the ``gh`` and ``git`` output
(``tests/test_prune_merged.py``).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

Runner = Callable[[Sequence[str]], str]

WORKTREE_DIR = ".claude/worktrees/"
PROTECTED = frozenset({"main", "master", "HEAD"})


@dataclass(frozen=True)
class Worktree:
    path: str
    head: str
    branch: str | None  # short name; None when detached
    locked: bool
    prunable: bool


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


# --- parsing --------------------------------------------------------------------------------


def parse_worktrees(porcelain: str) -> list[Worktree]:
    """Parse ``git worktree list --porcelain``; the first entry is the main checkout."""
    out: list[Worktree] = []
    for block in porcelain.strip().split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        path = head = ""
        branch: str | None = None
        locked = prunable = False
        for ln in lines:
            key, _, val = ln.partition(" ")
            if key == "worktree":
                path = val.replace("\\", "/")
            elif key == "HEAD":
                head = val
            elif key == "branch":
                branch = val.removeprefix("refs/heads/")
            elif key == "locked":
                locked = True
            elif key == "prunable":
                prunable = True
        out.append(Worktree(path, head, branch, locked, prunable))
    return out


def parse_pr_heads(gh_json: str) -> dict[str, set[str]]:
    """``gh pr list --json headRefName,headRefOid`` -> head name -> set of head SHAs."""
    heads: dict[str, set[str]] = {}
    for pr in json.loads(gh_json or "[]"):
        heads.setdefault(pr["headRefName"], set()).add(pr.get("headRefOid", ""))
    return heads


def parse_refs(for_each_ref: str) -> dict[str, str]:
    """``git for-each-ref --format='%(refname:short) %(objectname)'`` -> name -> SHA."""
    refs: dict[str, str] = {}
    for ln in for_each_ref.splitlines():
        if ln.strip():
            name, _, sha = ln.strip().partition(" ")
            refs[name] = sha
    return refs


def parse_names(listing: str) -> set[str]:
    return {ln.strip() for ln in listing.splitlines() if ln.strip()}


# --- the decision ---------------------------------------------------------------------------


def _merged_reason(
    name: str,
    tip: str,
    *,
    merged_prs: dict[str, set[str]],
    open_prs: dict[str, set[str]],
    on_main: set[str],
) -> tuple[bool, str]:
    """(prune?, reason) for a branch called ``name`` whose tip is ``tip``."""
    if name in PROTECTED:
        return False, "protected"
    if name in open_prs:
        return False, "open PR"
    if name in on_main:
        return True, "tip is on origin/main"
    if name in merged_prs:
        if tip in merged_prs[name]:
            return True, "PR merged (squash) at this tip"
        return False, "PR merged but branch has commits after it"
    return False, "commits not on origin/main"


def build_plan(run: Runner) -> Plan:
    merged_prs = parse_pr_heads(
        run(
            ["gh", "pr", "list", "--state", "merged", "--limit", "1000",
             "--json", "headRefName,headRefOid"]
        )
    )
    open_prs = parse_pr_heads(
        run(
            ["gh", "pr", "list", "--state", "open", "--limit", "1000",
             "--json", "headRefName,headRefOid"]
        )
    )
    worktrees = parse_worktrees(run(["git", "worktree", "list", "--porcelain"]))
    local_refs = parse_refs(
        run(["git", "for-each-ref", "refs/heads", "--format=%(refname:short) %(objectname)"])
    )
    remote_refs = {
        name.removeprefix("origin/"): sha
        for name, sha in parse_refs(
            run(
                ["git", "for-each-ref", "refs/remotes/origin",
                 "--format=%(refname:short) %(objectname)"]
            )
        ).items()
        if name != "origin/HEAD"
    }
    local_on_main = parse_names(
        run(["git", "branch", "--merged", "origin/main", "--format=%(refname:short)"])
    )
    remote_on_main = {
        n.removeprefix("origin/")
        for n in parse_names(
            run(["git", "branch", "-r", "--merged", "origin/main", "--format=%(refname:short)"])
        )
    }

    plan = Plan()
    if not worktrees:
        raise SystemExit("git worktree list returned nothing - not inside a repo?")
    root = worktrees[0].path.rstrip("/")
    surviving_checkouts: dict[str, str] = {}  # branch -> worktree path that keeps it

    for wt in worktrees[1:]:
        if not wt.path.startswith(root + "/" + WORKTREE_DIR):
            if wt.branch:
                surviving_checkouts[wt.branch] = wt.path
            continue
        label = f"worktree {wt.path}"
        if wt.prunable:
            plan.skipped.append((label, "prunable - `git worktree prune` handles it"))
            continue
        if wt.locked:
            plan.skipped.append((label, "locked (a session holds it)"))
        elif wt.branch is None:
            plan.skipped.append((label, "detached HEAD"))
        else:
            ok, why = _merged_reason(
                wt.branch, wt.head, merged_prs=merged_prs, open_prs=open_prs,
                on_main=local_on_main,
            )
            if ok and run(["git", "-C", wt.path, "status", "--porcelain"]).strip():
                ok, why = False, "dirty (uncommitted or untracked files)"
            if ok:
                plan.worktrees.append(wt.path)
                plan.reasons[label] = why
                continue
            plan.skipped.append((label, why))
        if wt.branch:
            surviving_checkouts[wt.branch] = wt.path
    if worktrees[0].branch:
        surviving_checkouts[worktrees[0].branch] = worktrees[0].path

    for name, sha in sorted(local_refs.items()):
        label = f"local {name}"
        if name in surviving_checkouts:
            plan.skipped.append((label, f"checked out in {surviving_checkouts[name]}"))
            continue
        ok, why = _merged_reason(
            name, sha, merged_prs=merged_prs, open_prs=open_prs, on_main=local_on_main
        )
        if ok:
            plan.local_branches.append(name)
            plan.reasons[label] = why
        elif why != "protected":
            plan.skipped.append((label, why))

    for name, sha in sorted(remote_refs.items()):
        label = f"remote origin/{name}"
        ok, why = _merged_reason(
            name, sha, merged_prs=merged_prs, open_prs=open_prs, on_main=remote_on_main
        )
        if ok:
            plan.remote_branches.append(name)
            plan.reasons[label] = why
        elif why != "protected":
            plan.skipped.append((label, why))
    return plan


# --- doing it -------------------------------------------------------------------------------


def apply_plan(run: Runner, plan: Plan) -> None:
    """Worktrees first (they pin branches), then local branches, then remote branches."""
    for path in plan.worktrees:
        run(["git", "worktree", "remove", path])
    if plan.local_branches:
        run(["git", "branch", "-D", *plan.local_branches])
    for i in range(0, len(plan.remote_branches), 50):
        run(["git", "push", "origin", "--delete", *plan.remote_branches[i : i + 50]])


def render(plan: Plan, *, verbose: bool) -> str:
    lines = [f"remove  {label}  - {why}" for label, why in plan.reasons.items()]
    if verbose:
        lines.extend(f"keep    {label}  - {why}" for label, why in plan.skipped)
    lines.append(
        f"{len(plan.worktrees)} worktree(s), {len(plan.local_branches)} local branch(es), "
        f"{len(plan.remote_branches)} remote branch(es) to remove; {len(plan.skipped)} kept"
    )
    return "\n".join(lines)


def subprocess_runner(argv: Sequence[str]) -> str:
    proc = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise SystemExit(f"{' '.join(argv)} failed ({proc.returncode}):\n{proc.stderr.strip()}")
    return proc.stdout


def main(argv: Sequence[str] | None = None, run: Runner = subprocess_runner) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="remove instead of listing")
    ap.add_argument("--no-fetch", action="store_true", help="skip `git fetch --prune origin`")
    ap.add_argument("-v", "--verbose", action="store_true", help="also list what is kept and why")
    args = ap.parse_args(argv)
    if not args.no_fetch:
        run(["git", "fetch", "--prune", "origin"])
    plan = build_plan(run)
    print(render(plan, verbose=args.verbose))
    if plan.empty:
        return 0
    if args.apply:
        apply_plan(run, plan)
        print("applied.")
    else:
        print("dry run - re-run with --apply to remove.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
