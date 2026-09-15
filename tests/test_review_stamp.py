"""scripts/review_stamp.py at its one seam: the Runner (#278).

Every ``gh`` and ``git`` command the stamp issues is answered from a fake PR —
the head sha, the body, and the edit that writes it back — so the whole path
verifies here without touching a real pull request. The git half reuses
``tests/test_review_gate.py``'s linear history, because the last step is the
gate's own ``check`` over the body that was written.

What no seam here covers: whether ``gh pr edit --body-file`` preserves the body
verbatim on GitHub's side. That is observed once, on this ticket's own PR.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scripts._run import CommandError, Runner
from scripts.review_gate import check, review_lines
from scripts.review_stamp import append_stamp, main, stamp_line
from tests.test_review_gate import HEAD, OLD, fake_git

SHA = HEAD[:7]
STAMP = f"Review: clean @{SHA}"


@dataclass
class FakePR:
    """A pull request as the script can see it: a head sha, a body ``gh pr edit``
    replaces, and the local HEAD ``git rev-parse`` reports."""

    body: str = "Findings: none."
    head: str = HEAD
    local: str = HEAD
    stubborn: bool = False  # the edit is accepted and dropped, as a bad token does
    edits: list[str] = field(default_factory=list)  # each body-file path, in order

    def runner(self) -> Runner:
        git = fake_git()

        def run(argv: Sequence[str]) -> str:
            if argv[0] == "git":
                return self.local + "\n" if list(argv[1:]) == ["rev-parse", "HEAD"] else git(argv)
            if list(argv[:3]) == ["gh", "pr", "view"]:
                if "headRefOid" in argv:
                    return self.head + "\n"
                if "body" in argv:
                    return self.body
            if list(argv[:3]) == ["gh", "pr", "edit"]:
                path = argv[argv.index("--body-file") + 1]
                self.edits.append(path)
                if not self.stubborn:
                    self.body = Path(path).read_text(encoding="utf-8")
                return ""
            raise AssertionError(f"unexpected command: {argv}")

        return run


def stamped(pr: FakePR, *argv: str) -> int:
    return main([*argv], run=pr.runner())


# ------------------------------------------------------------ the line it writes


def test_the_stamp_names_the_short_sha_of_the_pr_head() -> None:
    assert stamp_line(HEAD) == STAMP


def test_a_summary_follows_an_em_dash() -> None:
    assert stamp_line(HEAD, "fix diff re-checked") == f"{STAMP} — fix diff re-checked"


@pytest.mark.parametrize("summary", ["", "   ", None])
def test_no_summary_leaves_a_bare_line(summary: str | None) -> None:
    assert stamp_line(HEAD, summary) == STAMP


def test_the_stamp_goes_last_with_a_blank_line_above_it() -> None:
    assert append_stamp("Findings: none.\n", STAMP) == f"Findings: none.\n\n{STAMP}\n"


@pytest.mark.parametrize("body", ["", "\n\n", "  \n"])
def test_an_empty_body_becomes_the_line_alone(body: str) -> None:
    assert append_stamp(body, STAMP) == f"{STAMP}\n"


def test_a_crlf_body_is_written_back_with_newlines() -> None:
    assert "\r" not in append_stamp("Findings: none.\r\n", STAMP)


# ----------------------------------------------------------------- the happy path


def test_it_stamps_the_body_and_reports_the_line(capsys: pytest.CaptureFixture[str]) -> None:
    pr = FakePR()
    assert stamped(pr, "7", "--summary", "fix diff re-checked") == 0
    assert review_lines(pr.body)[-1] == f"{STAMP} — fix diff re-checked"
    assert capsys.readouterr().out.strip() == f"stamped PR 7: {STAMP} — fix diff re-checked"


def test_the_body_is_written_through_a_temp_file_that_does_not_survive() -> None:
    pr = FakePR()
    assert stamped(pr, "7") == 0
    assert len(pr.edits) == 1
    assert not Path(pr.edits[0]).exists()


def test_the_written_body_passes_the_gate() -> None:
    pr = FakePR()
    assert stamped(pr, "7", "--summary", "one finding, fixed") == 0
    assert check(pr.body, HEAD, fake_git()) is None


# ------------------------------------------------------------- the record it keeps

PRIOR = (
    "Findings: two, both fixed.\n\n"
    f"Review: clean @{OLD[:7]} - first pass\n\n"
    "Then main was merged in.\n\n"
    f"- **Review: clean @{OLD[:7]}**\n"
)


def test_earlier_review_lines_stay_above_the_new_one() -> None:
    pr = FakePR(body=PRIOR)
    assert stamped(pr, "7") == 0
    assert pr.body.startswith(PRIOR.rstrip())
    assert review_lines(pr.body) == [
        f"Review: clean @{OLD[:7]} - first pass",
        f"Review: clean @{OLD[:7]}",
        STAMP,
    ]


def test_the_stamp_is_what_the_gate_reads_even_under_an_older_line() -> None:
    pr = FakePR(body=PRIOR)
    assert check(PRIOR, HEAD, fake_git()) is not None  # red before
    assert stamped(pr, "7") == 0
    assert check(pr.body, HEAD, fake_git()) is None  # green after


# ------------------------------------------------------------------ what it refuses


def test_it_refuses_when_local_head_is_not_the_pr_head(capsys: pytest.CaptureFixture[str]) -> None:
    pr = FakePR(local=OLD)
    assert stamped(pr, "7") == 1
    assert pr.edits == []
    err = capsys.readouterr().err
    assert "not the PR head" in err and "\n" not in err.strip(), err


def test_a_refusal_leaves_the_body_alone() -> None:
    pr = FakePR(body=PRIOR, local=OLD)
    assert stamped(pr, "7") == 1
    assert pr.body == PRIOR


def test_it_refuses_when_gh_knows_no_such_pr(capsys: pytest.CaptureFixture[str]) -> None:
    pr = FakePR(head="")
    assert stamped(pr, "7") == 1
    assert pr.edits == []
    assert "no head sha" in capsys.readouterr().err


def test_a_failing_gh_command_is_reported_not_raised(capsys: pytest.CaptureFixture[str]) -> None:
    def run(argv: Sequence[str]) -> str:
        raise CommandError("gh pr view 7 failed (1): no pull requests found")

    assert main(["7"], run=run) == 1
    assert "no pull requests found" in capsys.readouterr().err


def test_a_body_the_gate_still_rejects_is_reported(capsys: pytest.CaptureFixture[str]) -> None:
    pr = FakePR(stubborn=True)  # the edit is swallowed, so the gate reads the old body
    assert stamped(pr, "7") == 1
    assert pr.edits  # it did try
    assert "the gate still fails" in capsys.readouterr().err


# ---------------------------------------------------------------------- dry run


def test_check_prints_the_line_and_edits_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    pr = FakePR()
    assert stamped(pr, "7", "--summary", "fix diff re-checked", "--check") == 0
    assert pr.edits == []
    assert pr.body == "Findings: none."
    assert capsys.readouterr().out.strip() == (
        f"would stamp PR 7: {STAMP} — fix diff re-checked"
    )
