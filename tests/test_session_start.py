"""The SessionStart hook (``.claude/hooks/session-start.py``) at its one seam: the Runner.

Every ``git`` / ``gh`` command the hook issues is answered from a fixture keyed on argv,
so each of the report's lines is driven from a repo state that never has to exist. One
test runs the real script as a subprocess, the way the harness does, to prove it can
never fail a session start.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

import pytest

from scripts._run import CommandError
from tests.test_read_guard import hook_env

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / ".claude" / "hooks"
HOOK = HOOKS / "session-start.py"
SETTINGS = REPO / ".claude" / "settings.json"

ROOT = "C:/Users/x/repos/resolve-mcp"
WT = f"{ROOT}/.claude/worktrees"
MAIN = "a" * 40
SQUASHED_TIP = "b" * 40
UNMERGED = "d" * 40
NOW = 1_800_000_000.0


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("session_start_hook", HOOK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def porcelain(*entries: tuple[str, str, str | None]) -> str:
    """(dir name, head, branch-or-None) tuples -> ``git worktree list --porcelain``."""
    blocks = [f"worktree {ROOT}\nHEAD {MAIN}\nbranch refs/heads/main\n"]
    for name, head, branch in entries:
        ref = f"branch refs/heads/{branch}" if branch else "detached"
        blocks.append(f"worktree {WT}/{name}\nHEAD {head}\n{ref}\n")
    return "\n".join(blocks)


@dataclass
class FakeRepo:
    """One checkout's answers, keyed on the argv the hook issues."""

    branch: str = "issue-9"
    head: str = UNMERGED
    behind: int = 0
    on_main: set[str] = field(default_factory=lambda: {"main"})
    merged_prs: list[dict[str, str]] = field(default_factory=list)
    worktrees: str = porcelain()
    hooks_drift: str = ""
    dirty: set[str] = field(default_factory=set)  # worktree paths with uncommitted files
    fetch_fails: bool = False
    gh_fails: bool = False
    git_dir: str = ROOT + "/.git"
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str]) -> str:
        argv = list(argv)
        self.calls.append(argv)
        match argv:
            case ["git", "fetch", "-q", "origin"]:
                if self.fetch_fails:
                    raise CommandError("git fetch -q origin timed out after 10s")
                return ""
            case ["git", "rev-parse", "--git-common-dir"]:
                return self.git_dir + "\n"
            case ["git", "worktree", "list", "--porcelain"]:
                return self.worktrees
            case ["git", "branch", "--merged", "origin/main", *_]:
                return "\n".join(sorted(self.on_main))
            case ["gh", "pr", "list", "--state", "merged", *_]:
                if self.gh_fails:
                    raise CommandError("gh: not logged in")
                return json.dumps(self.merged_prs)
            case ["gh", "pr", "list", "--state", "open", *_]:
                return "[]"
            case ["git", "branch", "--show-current"]:
                return self.branch + "\n"
            case ["git", "rev-parse", "HEAD"]:
                return self.head + "\n"
            case ["git", "rev-list", "--count", "HEAD..origin/main"]:
                return f"{self.behind}\n"
            case ["git", "diff", "--name-only", "origin/main", "--", ":(top).claude/hooks"]:
                return self.hooks_drift
            case ["git", "-C", path, "status", "--porcelain"]:
                return "?? scratch.txt\n" if path in self.dirty else ""
        raise AssertionError(f"unexpected command: {argv}")

    def gh_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "gh"]


def lines(repo: FakeRepo, cwd: str = ROOT) -> list[str]:
    out = load_hook().report(repo, now=NOW, cwd=cwd)
    assert isinstance(out, list)
    return out


def first_word(line: str) -> str:
    return line.split(":", 1)[0]


# --- STALE ----------------------------------------------------------------------------------


def test_merged_by_ancestry_is_stale() -> None:
    out = lines(FakeRepo(branch="issue-219", head=MAIN, behind=50, on_main={"main", "issue-219"}))
    assert out == [
        "STALE: branch issue-219 is merged into origin/main (50 commits behind). "
        "Run git switch main, then git pull (two commands)."
    ]


def test_the_main_checkout_remedy_is_two_commands_not_a_chain() -> None:
    """The worktree guard refuses ``a && b``, so the remedy must never be one."""
    out = lines(FakeRepo(branch="issue-219", head=MAIN, behind=50, on_main={"main", "issue-219"}))
    assert "&&" not in out[0] and ";" not in out[0]


