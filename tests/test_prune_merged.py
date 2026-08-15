"""scripts/prune_merged.py at its one seam: the Runner.

Every ``gh`` / ``git`` command the script issues is answered from a fixture keyed on argv;
mutating commands are recorded, never run. Fixture shapes are the real ones —
``gh pr list --json headRefName,headRefOid`` and ``git worktree list --porcelain``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from scripts._run import CommandError
from scripts.prune_merged import (
    Plan,
    apply_plan,
    build_plan,
    main,
    parse_worktrees,
)

ROOT = "C:/Users/x/repos/resolve-mcp"
WT = f"{ROOT}/.claude/worktrees"

MAIN = "a" * 40
SQUASHED_TIP = "b" * 40  # head of a squash-merged PR: never an ancestor of main
AFTER_MERGE = "c" * 40  # commits pushed after the PR merged
UNMERGED = "d" * 40
OPEN_TIP = "e" * 40
FRESH = "f" * 40  # a branch that is on main by ancestry (merge-commit PR, or no commits yet)
STACKED = "1" * 40  # head of a PR squash-merged into another branch, never into main

MERGED_PRS = json.dumps(
    [
        # squash into main; branch still at the squashed tip
        {"headRefName": "issue-1", "headRefOid": SQUASHED_TIP, "baseRefName": "main"},
        # merge-commit into main; tip is an ancestor of main
        {"headRefName": "issue-2", "headRefOid": FRESH, "baseRefName": "main"},
        # merged into main, then more commits pushed to the branch
        {"headRefName": "issue-3", "headRefOid": "9" * 40, "baseRefName": "main"},
        # stacked: squash-merged into issue-2, never into main; reads MERGED on the forge
        {"headRefName": "issue-8", "headRefOid": STACKED, "baseRefName": "issue-2"},
    ]
)
OPEN_PRS = json.dumps([{"headRefName": "issue-4", "headRefOid": OPEN_TIP, "baseRefName": "main"}])

PORCELAIN = f"""worktree {ROOT}
HEAD {MAIN}
branch refs/heads/main

worktree {WT}/issue-1
HEAD {SQUASHED_TIP}
branch refs/heads/issue-1

worktree {WT}/issue-2
HEAD {FRESH}
branch refs/heads/issue-2

worktree {WT}/issue-3
HEAD {AFTER_MERGE}
branch refs/heads/issue-3

worktree {WT}/issue-4
HEAD {OPEN_TIP}
branch refs/heads/issue-4

worktree {WT}/issue-5
HEAD {FRESH}
branch refs/heads/issue-5
locked

worktree {WT}/issue-6
HEAD {UNMERGED}
branch refs/heads/issue-6

worktree {WT}/issue-7
HEAD {FRESH}
branch refs/heads/issue-7

worktree {WT}/issue-8
HEAD {STACKED}
branch refs/heads/issue-8

worktree {WT}/detached
HEAD {FRESH}
detached

