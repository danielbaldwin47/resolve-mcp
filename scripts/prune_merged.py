"""Prune the residue of merged PRs: remote branches, local branches, worktrees (#249).

    uv run python scripts/prune_merged.py            # dry run: print what would go
    uv run python scripts/prune_merged.py --apply    # delete it

One bare command, so the worktree guard accepts it from any checkout of the repo.

What counts as merged is decided by GitHub, not by ancestry: `gh pr list --state merged`
names each merged PR's head branch and the commit it was merged at (`headRefOid`).
Squash-merged branches are never ancestors of `origin/main`, so ancestry alone would keep
every one of them; the PR head oid is what proves them done. A branch is pruned only when

  - some merged PR has it as head, and no open PR does, and
  - its tip is either an ancestor of `origin/main` or exactly a merged PR's head oid
    (a tip past the merged head is "unmerged commits" and is kept).

Worktrees under `.claude/worktrees/` go by the same rule for their branch, and are also
kept when locked (Claude Code locks a worktree for the session that owns it), dirty,
detached, or holding the current directory. Nothing outside `.claude/worktrees/` is ever
removed. Removing a worktree first is what frees its branch for deletion, so the plan
orders worktrees → local branches → remote branches.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

PROTECTED = {"main", "master", "HEAD"}
WORKTREE_DIR = PurePosixPath(".claude") / "worktrees"


@dataclass(frozen=True)
class Worktree:
    path: PurePosixPath
    head: str
    branch: str | None  # None when detached
    locked: str | None  # lock reason ("" when locked without one), None when unlocked


@dataclass
class Plan:
    worktrees: list[Worktree] = field(default_factory=list)
    local: list[str] = field(default_factory=list)
    remote: list[str] = field(default_factory=list)
    kept: list[tuple[str, str]] = field(default_factory=list)  # (what, reason)


# ---------------------------------------------------------------- parsers (pure)


def parse_prs(text: str) -> dict[str, set[str]]:
    """`gh pr list --json headRefName,headRefOid` → head branch → set of head oids."""
    out: defaultdict[str, set[str]] = defaultdict(set)
    for pr in json.loads(text):
        out[pr["headRefName"]].add(pr["headRefOid"])
    return dict(out)


def parse_worktrees(porcelain: str) -> list[Worktree]:
    """`git worktree list --porcelain` → entries in listed order (main worktree first)."""
    out: list[Worktree] = []
    for block in porcelain.strip().split("\n\n"):
        path: PurePosixPath | None = None
        head = ""
        branch: str | None = None
        locked: str | None = None
        for line in block.splitlines():
            key, _, rest = line.partition(" ")
            if key == "worktree":
                path = PurePosixPath(rest)
            elif key == "HEAD":
                head = rest
            elif key == "branch":
                branch = rest.removeprefix("refs/heads/")
            elif key == "locked":
                locked = rest
        if path is not None:
            out.append(Worktree(path, head, branch, locked))
    return out


# ---------------------------------------------------------------- planner (pure)


def _branch_verdict(
    name: str,
    tip: str,
    merged: dict[str, set[str]],
    open_heads: set[str],
    on_main: set[str],
) -> str | None:
    """None when the branch may go; otherwise the reason it stays."""
    if name in PROTECTED:
        return "protected"
    if name in open_heads:
        return "open PR"
    if name not in merged:
        return "no PR merged from this branch"
    if name in on_main or tip in merged[name]:
        return None
    return "unmerged commits past the merged PR head"


def plan(
    *,
    merged: dict[str, set[str]],
    open_heads: set[str],
    local: dict[str, str],
    local_merged: set[str],
    remote: dict[str, str],
    remote_merged: set[str],
    worktrees: list[Worktree],
    dirty: set[PurePosixPath],
    cwd: PurePosixPath,
) -> Plan:
    """Decide what goes. `local`/`remote` map branch name → tip sha; `*_merged` are the
    names `git branch --merged origin/main` reports (tips that are ancestors of main).
    `dirty` is the set of worktree paths with a non-empty `git status --porcelain`."""
    p = Plan()
    if not worktrees:
        raise ValueError("no worktrees listed; refusing to plan without the main checkout")
    root = worktrees[0].path
    ours = root / WORKTREE_DIR

    checked_out: set[str] = set()
    for wt in worktrees:
        if wt.branch is not None:
            checked_out.add(wt.branch)
    for wt in worktrees[1:]:
        if not wt.path.is_relative_to(ours):
            continue  # not a Claude worktree; not ours to touch
        reason = _worktree_verdict(wt, merged, open_heads, local_merged, dirty, cwd)
        if reason is None:
            p.worktrees.append(wt)
            if wt.branch is not None:
                checked_out.discard(wt.branch)  # removing the worktree frees the branch
        else:
            p.kept.append((str(wt.path), reason))

    for name, tip in sorted(local.items()):
        reason = _branch_verdict(name, tip, merged, open_heads, local_merged)
        if reason is None and name in checked_out:
            reason = "checked out in a kept worktree"
        if reason is None:
            p.local.append(name)
        elif reason != "protected":
            p.kept.append((name, reason))

    for name, tip in sorted(remote.items()):
        reason = _branch_verdict(name, tip, merged, open_heads, remote_merged)
        if reason is None:
            p.remote.append(name)
        elif reason != "protected":
            p.kept.append((f"origin/{name}", reason))
    return p


def _worktree_verdict(
    wt: Worktree,
    merged: dict[str, set[str]],
    open_heads: set[str],
    local_merged: set[str],
    dirty: set[PurePosixPath],
    cwd: PurePosixPath,
) -> str | None:
    if wt.locked is not None:
        return f"locked: {wt.locked or '(no reason)'}"
    if cwd == wt.path or cwd.is_relative_to(wt.path):
        return "holds the current directory"
    if wt.branch is None:
        return "detached HEAD"
    if wt.path in dirty:
        return "dirty working tree"
    reason = _branch_verdict(wt.branch, wt.head, merged, open_heads, local_merged)
    if reason == "protected":
        return f"branch {wt.branch} is protected"
    return reason


def render(p: Plan, *, apply: bool) -> str:
    verb = "" if apply else "would "
    lines = [f"{verb}remove worktree {wt.path} ({wt.branch})" for wt in p.worktrees]
    lines += [f"{verb}delete local branch {b}" for b in p.local]
    lines += [f"{verb}delete remote origin/{b}" for b in p.remote]
    if not lines:
        lines.append("nothing to prune")
    lines += [f"kept {what}: {why}" for what, why in p.kept]
    if not apply:
        lines.append("dry run — pass --apply to delete")
    return "\n".join(lines)


# ---------------------------------------------------------------- shell


def _run(*cmd: str, check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode != 0:
        sys.exit(f"{' '.join(cmd)} failed ({r.returncode}):\n{r.stderr.strip()}")
    return r.stdout


def _refs(pattern: str, *extra: str) -> dict[str, str]:
    text = _run(
        "git", "for-each-ref", "--format=%(refname:short) %(objectname)", *extra, pattern
    )
    out: dict[str, str] = {}
    for line in text.splitlines():
        name, _, sha = line.partition(" ")
        out[name.removeprefix("origin/")] = sha
    return out


def gather(*, fetch: bool) -> Plan:
    if fetch:
        _run("git", "fetch", "--prune", "origin")
    merged = parse_prs(
        _run("gh", "pr", "list", "--state", "merged", "--limit", "1000", "--json",
             "headRefName,headRefOid")
    )
    open_heads = set(parse_prs(
        _run("gh", "pr", "list", "--state", "open", "--limit", "1000", "--json",
             "headRefName,headRefOid")
    ))
    worktrees = parse_worktrees(_run("git", "worktree", "list", "--porcelain"))
    dirty = {
        wt.path
        for wt in worktrees[1:]
        if _run("git", "-C", str(wt.path), "status", "--porcelain", check=False).strip()
    }
    return plan(
        merged=merged,
        open_heads=open_heads,
        local=_refs("refs/heads"),
        local_merged=set(_refs("refs/heads", "--merged=origin/main")),
        remote=_refs("refs/remotes/origin"),
        remote_merged=set(_refs("refs/remotes/origin", "--merged=origin/main")),
        worktrees=worktrees,
        dirty=dirty,
        cwd=PurePosixPath(Path.cwd().as_posix()),
    )


def execute(p: Plan) -> int:
    """Apply the plan; a failed step is reported and the rest continues. Returns 0/1."""
    failures = 0
    for wt in p.worktrees:
        r = subprocess.run(
            ["git", "worktree", "remove", str(wt.path)], capture_output=True, text=True
        )
        if r.returncode:
            failures += 1
            print(f"FAILED worktree remove {wt.path}: {r.stderr.strip()}")
    _run("git", "worktree", "prune")
    for b in p.local:
        # -D, not -d: squash-merged branches are not ancestors of main, and the plan
        # already proved each one merged through its PR.
        r = subprocess.run(["git", "branch", "-D", b], capture_output=True, text=True)
        if r.returncode:
            failures += 1
            print(f"FAILED branch -D {b}: {r.stderr.strip()}")
    if p.remote:
        r = subprocess.run(
            ["git", "push", "origin", "--delete", *p.remote], capture_output=True, text=True
        )
        if r.returncode:
            failures += 1
            print(f"FAILED push --delete: {r.stderr.strip()}")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="delete instead of printing")
    ap.add_argument("--no-fetch", action="store_true", help="skip `git fetch --prune`")
    args = ap.parse_args(argv)
    p = gather(fetch=not args.no_fetch)
    print(render(p, apply=args.apply))
    return execute(p) if args.apply else 0


if __name__ == "__main__":
    sys.exit(main())
