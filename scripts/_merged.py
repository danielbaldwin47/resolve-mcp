"""What counts as merged - the one decision ``prune_merged.py`` and the SessionStart hook share.

For a branch ``N`` whose tip is ``T``, merged means:

* ``T`` is already on ``origin/main`` (``git branch --merged origin/main``), or
* a PR from ``N`` **into main** merged with ``T`` as its head - the squash case, where the
  content landed but the commits themselves never became ancestors of main. A PR merged into
  any other base does not count: a stacked PR reads MERGED while its commits may never reach
  main (CLAUDE.md step 6).

A ``worktree-agent-*`` branch "with no commits" is one whose tip some other branch already
contains (``git branch --contains``): cut from main or from a feature branch, it added
nothing of its own, so the checkout is residue whatever its base was.

Extracted from ``prune_merged.py`` so the hook that *reports* residue and the script that
*removes* it cannot disagree. Everything here learns what it knows through a ``Runner``
(``scripts/_run.py``), so the fake tier drives both callers on fixtures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from scripts._run import Runner

WORKTREE_DIR = ".claude/worktrees/"
PROTECTED = frozenset({"main", "HEAD"})
BASE = "main"
AGENT_PREFIX = "worktree-agent-"


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

    @classmethod
    def ancestry_only(cls, worktrees: list[Worktree]) -> MergeFacts:
        """The facts with no forge answer: only ``git branch --merged`` decides."""
        return cls(
            merged_into_main={},
            merged_elsewhere=set(),
            open_prs=set(),
            held={wt.branch for wt in worktrees if wt.locked and wt.branch},
        )


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


# --- gathering ------------------------------------------------------------------------------


def gh_heads(run: Runner, state: str) -> list[dict[str, str]]:
    return parse_prs(
        run(
            ["gh", "pr", "list", "--state", state, "--limit", "1000",
             "--json", "headRefName,headRefOid,baseRefName"]
        )
    )


def refs(run: Runner, namespace: str) -> dict[str, str]:
    return parse_refs(
        run(["git", "for-each-ref", namespace, "--format=%(refname:short) %(objectname)"])
    )


def on_main(run: Runner, *flags: str) -> set[str]:
    """Branch names whose tip is an ancestor of ``origin/main`` (``-r`` for remote ones)."""
    return parse_names(
        run(["git", "branch", *flags, "--merged", f"origin/{BASE}", "--format=%(refname:short)"])
    )


def gather_facts(run: Runner, worktrees: list[Worktree]) -> MergeFacts:
    """Ask the forge which branches merged where, and which have an open PR."""
    merged_into_main: dict[str, set[str]] = {}
    merged_elsewhere: set[str] = set()
    for pr in gh_heads(run, "merged"):
        if pr.get("baseRefName") == BASE:
            merged_into_main.setdefault(pr["headRefName"], set()).add(pr.get("headRefOid", ""))
        else:
            merged_elsewhere.add(pr["headRefName"])
    return MergeFacts(
        merged_into_main=merged_into_main,
        merged_elsewhere=merged_elsewhere,
        open_prs={pr["headRefName"] for pr in gh_heads(run, "open")},
        held={wt.branch for wt in worktrees if wt.locked and wt.branch},
    )


# --- the decision ---------------------------------------------------------------------------


def merge_decision(name: str, tip: str, on_main: set[str], facts: MergeFacts) -> tuple[bool, str]:
    """(merged?, reason) for a branch called ``name`` whose tip is ``tip``."""
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


NO_COMMITS = "worktree-agent-* with no commits"


def agent_without_commits(run: Runner, name: str, tip: str) -> bool:
    """A ``worktree-agent-*`` branch that never got a commit of its own.

    True when some other branch contains ``tip`` - the base it was cut from, or a branch
    its work was merged into. One ``git branch --contains`` per agent branch, so the
    check is asked only for names that carry the prefix.
    """
    if not name.startswith(AGENT_PREFIX):
        return False
    holders = parse_names(run(["git", "branch", "--contains", tip, "--format=%(refname:short)"]))
    return bool(holders - {name})


def branch_decision(
    run: Runner, name: str, tip: str, on_main: set[str], facts: MergeFacts
) -> tuple[bool, str]:
    """``merge_decision`` plus the agent-branch rule; for local branches only."""
    if name in PROTECTED or name in facts.held or name in facts.open_prs:
        return merge_decision(name, tip, on_main, facts)
    if agent_without_commits(run, name, tip):
        return True, NO_COMMITS
    return merge_decision(name, tip, on_main, facts)


def worktree_decision(
    run: Runner, wt: Worktree, root: str, on_main: set[str], facts: MergeFacts
) -> tuple[bool, str]:
    """(residue?, reason) for one worktree of the checkout at ``root``.

    Dirtiness is the caller's question - it costs a ``git status`` per worktree, and only
    the remover needs the answer.
    """
    if not wt.path.startswith(root + "/" + WORKTREE_DIR):
        return False, "outside " + WORKTREE_DIR
    if wt.locked:
        return False, "locked (a session holds it)"
    if wt.branch is None:
        return False, "detached HEAD"
    return branch_decision(run, wt.branch, wt.head, on_main, facts)