worktree C:/Users/x/elsewhere
HEAD {FRESH}
branch refs/heads/elsewhere
"""

LOCAL_REFS = "\n".join(
    [
        f"main {MAIN}",
        f"issue-1 {SQUASHED_TIP}",
        f"issue-2 {FRESH}",
        f"issue-3 {AFTER_MERGE}",
        f"issue-4 {OPEN_TIP}",
        f"issue-5 {FRESH}",
        f"issue-6 {UNMERGED}",
        f"issue-7 {FRESH}",
        f"issue-8 {STACKED}",
        f"elsewhere {FRESH}",
        f"orphan-on-main {FRESH}",  # no worktree, no PR, but nothing main lacks
        f"orphan-ahead {UNMERGED}",
    ]
)
REMOTE_REFS = "\n".join(
    [
        f"origin/HEAD {MAIN}",
        f"origin/main {MAIN}",
        f"origin/issue-1 {SQUASHED_TIP}",
        f"origin/issue-2 {FRESH}",
        f"origin/issue-3 {AFTER_MERGE}",
        f"origin/issue-4 {OPEN_TIP}",
        f"origin/issue-5 {FRESH}",
        f"origin/issue-6 {UNMERGED}",
        f"origin/issue-8 {STACKED}",
    ]
)
# ``git branch --merged origin/main``: names whose tip is an ancestor of origin/main.
LOCAL_ON_MAIN = "\n".join(
    ["main", "issue-2", "issue-5", "issue-7", "elsewhere", "orphan-on-main"]
)
REMOTE_ON_MAIN = "\n".join(["origin/HEAD", "origin/main", "origin/issue-2", "origin/issue-5"])


class FakeRunner:
    def __init__(
        self,
        dirty: frozenset[str] | set[str] = frozenset(),
        failing: frozenset[str] | set[str] = frozenset(),
    ) -> None:
        self.dirty = set(dirty)
        self.failing = set(failing)  # last argv token of a command that should fail
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str]) -> str:
        argv = list(argv)
        self.calls.append(argv)
        if argv[-1] in self.failing:
            raise CommandError(f"{' '.join(argv)} failed (1): refused")
        match argv:
            case ["gh", "pr", "list", "--state", "merged", *_]:
                return MERGED_PRS
            case ["gh", "pr", "list", "--state", "open", *_]:
                return OPEN_PRS
            case ["git", "worktree", "list", "--porcelain"]:
                return PORCELAIN
            case ["git", "for-each-ref", "refs/heads", *_]:
                return LOCAL_REFS
            case ["git", "for-each-ref", "refs/remotes/origin", *_]:
                return REMOTE_REFS
            case ["git", "branch", "--merged", "origin/main", *_]:
                return LOCAL_ON_MAIN
            case ["git", "branch", "-r", "--merged", "origin/main", *_]:
                return REMOTE_ON_MAIN
            case ["git", "-C", path, "status", "--porcelain"]:
                return "?? scratch.txt\n" if path in self.dirty else ""
            case ["git", "fetch", *_] | ["git", "worktree", "prune"]:
                return ""
            case ["git", "worktree", "remove", *_]:
                return ""
            case ["git", "branch", "-D", *_] | ["git", "push", "origin", "--delete", *_]:
                return ""
        raise AssertionError(f"unexpected command: {argv}")

    def mutations(self) -> list[list[str]]:
        return [
            c
            for c in self.calls
            if c[:3] in (["git", "worktree", "remove"], ["git", "branch", "-D"])
            or c[:4] == ["git", "push", "origin", "--delete"]
        ]


def test_parse_worktrees_reads_locked_detached_and_prunable() -> None:
    wts = {w.path.rsplit("/", 1)[-1]: w for w in parse_worktrees(PORCELAIN)}
    assert wts["issue-5"].locked and wts["issue-5"].branch == "issue-5"
    assert wts["detached"].branch is None
    assert wts["resolve-mcp"].branch == "main"
    gone = parse_worktrees(
        f"worktree {WT}/gone\nHEAD {FRESH}\nbranch refs/heads/gone\n"
        "prunable gitdir file points to non-existent location\n"
    )
    assert gone[0].prunable


def test_parse_worktrees_normalises_backslashes() -> None:
    wt = parse_worktrees("worktree C:\\repo\\.claude\\worktrees\\x\nHEAD 1\nbranch refs/heads/x\n")
    assert wt[0].path == "C:/repo/.claude/worktrees/x"


def test_plan_removes_only_merged_and_unheld() -> None:
    plan = build_plan(FakeRunner())
    assert plan.worktrees == [f"{WT}/issue-1", f"{WT}/issue-2", f"{WT}/issue-7"]
    assert plan.local_branches == ["issue-1", "issue-2", "issue-7", "orphan-on-main"]
    assert plan.remote_branches == ["issue-1", "issue-2"]
    assert plan.reasons[f"worktree {WT}/issue-1"] == "PR merged (squash) at this tip"
    assert plan.reasons["remote origin/issue-2"] == "tip is on origin/main"


def test_plan_refuses_locked_worktree_and_keeps_its_branch() -> None:
    plan = build_plan(FakeRunner())
    kept = dict(plan.skipped)
    assert kept[f"worktree {WT}/issue-5"].startswith("locked")
    # issue-5's tip is on main, but the locked worktree still checks the branch out — and the
    # lock holds the remote branch too, since the session may push again.
    assert kept["local issue-5"] == f"checked out in {WT}/issue-5"
    assert kept["remote origin/issue-5"] == "held by a locked worktree"
    assert "issue-5" not in plan.local_branches
    assert "issue-5" not in plan.remote_branches


def test_plan_refuses_worktree_with_commits_not_on_main() -> None:
    plan = build_plan(FakeRunner())
    kept = dict(plan.skipped)
    assert kept[f"worktree {WT}/issue-6"] == "commits not on origin/main"
    assert kept[f"worktree {WT}/issue-3"] == "PR merged but branch has commits after it"
    assert kept["local orphan-ahead"] == "commits not on origin/main"
    assert kept["remote origin/issue-3"] == "PR merged but branch has commits after it"
    assert kept["remote origin/issue-6"] == "commits not on origin/main"
    for name in ("issue-3", "issue-6", "issue-8"):
        assert f"{WT}/{name}" not in plan.worktrees
        assert name not in plan.local_branches
        assert name not in plan.remote_branches


def test_plan_ignores_prs_merged_into_a_branch_other_than_main() -> None:
    """A stacked PR reads MERGED on the forge while its commits never reached main."""
    plan = build_plan(FakeRunner())
    kept = dict(plan.skipped)
    assert kept[f"worktree {WT}/issue-8"] == "PR merged into a branch, not main"
    assert kept["local issue-8"] == f"checked out in {WT}/issue-8"
    assert kept["remote origin/issue-8"] == "PR merged into a branch, not main"


def test_plan_never_touches_open_pr_branches() -> None:
    plan = build_plan(FakeRunner())
    kept = dict(plan.skipped)
    assert kept[f"worktree {WT}/issue-4"] == "open PR"
    assert kept["local issue-4"] == f"checked out in {WT}/issue-4"  # its worktree stays too
    assert "issue-4" not in plan.local_branches
    assert kept["remote origin/issue-4"] == "open PR"


def test_plan_skips_detached_prunable_and_foreign_worktrees() -> None:
    plan = build_plan(FakeRunner())
    kept = dict(plan.skipped)
    assert kept[f"worktree {WT}/detached"] == "detached HEAD"
    # A worktree outside .claude/worktrees/ is never removed, and it pins its branch.
    assert kept["worktree C:/Users/x/elsewhere"] == "outside .claude/worktrees/"
    assert "C:/Users/x/elsewhere" not in plan.worktrees
    assert kept["local elsewhere"] == "checked out in C:/Users/x/elsewhere"
    assert kept["local main"] == f"checked out in {ROOT}"
    assert "main" not in plan.local_branches
    assert "main" not in plan.remote_branches


def test_plan_refuses_dirty_worktree() -> None:
    plan = build_plan(FakeRunner(dirty={f"{WT}/issue-2"}))
    assert f"{WT}/issue-2" not in plan.worktrees
    assert dict(plan.skipped)[f"worktree {WT}/issue-2"].startswith("dirty")
    # The branch stays too — the worktree still checks it out.
    assert "issue-2" not in plan.local_branches
    # The remote branch is independent of the worktree and still goes.
    assert "issue-2" in plan.remote_branches


def test_dry_run_issues_no_mutations(capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRunner()
    assert main(["--no-fetch"], run=run) == 0
    assert run.mutations() == []
    out = capsys.readouterr().out
    assert "3 worktree(s), 4 local branch(es), 2 remote branch(es) to remove" in out
    assert "dry run" in out
    assert not any(c[:2] == ["git", "fetch"] for c in run.calls)


def test_apply_removes_worktrees_then_locals_then_remotes() -> None:
    run = FakeRunner()
    assert main(["--apply"], run=run) == 0
    assert run.calls[:2] == [["git", "fetch", "--prune", "origin"], ["git", "worktree", "prune"]]
    assert run.mutations() == [
        ["git", "worktree", "remove", f"{WT}/issue-1"],
        ["git", "worktree", "remove", f"{WT}/issue-2"],
        ["git", "worktree", "remove", f"{WT}/issue-7"],
        ["git", "branch", "-D", "issue-1"],
        ["git", "branch", "-D", "issue-2"],
        ["git", "branch", "-D", "issue-7"],
        ["git", "branch", "-D", "orphan-on-main"],
        ["git", "push", "origin", "--delete", "issue-1", "issue-2"],
    ]


def test_apply_reports_a_refusal_and_keeps_going(capsys: pytest.CaptureFixture[str]) -> None:
    run = FakeRunner(failing={f"{WT}/issue-2", "issue-7"})
    assert main(["--apply", "--no-fetch"], run=run) == 1
    assert len(run.mutations()) == 8  # every removal was still attempted
    err = capsys.readouterr().err
    assert f"FAILED  git worktree remove {WT}/issue-2 failed (1): refused" in err
    assert "FAILED  git branch -D issue-7 failed (1): refused" in err


def test_a_failing_read_stops_before_anything_is_removed() -> None:
    run = FakeRunner(failing={"--porcelain"})
    assert main(["--apply", "--no-fetch"], run=run) == 1
    assert run.mutations() == []


def test_apply_batches_remote_deletes() -> None:
    run = FakeRunner()
    apply_plan(run, Plan(remote_branches=[f"b{i}" for i in range(120)]))
    pushes = [c for c in run.calls if c[:2] == ["git", "push"]]
    assert [len(c) - 4 for c in pushes] == [50, 50, 20]
