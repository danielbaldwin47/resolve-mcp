"""The Windows sharing retry, on its own — the rule two very different files depend on.

A job record and a claim file are the same problem: another handle holds the file for the
microsecond of a poll, and Windows refuses the read or the replace that lands in it. Both
reach for this, which is why it is neither of theirs.
"""

from __future__ import annotations

import pytest

from resolve_mcp.sharing import sharing


def test_a_write_that_loses_the_race_with_a_reader_is_tried_again() -> None:
    """The rule the retry encodes, testable off Windows where the race cannot happen.

    A reader holding the file is a refusal to wait out, not a failure to report: giving up
    would kill the worker thread mid-save and leave the record saying running forever.
    """
    attempts: list[int] = []

    def flaky() -> str:
        attempts.append(len(attempts))
        if len(attempts) < 3:
            raise PermissionError(32, "The process cannot access the file")
        return "written"

    assert sharing(flaky) == "written"
    assert len(attempts) == 3


def test_a_reader_that_never_lets_go_is_still_an_error() -> None:
    """Retrying forever would hide a genuinely locked cache directory."""

    def locked() -> str:
        raise PermissionError(32, "The process cannot access the file")

    with pytest.raises(PermissionError):
        sharing(locked)
