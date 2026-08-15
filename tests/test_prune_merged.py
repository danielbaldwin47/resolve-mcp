"""Fake-tier tests for scripts/prune_merged.py (#249).

The planner is pure: it takes parsed `gh pr list` and `git worktree list --porcelain`
output plus branch/sha maps and returns what to delete and what to keep (with reason).
No git or gh runs here.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import prune_merged as pm

ROOT = PurePosixPath("C:/repo")
WT = ROOT / ".claude" / "worktrees"

MERGED_JSON = """[
  {"headRefName": "issue-1", "headRefOid": "aaa1"},
  {"headRefName": "issue-2", "headRefOid": "bbb2"},
  {"headRefName": "issue-2", "headRefOid": "bbb2-old"},
  {"headRefName": "issue-3", "headRefOid": "ccc3"},
  {"headRefName": "issue-4", "headRefOid": "ddd4"},
  {"headRefName": "issue-5", "headRefOid": "eee5"},
  {"headRefName": "issue-6", "headRefOid": "fff6"}
]"""
OPEN_JSON = """[
  {"headRefName": "issue-7", "headRefOid": "0007"},
  {"headRefName": "issue-3", "headRefOid": "ccc3-reopened"}
]"""

PORCELAIN = f"""worktree {ROOT}
HEAD 1234
branch refs/heads/main

worktree {WT}/issue-1
HEAD aaa1
branch refs/heads/issue-1

worktree {WT}/issue-2
HEAD bbb2
branch refs/heads/issue-2
locked claude session issue-2 (pid 42)

worktree {WT}/issue-4
HEAD ddd4-plus
branch refs/heads/issue-4

worktree {WT}/issue-5
HEAD eee5
branch refs/heads/issue-5

worktree {WT}/issue-6
HEAD fff6
branch refs/heads/issue-6

worktree {WT}/detached
HEAD 9999
detached

worktree {ROOT}/../elsewhere
HEAD aaa1
branch refs/heads/issue-1-copy

"""


def test_parse_prs_groups_oids_by_head() -> None:
    merged = pm.parse_prs(MERGED_JSON)
    assert merged["issue-2"] == {"bbb2", "bbb2-old"}
    assert set(merged) == {f"issue-{n}" for n in range(1, 7)}


def test_parse_worktrees_reads_lock_branch_and_detached() -> None:
    wts = pm.parse_worktrees(PORCELAIN)
    by_path = {w.path.name: w for w in wts}
    assert by_path["issue-2"].locked == "claude session issue-2 (pid 42)"
    assert by_path["issue-1"].locked is None
    assert by_path["issue-1"].branch == "issue-1"
    assert by_path["detached"].branch is None
    assert wts[0].branch == "main"


def _plan(**over: object) -> pm.Plan:
    kwargs: dict[str, object] = dict(
        merged=pm.parse_prs(MERGED_JSON),
        open_heads=set(pm.parse_prs(OPEN_JSON)),
        local={
            "main": "1234",
            "issue-1": "aaa1",
            "issue-2": "bbb2",
            "issue-3": "ccc3",
            "issue-4": "ddd4-plus",
            "issue-5": "eee5",
            "issue-6": "fff6",
            "issue-7": "0007",
            "no-pr": "abcd",
        },
        local_merged={"main", "issue-1", "issue-6", "no-pr"},
        remote={
            "main": "1234",
            "issue-1": "aaa1",
            "issue-2": "bbb2",
            "issue-4": "ddd4",
            "issue-7": "0007",
            "HEAD": "1234",
        },
        remote_merged={"main", "issue-1", "HEAD"},
        worktrees=pm.parse_worktrees(PORCELAIN),
        dirty={WT / "issue-5"},
        cwd=ROOT,
    )
    kwargs.update(over)
    return pm.plan(**kwargs)  # type: ignore[arg-type]


def test_remote_branches_merged_by_pr_are_pruned() -> None:
    p = _plan()
    # issue-1: merged PR, tip == PR head. issue-4: tip == merged PR head (squash-merged,
    # so not an ancestor of main — the PR oid is what proves it).
    assert p.remote == ["issue-1", "issue-2", "issue-4"]


def test_remote_open_pr_and_no_pr_and_main_are_kept() -> None:
    p = _plan()
    kept = dict(p.kept)
    assert "origin/issue-7" in kept and "open PR" in kept["origin/issue-7"]
    assert "origin/main" not in kept and "main" not in p.remote
    assert "origin/HEAD" not in p.remote
    assert "issue-2" in p.remote  # merged, tip == PR head, not open


def test_local_branch_with_commits_past_merged_pr_is_kept() -> None:
    p = _plan()
    kept = dict(p.kept)
    # issue-4 locally sits at ddd4-plus: not the merged PR head, not on origin/main.
    assert "issue-4" not in p.local
    assert "unmerged commits" in kept["issue-4"]


def test_local_branches_pruned_only_with_a_merged_pr() -> None:
    p = _plan()
    kept = dict(p.kept)
    # no-pr is an ancestor of main but never had a PR: left alone (ticket: gh is the
    # source of truth). issue-3 has a merged PR and an open one: kept.
    assert "no-pr" not in p.local
    assert "no PR" in kept["no-pr"]
    assert "issue-3" not in p.local
    assert "open PR" in kept["issue-3"]
    assert "main" not in p.local


def test_local_branch_checked_out_in_kept_worktree_is_kept() -> None:
    p = _plan()
    kept = dict(p.kept)
    # issue-2 is merged, but its worktree is locked → worktree kept → branch kept.
    assert "issue-2" not in p.local
    assert "checked out" in kept["issue-2"]
    # Its remote branch is still fair game.
    assert "issue-2" in p.remote


def test_worktrees_locked_dirty_unmerged_detached_or_outside_are_kept() -> None:
    p = _plan()
    kept = dict(p.kept)
    removed = {w.path.name for w in p.worktrees}
    assert removed == {"issue-1", "issue-6"}
    assert "locked" in kept[str(WT / "issue-2")]
    assert "unmerged commits" in kept[str(WT / "issue-4")]
    assert "dirty" in kept[str(WT / "issue-5")]
    assert "detached" in kept[str(WT / "detached")]
    assert str(ROOT / "../elsewhere") not in kept  # outside .claude/worktrees: not ours


def test_worktree_holding_cwd_is_kept() -> None:
    p = _plan(cwd=WT / "issue-1" / "src")
    kept = dict(p.kept)
    assert "issue-1" not in {w.path.name for w in p.worktrees}
    assert "current directory" in kept[str(WT / "issue-1")]
    # And its branch stays checked out, so it is kept too.
    assert "issue-1" not in p.local
    assert "issue-1" in p.remote


def test_worktree_removed_frees_its_branch_for_deletion() -> None:
    p = _plan()
    assert "issue-1" in p.local
    assert "issue-6" in p.local


def test_render_dry_run_lists_every_action() -> None:
    text = pm.render(_plan(), apply=False)
    assert "would delete remote origin/issue-1" in text
    assert "would remove worktree" in text
    assert "kept" in text
