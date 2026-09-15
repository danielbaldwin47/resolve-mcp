"""Stamp a PR body with the review line the gate reads (#278).

Run from the repo root, on the branch you just re-checked::

    uv run python scripts/review_stamp.py <pr> --summary "fix diff re-checked"

Why a script (2026-09-15 retro, point 8): across eight PRs the review gate was
the only CI failure, three times, every one of them a body re-stamped by hand
after a focused re-check. The hand edit gets the sha wrong, or lands under an
earlier ``Review:`` line, or never happens. This does the same edit from the
facts: the PR head comes from ``gh``, the line goes last, and the written body
is read back and run through ``scripts.review_gate.check`` — the gate's own
verdict, not a second copy of its rules.

It refuses when the working tree's HEAD is not the PR head: the re-check must
have looked at what it stamps, and a stamp naming a commit you did not review
is exactly the hole the sha closed.

Everything it learns comes through one ``Runner`` (a callable from argv to
stdout), so the fake tier drives every path on fixtures of the ``gh`` / ``git``
output (``tests/test_review_stamp.py``) and no test touches a real PR.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):  # run by path, not as ``-m scripts.review_stamp``
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._run import CommandError, Runner, subprocess_runner  # noqa: E402
from scripts.review_gate import check, review_lines  # noqa: E402

SHORT = 7  # the sha length `git rev-parse --short` gives here; the gate takes 7-40


def stamp_line(head: str, summary: str | None = None) -> str:
    """The line the gate wants: ``Review: clean @<short sha>``, plus the summary
    after an em dash when there is one."""
    line = f"Review: clean @{head[:SHORT]}"
    return f"{line} — {summary.strip()}" if summary and summary.strip() else line


def append_stamp(body: str, line: str) -> str:
    """*body* with *line* as its last line, every earlier line kept as it was
    (earlier ``Review:`` lines included — they are the record)."""
    text = body.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    return f"{text}\n\n{line}\n" if text else f"{line}\n"


def pr_head(pr: str, run: Runner) -> str:
    """The commit the PR would merge, as ``gh`` reports it."""
    return run(["gh", "pr", "view", pr, "--json", "headRefOid", "-q", ".headRefOid"]).strip()


def pr_body(pr: str, run: Runner) -> str:
    """The PR body as ``gh`` stores it."""
    return run(["gh", "pr", "view", pr, "--json", "body", "-q", ".body"])


def head_failure(head: str, run: Runner) -> str | None:
    """Why the working tree is not standing on the PR head, or None when it is."""
    local = run(["git", "rev-parse", "HEAD"]).strip()
    if local == head:
        return None
    return (
        f"local HEAD {local[:SHORT]} is not the PR head {head[:SHORT]} - check out the branch you "
        "reviewed (and push it) before stamping; the sha is what proves the review saw what merges."
    )


def write_body(pr: str, body: str, run: Runner) -> None:
    """``gh pr edit --body-file`` over a temp file, so a body of any length and
    any markdown survives the shell."""
    directory = tempfile.mkdtemp(prefix="review-stamp-")
    path = Path(directory) / "body.md"
    try:
        path.write_text(body, encoding="utf-8", newline="\n")
        run(["gh", "pr", "edit", pr, "--body-file", str(path)])
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def stamp(pr: str, summary: str | None, run: Runner, *, dry_run: bool = False) -> tuple[int, str]:
    """Stamp PR *pr*; the exit code and the line to print."""
    head = pr_head(pr, run)
    if not head:
        return 1, f"gh gave no head sha for PR {pr} - is {pr} a pull request on this repository?"
    failure = head_failure(head, run)
    if failure is not None:
        return 1, failure
    line = stamp_line(head, summary)
    body = append_stamp(pr_body(pr, run), line)
    if dry_run:
        return 0, f"would stamp PR {pr}: {line}"
    write_body(pr, body, run)
    written = pr_body(pr, run)  # what GitHub now has, which is what the gate will read
    gate = check(written, head, run)
    if gate is not None:
        return 1, f"stamped PR {pr}, but the gate still fails: {gate}"
    return 0, f"stamped PR {pr}: {review_lines(written)[-1]}"


def main(argv: Sequence[str] | None = None, run: Runner = subprocess_runner) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("pr", help="the PR number to stamp")
    ap.add_argument("--summary", help="what the review found, appended after an em dash")
    ap.add_argument("--check", action="store_true", help="print the line; edit nothing")
    args = ap.parse_args(argv)
    try:
        code, message = stamp(args.pr, args.summary, run, dry_run=args.check)
    except CommandError as e:
        print(e, file=sys.stderr)
        return 1
    print(message, file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())
