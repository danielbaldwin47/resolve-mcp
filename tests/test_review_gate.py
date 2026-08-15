"""scripts/review_gate.py at its one seam: the Runner (#251).

Every ``git`` command the check issues is answered from a fake linear history —
the gate's verdicts are decisions about a PR body, and decisions verify at the
fake tier. What no seam here covers is GitHub's half of it (the checkout ref,
the event types the workflow fires on); that is verified once with a scratch PR
and recorded on the ticket.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from scripts.review_gate import CommandError, Runner, check, main, review_lines

OLD = "1" * 40
MID = "2" * 40
HEAD = "3" * 40
HISTORY = [OLD, MID, HEAD]  # oldest first
FOREIGN = "9" * 40  # a commit off this PR's history entirely


def fake_git(history: Sequence[str] = tuple(HISTORY), extra: Sequence[str] = (FOREIGN,)) -> Runner:
    """A Runner over a linear history: ``rev-parse`` resolves a short sha only
    for a commit the repository has, ``rev-list --count a..b`` counts what *b*
    carries that *a* does not."""
    known = {c[:7]: c for c in [*history, *extra]}

    def count(a: str, b: str) -> int:
        if b not in history:
            return 0 if a == b else 1
        if a not in history:
            return history.index(b) + 1
        return max(0, history.index(b) - history.index(a))

    def run(argv: Sequence[str]) -> str:
        if argv[1] == "rev-parse":
            ref = argv[-1].removesuffix("^{commit}")
            full = known.get(ref[:7])
            if full is None or not full.startswith(ref):
                raise CommandError(f"{' '.join(argv)}: unknown revision")
            return full + "\n"
        if argv[1] == "rev-list":
            a, b = argv[-1].split("..")
            return f"{count(a, b)}\n"
        raise AssertionError(f"unexpected command: {argv}")

    return run


def verdict(body: str, head: str = HEAD) -> str:
    """The failure message, or '' when the body passes the gate."""
    return check(body, head, fake_git()) or ""


SHA = HEAD[:7]


# ------------------------------------------------------- the sha names the head

PASSING = [
    f"Review: clean @{SHA}",
    f"Review: clean @{HEAD}",  # a full sha is a sha
    f"Review: clean @{SHA} — two findings, both fixed in 1a2b3c4",
    f"Findings: none.\n\nReview: clean @{SHA}\n",
    f"Review: clean @{SHA}\r\n",  # CRLF, as GitHub stores it
]


@pytest.mark.parametrize("body", PASSING)
def test_a_clean_line_naming_the_head_passes(body: str) -> None:
    assert verdict(body) == "", body


MARKUP = [
    f"**Review: clean @{SHA}**",
    f"- Review: clean @{SHA}",
    f"- **Review: clean @{SHA}**",
    f"* __Review: clean @{SHA}__",
    f"1. Review: clean @{SHA}",
    f"> Review: clean @{SHA}",
    f"  Review: clean @{SHA}  ",
]


@pytest.mark.parametrize("body", MARKUP)
def test_leading_list_quote_and_bold_markup_is_tolerated(body: str) -> None:
    assert verdict(body) == "", body


# ------------------------------------------------------------ the sha is stale

def test_a_commit_landed_after_the_reviewed_sha_is_red() -> None:
    msg = verdict(f"Review: clean @{MID[:7]}")
    assert "1 commit(s) landed after" in msg, msg
    assert f"@{SHA}" in msg, msg  # the message names the line to append


def test_two_commits_behind_counts_them() -> None:
    assert "2 commit(s) landed after" in verdict(f"Review: clean @{OLD[:7]}")


def test_a_sha_off_this_prs_history_is_red() -> None:
    assert "not reachable from the PR head" in verdict(f"Review: clean @{FOREIGN[:7]}")


def test_a_sha_the_repository_does_not_have_is_red() -> None:
    assert "does not have" in verdict("Review: clean @abcdef0")


# ------------------------------------------------------------- the line itself

def test_no_review_line_is_red() -> None:
    assert "no 'Review:' line" in verdict("Findings: none. Looks good.")


def test_clean_without_a_sha_names_the_new_convention() -> None:
    msg = verdict("Review: clean")
    assert "with no commit" in msg and "Review: clean @<sha>" in msg, msg


def test_clean_with_a_summary_but_no_sha_is_red() -> None:
    assert "with no commit" in verdict("Review: clean — one finding, fixed")


def test_findings_held_keeps_its_own_message() -> None:
    assert "contentless token" in verdict("Review: findings held")


def test_any_other_review_line_is_red() -> None:
    msg = verdict("Review: two findings open")
    assert "not 'Review: clean @<sha>'" in msg, msg


def test_the_last_review_line_decides() -> None:
    assert verdict(f"Review: findings held\n\nReview: clean @{SHA}") == ""
    assert "contentless token" in verdict(f"Review: clean @{SHA}\n\nReview: findings held")


# ----------------------------------------------------------------- code fences

FENCED_ONLY = [
    f"See the convention:\n\n```\nReview: clean @{SHA}\n```\n",
    f"~~~\nReview: clean @{SHA}\n~~~\n",
    f"```markdown\nReview: clean @{SHA}\n```\n",
    f"  ```\n  Review: clean @{SHA}\n  ```\n",
]


@pytest.mark.parametrize("body", FENCED_ONLY)
def test_a_review_line_inside_a_fence_is_not_the_review_line(body: str) -> None:
    assert "no 'Review:' line" in verdict(body), body


def test_a_fenced_example_does_not_override_the_real_line() -> None:
    body = f"```\nReview: clean @{SHA}\n```\n\nReview: findings held\n"
    assert "contentless token" in verdict(body)


def test_a_fenced_example_after_the_real_line_does_not_count() -> None:
    body = f"Review: clean @{SHA}\n\n```\nReview: findings held\n```\n"
    assert verdict(body) == ""


def test_review_lines_returns_the_lines_it_found_normalised() -> None:
    body = f"- **Review: clean @{SHA}**\n```\nReview: findings held\n```\n"
    assert review_lines(body) == [f"Review: clean @{SHA}"]


# ------------------------------------------------------------------- the entry

def test_main_prints_the_line_and_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(f"Review: clean @{SHA}", HEAD, fake_git()) == 0
    assert capsys.readouterr().out.strip() == f"gate: Review: clean @{SHA}"


def test_main_prints_a_workflow_error_and_exits_one(capsys: pytest.CaptureFixture[str]) -> None:
    assert main("Review: clean", HEAD, fake_git()) == 1
    out = capsys.readouterr().out
    assert out.startswith("::error::") and "\n" not in out.strip(), out


def test_an_empty_body_is_red() -> None:
    assert main("", HEAD, fake_git()) == 1
