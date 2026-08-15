"""The review gate's body check: the PR body's last ``Review:`` line must read
``Review: clean @<sha>`` and name the commit the PR is about to merge.

Run by ``.github/workflows/review-gate.yml`` in a checkout of the PR head::

    BODY=... HEAD_SHA=... python3 -m scripts.review_gate

Why the sha (2026-08-15 workflow audit, #251): the old gate matched the literal
``Review: clean`` and re-ran on ``synchronize`` with an unchanged body, so a PR
that gained commits after its review passed the gate green — the habit of
re-reviewing was followed 35/35 times, but the gate could not see it. A sha
turns the convention into something checkable: the reviewed commit must *be*
the PR head, so any commit pushed after the review reddens the gate until a
fresh ``Review:`` line lands.

The same audit found two shape holes, both closed here: ``Review: clean`` at
column 0 **inside a fenced example** passed (fenced blocks are now stripped
before the search), and ``**Review: clean**`` / ``- Review: clean`` blocked
(leading list, quote and bold markup is now tolerated).

Everything the check learns about the repository comes through one ``Runner``
(a callable from argv to stdout), so the fake tier drives every verdict on
fixtures of the ``git`` output (``tests/test_review_gate.py``).
"""

from __future__ import annotations

import os
import re
import sys

from scripts._run import CommandError, Runner, subprocess_runner

# A fence opens or closes on ``` / ~~~ at any indent; the info string of an
# opening fence is ignored. Toggling is enough: an unclosed fence swallows the
# rest of the body, which is the safe direction (the line is not found).
FENCE = re.compile(r"^[ \t]*(?:```|~~~)")
# Markdown that may lead the line: list bullets, ordered items, block quotes.
LEADING_MARKUP = re.compile(r"^(?:[-*+]\s+|\d+[.)]\s+|>\s*)+")
# ...and the emphasis that may wrap it: **bold**, __bold__, *italic*, _italic_.
EMPHASIS_OPEN = re.compile(r"^(?:\*{1,2}|_{1,2})")
EMPHASIS_CLOSE = re.compile(r"(?:\*{1,2}|_{1,2})$")
REVIEW = re.compile(r"^Review:")
CLEAN = re.compile(r"^Review:\s+clean\s+@(?P<sha>[0-9a-fA-F]{7,40})\b.*$")  # a summary may follow
CLEAN_NO_SHA = re.compile(r"^Review:\s+clean\b")
HELD = re.compile(r"^Review:\s+findings\s+held\b", re.I)


def normalise(line: str) -> str:
    """Strip the markdown a ``Review:`` line may be dressed in, leaving the line
    itself: ``- **Review: clean @abc1234**`` -> ``Review: clean @abc1234``."""
    text = line.strip()
    text = LEADING_MARKUP.sub("", text)
    text = EMPHASIS_OPEN.sub("", text)
    text = EMPHASIS_CLOSE.sub("", text)
    return text.strip()


def review_lines(body: str) -> list[str]:
    """Every normalised ``Review:`` line outside a fenced code block, in order."""
    found = []
    fenced = False
    for raw in body.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if FENCE.match(raw):
            fenced = not fenced
            continue
        if fenced:
            continue
        line = normalise(raw)
        if REVIEW.match(line):
            found.append(line)
    return found


def sha_failure(sha: str, head: str, run: Runner) -> str | None:
    """Why *sha* does not stand for the PR head — unknown to the repository, off
    this PR's history, or overtaken by commits — or None when it is the head."""
    try:
        full = run(["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"]).strip()
    except CommandError:
        full = ""
    if not full:
        return (
            f"Review line names @{sha}, a commit this repository does not have — "
            "use the short sha of the commit you reviewed (git rev-parse --short HEAD)."
        )
    if full == head:
        return None
    if int(run(["git", "rev-list", "--count", f"{head}..{full}"]).strip() or 0):
        return (
            f"Review line names @{sha}, which is not reachable from the PR head {head[:12]} — "
            "review the branch as it stands and name its tip."
        )
    ahead = run(["git", "rev-list", "--count", f"{full}..{head}"]).strip()
    return (
        f"{ahead} commit(s) landed after the reviewed commit @{sha} — re-review the new diff "
        f"and append a fresh 'Review: clean @{head[:7]}' line (the earlier lines stay above it)."
    )


def check(body: str, head: str, run: Runner) -> str | None:
    """The gate's verdict on a PR body: a failure message, or None to pass."""
    lines = review_lines(body or "")
    if not lines:
        return (
            "PR body has no 'Review:' line — run /code-review (two-axis), then append "
            "'Review: clean @<sha>' naming the commit you reviewed, or 'Review:' plus the "
            "findings themselves."
        )
    line = lines[-1]
    if HELD.match(line):
        return (
            "'findings held' is a contentless token — write the findings into the body, "
            "resolve them, then set 'Review: clean @<sha>'."
        )
    match = CLEAN.match(line)
    if not match:
        if CLEAN_NO_SHA.match(line):
            return (
                f"Review line reads '{line}' with no commit — the gate proves the review "
                "covered what merges: 'Review: clean @<sha>', the short sha of the reviewed "
                "commit (git rev-parse --short HEAD)."
            )
        return (
            f"Review line reads '{line}', not 'Review: clean @<sha>' — "
            "resolve what it names before merging."
        )
    return sha_failure(match.group("sha"), head, run)


def main(body: str | None = None, head: str | None = None, run: Runner = subprocess_runner) -> int:
    body = os.environ.get("BODY", "") if body is None else body
    head = os.environ.get("HEAD_SHA", "") if head is None else head
    failure = check(body, head, run)
    if failure is None:
        print(f"gate: {review_lines(body)[-1]}")
        return 0
    print(f"::error::{failure}")
    return 1


if __name__ == "__main__":  # pragma: no cover - the workflow's entry point
    sys.exit(main())
