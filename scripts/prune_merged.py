"""Prune the residue merged work leaves behind: remote branches, local branches, worktrees.

Dry-run by default — prints what it would remove and why. ``--apply`` removes it.

    uv run python scripts/prune_merged.py            # list
    uv run python scripts/prune_merged.py --apply    # remove

What counts as merged, for a branch ``N`` whose tip is ``T``:

* ``T`` is already on ``origin/main`` (``git branch --merged origin/main``), or
* a PR from ``N`` **into main** merged with ``T`` as its head — the squash case, where the
  content landed but the commits themselves never became ancestors of main. A PR merged into
  any other base does not count: a stacked PR reads MERGED while its commits may never reach
  main (CLAUDE.md step 6).

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
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

Runner = Callable[[Sequence[str]], str]

WORKTREE_DIR = ".claude/worktrees/"
PROTECTED = frozenset({"main", "HEAD"})
BASE = "main"


class CommandError(RuntimeError):
    """A command the Runner issued failed; the message carries argv and stderr."""


@dataclass(frozen=True)
class Worktree:
    path: str
    head: str
    branch: str | None  # short name; None when detached
    locked: bool
    prunable: bool


@dataclass(frozen=True)
class MergeFacts:
    """What the repo and the forge say about branches, gathered once."""

    merged_into_main: dict[str, set[str]]  # head name -> head SHAs of PRs merged into main
    merged_elsewhere: set[str]  # head names of PRs merged into some other base
    open_prs: set[str]  # head names with an open PR
    held: set[str]  # branch names a locked worktree checks out


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


def parse_prs(gh_json: str) -> list[dict[str, str]]:
    """``gh pr list --json headRefName,headRefOid,baseRefName`` -> list of those dicts."""
    prs: list[dict[str, str]] = json.loads(gh_json or "[]")
    return prs


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


def _prune_decision(name: str, tip: str, on_main: set[str], facts: MergeFacts) -> tuple[bool, str]:
    """(prune?, reason) for a branch called ``name`` whose tip is ``tip``."""
    if name in PROTECTED:
        return False, "protected"
    if name in facts.held:
        return False, "held by a locked worktree"
    if name in facts.open_prs:
        return False, "open PR"
    if name in on_main:
        return True, "tip is on origin/main"
    if name in facts.merged_into_main:
        if tip in facts.merged_into_main[name]:
            return True, "PR merged (squash) at this tip"
        return False, "PR merged but branch has commits after it"
    if name in facts.merged_elsewhere:
        return False, "PR merged into a branch, not main"
    return False, "commits not on origin/main"


def _record(plan: Plan, bucket: list[str], item: str, label: str, ok: bool, why: str) -> None:
    if ok:
        bucket.append(item)
        plan.reasons[label] = why
    elif why != "protected":
        plan.skipped.append((label, why))


def _gh_heads(run: Runner, state: str) -> list[dict[str, str]]:
    return parse_prs(
        run(
            ["gh", "pr", "list", "--state", state, "--limit", "1000",
             "--json", "headRefName,headRefOid,baseRefName"]
        )
    )


def _refs(run: Runner, namespace: str) -> dict[str, str]:
    return parse_refs(
        run(["git", "for-each-ref", namespace, "--format=%(refname:short) %(objectname)"])
    )


def _on_main(run: Runner, *flags: str) -> set[str]:
    return parse_names(
        run(["git", "branch", *flags, "--merged", f"origin/{BASE}", "--format=%(refname:short)"])
    )


def build_plan(run: Runner) -> Plan:
    worktrees = parse_worktrees(run(["git", "worktree", "list", "--porcelain"]))
    if not worktrees:
        raise CommandError("git worktree list returned nothing - not inside a repo?")
    root = worktrees[0].path.rstrip("/")

    merged_into_main: dict[str, set[str]] = {}
    merged_elsewhere: set[str] = set()
    for pr in _gh_heads(run, "merged"):
        if pr.get("baseRefName") == BASE:
            merged_into_main.setdefault(pr["headRefName"], set()).add(pr.get("headRefOid", ""))
        else:
            merged_elsewhere.add(pr["headRefName"])
    facts = MergeFacts(
        merged_into_main=merged_into_main,
        merged_elsewhere=merged_elsewhere,
        open_prs={pr["headRefName"] for pr in _gh_heads(run, "open")},
        held={wt.branch for wt in worktrees if wt.locked and wt.branch},
    )
    local_refs = _refs(run, "refs/heads")
    remote_refs = {
        name.removeprefix("origin/"): sha
        for name, sha in _refs(run, "refs/remotes/origin").items()
        if name != "origin/HEAD"
    }
    local_on_main = _on_main(run)
    remote_on_main = {n.removeprefix("origin/") for n in _on_main(run, "-r")}

    plan = Plan()
    surviving_checkouts: dict[str, str] = {}  # branch -> worktree path that keeps it
    if worktrees[0].branch:
        surviving_checkouts[worktrees[0].branch] = worktrees[0].path

    for wt in worktrees[1:]:
        label = f"worktree {wt.path}"
        if not wt.path.startswith(root + "/" + WORKTREE_DIR):
            ok, why = False, "outside " + WORKTREE_DIR
        elif wt.locked:
            ok, why = False, "locked (a session holds it)"
        elif wt.branch is None:
            ok, why = False, "detached HEAD"
        else:
            ok, why = _prune_decision(wt.branch, wt.head, local_on_main, facts)
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
            ok, why = _prune_decision(name, sha, local_on_main, facts)
        _record(plan, plan.local_branches, name, label, ok, why)

    for name, sha in sorted(remote_refs.items()):
        ok, why = _prune_decision(name, sha, remote_on_main, facts)
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


def subprocess_runner(argv: Sequence[str]) -> str:
    proc = subprocess.run(list(argv), capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise CommandError(f"{' '.join(argv)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


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