def test_a_linked_worktree_gets_the_leave_and_prune_remedy() -> None:
    """``git switch main`` is refused inside a linked worktree (main is checked out in the
    main checkout), so the remedy there is to leave it and prune from the main checkout."""
    repo = FakeRepo(
        branch="issue-219",
        head=MAIN,
        behind=50,
        on_main={"main", "issue-219"},
        worktrees=porcelain(("issue-219", MAIN, "issue-219")),
    )
    out = lines(repo, cwd=f"{WT}/issue-219")
    assert out == [
        "STALE: branch issue-219 is merged into origin/main (50 commits behind). "
        "This worktree's branch is merged: leave it (ExitWorktree) and run "
        "uv run python scripts/prune_merged.py --apply from the main checkout."
    ]


def test_squash_merged_at_this_tip_is_stale() -> None:
    prs = [{"headRefName": "issue-9", "headRefOid": SQUASHED_TIP, "baseRefName": "main"}]
    out = lines(FakeRepo(branch="issue-9", head=SQUASHED_TIP, behind=3, merged_prs=prs))
    assert [first_word(ln) for ln in out] == ["STALE"]
    assert "(3 commits behind)" in out[0]


def test_behind_but_unmerged_is_not_stale() -> None:
    assert lines(FakeRepo(branch="issue-9", head=UNMERGED, behind=12)) == []


def test_squash_merged_then_more_commits_is_not_stale() -> None:
    prs = [{"headRefName": "issue-9", "headRefOid": SQUASHED_TIP, "baseRefName": "main"}]
    assert lines(FakeRepo(branch="issue-9", head=UNMERGED, behind=3, merged_prs=prs)) == []


def test_fresh_branch_at_origin_main_is_silent() -> None:
    # A branch just cut from main has its tip on origin/main and nothing to pull.
    assert lines(FakeRepo(branch="issue-9", head=MAIN, behind=0, on_main={"main", "issue-9"})) == []


@pytest.mark.parametrize("branch", ["main", ""])
def test_main_and_detached_head_are_never_stale(branch: str) -> None:
    assert lines(FakeRepo(branch=branch, head=MAIN, behind=4, on_main={"main"})) == []


def test_stale_is_the_first_line_when_everything_fires() -> None:
    repo = FakeRepo(
        branch="issue-219",
        head=MAIN,
        behind=50,
        on_main={"main", "issue-219", "issue-1"},
        worktrees=porcelain(("issue-1", MAIN, "issue-1")),
        hooks_drift=".claude/hooks/context-guard.py\n",
        fetch_fails=True,
    )
    assert [first_word(ln) for ln in lines(repo)] == ["STALE", "RESIDUE", "HOOKS", "FETCH"]


# --- fetch fallback -------------------------------------------------------------------------


def test_fetch_timeout_falls_back_and_names_the_last_fetch_age(tmp_path: Path) -> None:
    fetch_head = tmp_path / "FETCH_HEAD"
    fetch_head.write_text("", encoding="utf-8")
    os.utime(fetch_head, (NOW - 3 * 3600 - 5, NOW - 3 * 3600 - 5))
    repo = FakeRepo(fetch_fails=True, git_dir=str(tmp_path))
    out = lines(repo)
    assert out == [
        "FETCH: git fetch origin failed or timed out; reporting from the last fetch, 3h ago."
    ]
    # The rest of the report still ran on what the last fetch left behind.
    assert ["git", "worktree", "list", "--porcelain"] in repo.calls


def test_fetch_timeout_with_no_earlier_fetch_says_so(tmp_path: Path) -> None:
    out = lines(FakeRepo(fetch_fails=True, git_dir=str(tmp_path)))
    assert out == [
        "FETCH: git fetch origin failed or timed out; no earlier fetch on record."
    ]


def test_a_successful_fetch_prints_no_fetch_line() -> None:
    assert lines(FakeRepo()) == []


def test_gh_unavailable_still_decides_by_ancestry() -> None:
    repo = FakeRepo(branch="issue-219", head=MAIN, behind=2, on_main={"main", "issue-219"})
    repo.gh_fails = True
    assert [first_word(ln) for ln in lines(repo)] == ["STALE"]


# --- gh is asked only when it could change a verdict -----------------------------------------


def test_main_with_no_worktrees_never_calls_gh() -> None:
    """Two ``gh pr list --limit 1000`` calls cost ~70 s on this box; on a bare ``main``
    checkout there is no branch a squash-merge fact could apply to."""
    repo = FakeRepo(branch="main", head=MAIN, worktrees=porcelain())
    assert lines(repo) == []
    assert repo.gh_calls() == []


def test_a_locked_or_foreign_worktree_alone_does_not_call_gh() -> None:
    locked = f"worktree {WT}/issue-5\nHEAD {MAIN}\nbranch refs/heads/issue-5\nlocked\n"
    outside = f"worktree C:/Users/x/elsewhere\nHEAD {MAIN}\nbranch refs/heads/elsewhere\n"
    repo = FakeRepo(branch="main", head=MAIN, worktrees=f"{porcelain()}\n{locked}\n{outside}")
    assert lines(repo) == []
    assert repo.gh_calls() == []


@pytest.mark.parametrize(
    "repo",
    [
        FakeRepo(branch="issue-9"),  # the session's own branch
        FakeRepo(branch="main", worktrees=porcelain(("issue-1", UNMERGED, "issue-1"))),
    ],
)
def test_a_branch_that_could_be_squash_merged_asks_gh(repo: FakeRepo) -> None:
    lines(repo)
    assert [c[:5] for c in repo.gh_calls()] == [
        ["gh", "pr", "list", "--state", "merged"],
        ["gh", "pr", "list", "--state", "open"],
    ]


# --- RESIDUE --------------------------------------------------------------------------------


def test_no_residue_prints_nothing() -> None:
    repo = FakeRepo(
        on_main={"main"},
        worktrees=porcelain(("issue-6", UNMERGED, "issue-6"), ("detached", MAIN, None)),
    )
    assert lines(repo) == []


def residue_repo(on_main: set[str] | None = None, dirty: set[str] | None = None) -> FakeRepo:
    prs = [{"headRefName": "issue-1", "headRefOid": SQUASHED_TIP, "baseRefName": "main"}]
    agent_work = "3" * 40  # a commit only that agent's branch has
    return FakeRepo(
        on_main={"main", "issue-2", "worktree-agent-abc"} if on_main is None else on_main,
        dirty=dirty or set(),
        merged_prs=prs,
        worktrees=porcelain(
            ("issue-1", SQUASHED_TIP, "issue-1"),  # squash-merged at this tip
            ("issue-2", MAIN, "issue-2"),  # tip on origin/main
            ("issue-6", UNMERGED, "issue-6"),  # real work, kept
            ("worktree-agent-abc", MAIN, "worktree-agent-abc"),  # cut from main, no commits
            ("worktree-agent-def", UNMERGED, "worktree-agent-def"),  # cut from issue-6: kept
            ("worktree-agent-ghi", agent_work, "worktree-agent-ghi"),  # an agent that committed
        ),
    )


RESIDUE = (
    "RESIDUE: 2 worktrees on merged branches, 1 worktree-agent-* with no commits. "
    "uv run python scripts/prune_merged.py --apply"
)


def test_residue_counts_merged_worktrees_and_empty_agent_worktrees() -> None:
    assert lines(residue_repo()) == [RESIDUE]


def test_an_agent_worktree_cut_from_unmerged_work_is_not_residue() -> None:
    """Review 2026-09-15, finding 1: worktree-agent-def's tip is issue-6's, which main
    lacks - another session's work, not an empty checkout. It is never counted, and the
    hook asks git nothing per branch to know that."""
    repo = residue_repo()
    assert lines(repo) == [RESIDUE]
    assert not any(c[:3] == ["git", "branch", "--contains"] for c in repo.calls)
    assert lines(residue_repo(on_main={"main", "issue-2"})) == [
        "RESIDUE: 2 worktrees on merged branches, 0 worktree-agent-* with no commits. "
        "uv run python scripts/prune_merged.py --apply"
    ]


def test_the_sessions_own_worktree_is_never_residue() -> None:
    """Review 2026-09-15, finding 2: the worktree the session runs in is where the sweep
    would be run from, not something it removes."""
    assert lines(residue_repo(), cwd=f"{WT}/issue-1") == [
        "RESIDUE: 1 worktrees on merged branches, 1 worktree-agent-* with no commits. "
        "uv run python scripts/prune_merged.py --apply"
    ]
    assert lines(residue_repo(), cwd=f"{WT}/worktree-agent-abc/") == [
        "RESIDUE: 2 worktrees on merged branches, 0 worktree-agent-* with no commits. "
        "uv run python scripts/prune_merged.py --apply"
    ]


def test_a_dirty_candidate_is_skipped_and_counted() -> None:
    """The count matches what ``--apply`` removes: the sweep refuses a dirty worktree."""
    repo = residue_repo(dirty={f"{WT}/issue-2", f"{WT}/worktree-agent-abc"})
    assert lines(repo) == [
        "RESIDUE: 1 worktrees on merged branches, 0 worktree-agent-* with no commits, "
        "2 dirty skipped. uv run python scripts/prune_merged.py --apply"
    ]
    # Only the candidates were asked; issue-6 (kept) and the session's cwd cost no status.
    asked = {c[2] for c in repo.calls if c[:2] == ["git", "-C"]}
    assert asked == {f"{WT}/issue-1", f"{WT}/issue-2", f"{WT}/worktree-agent-abc"}


def test_all_candidates_dirty_prints_nothing() -> None:
    dirty = {f"{WT}/issue-1", f"{WT}/issue-2", f"{WT}/worktree-agent-abc"}
    assert lines(residue_repo(dirty=dirty)) == []


def test_residue_skips_locked_worktrees_and_ones_outside_the_worktree_dir() -> None:
    locked = f"worktree {WT}/issue-5\nHEAD {MAIN}\nbranch refs/heads/issue-5\nlocked\n"
    outside = f"worktree C:/Users/x/elsewhere\nHEAD {MAIN}\nbranch refs/heads/elsewhere\n"
    repo = FakeRepo(
        on_main={"main", "issue-5", "elsewhere"},
        worktrees=porcelain() + "\n" + locked + "\n" + outside,
    )
    assert lines(repo) == []


# --- HOOKS ----------------------------------------------------------------------------------


def test_hooks_drift_is_reported() -> None:
    out = lines(FakeRepo(hooks_drift=".claude/hooks/read-guard.py\n"))
    assert out == [
        "HOOKS: .claude/hooks differs from origin/main - this checkout enforces old rules."
    ]


# --- one source of truth --------------------------------------------------------------------


def test_merge_decision_is_defined_once_and_shared() -> None:
    shared = REPO / "scripts" / "_merged.py"
    assert shared.exists()
    for path in (HOOK, REPO / "scripts" / "prune_merged.py"):
        src = path.read_text(encoding="utf-8")
        assert re.search(r"^from scripts\._merged import", src, re.M), path.name
        for name in ("merge_decision", "worktree_decision", "agent_without_commits"):
            assert not re.search(rf"^def {name}\b", src, re.M), f"{path.name} redefines {name}"
    shared_src = shared.read_text(encoding="utf-8")
    for name in ("merge_decision", "worktree_decision", "agent_without_commits"):
        assert re.search(rf"^def {name}\(", shared_src, re.M), name


def test_hook_is_stdlib_only() -> None:
    src = HOOK.read_text(encoding="utf-8")
    imports = re.findall(r"^(?:from|import) (\S+)", src, re.M)
    allowed = {"dataclasses", "json", "os", "subprocess", "sys", "time", "pathlib"}
    third_party = {m for m in imports if not m.startswith("scripts.") and m not in allowed}
    assert not third_party, third_party


def test_the_dirtiness_gate_is_the_sweeps_own() -> None:
    """One ``dirty`` in ``scripts/_merged.py``; neither caller spells the status call."""
    for path in (HOOK, REPO / "scripts" / "prune_merged.py"):
        src = path.read_text(encoding="utf-8")
        assert '"status"' not in src, path.name
        assert re.search(r"^\s+dirty,$", src, re.M), path.name


# --- plumbing -------------------------------------------------------------------------------


def test_settings_register_the_hook_under_session_start() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    entries = settings["hooks"]["SessionStart"]
    commands = [h["command"] for e in entries for h in e["hooks"] if h["type"] == "command"]
    assert any(c.endswith("/.claude/hooks/session-start.py") for c in commands), commands


def test_hook_never_fails_the_session_start(tmp_path: Path) -> None:
    # PATH is empty: git does not resolve, which is the worst case a session start can meet.
    payload = {"session_id": "s1", "hook_event_name": "SessionStart", "cwd": str(tmp_path)}
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=hook_env(tmp_path, tmp_path),
    )
    assert r.returncode == 0
    assert r.stdout == ""
    assert r.stderr.count("\n") == 1 and r.stderr.startswith("session-start: skipped (")


def test_hook_accepts_an_empty_stdin(tmp_path: Path) -> None:
    r = subprocess.run(
        [sys.executable, str(HOOK)],
        input="",
        capture_output=True,
        text=True,
        env=hook_env(tmp_path, tmp_path),
        cwd=tmp_path,
    )
    assert r.returncode == 0
