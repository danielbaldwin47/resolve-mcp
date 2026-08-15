"""The findings module: one packaged report, one refusal, one pre-flight shape (#219).

Every validating route used to build ``{"errors": [...], "warnings": [...]}`` by hand, and
the cut and titles contracts each carried their own severity split. What is pinned here is
that the packaging is now a single function, that the refusal preamble reads the same
sentence whichever file it is refusing, and that both contracts split severity through the
one shared pre-flight shape rather than through a copy of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.document import LoadedDocument, Preflight
from resolve_mcp.errors import CutInvalidError, ResolveMcpError, TitlesInvalidError
from resolve_mcp.findings import Finding, refuse, report
from resolve_mcp.resolve import cut as cut_read
from resolve_mcp.resolve import titles as titles_read

BAD_SCHEMA = Finding("E1", None, "not a cut file", "author one")
NO_SUCH_CLIP = Finding("E4", "s001", "no clip named cam_a", "check the pool")
NO_BOUNDS = Finding("W9", "s002", "no usable bounds", "ignore or re-ingest")


def a_document() -> LoadedDocument:
    return LoadedDocument(Path("cut.json"), "abc123", {}, None)


# --- report ------------------------------------------------------------------------------------


def test_a_report_splits_the_findings_by_severity() -> None:
    packaged = report([NO_SUCH_CLIP, NO_BOUNDS])

    assert packaged["errors"] == [NO_SUCH_CLIP.as_dict()]
    assert packaged["warnings"] == [NO_BOUNDS.as_dict()]


def test_a_report_always_carries_both_keys() -> None:
    """The agent reads one shape whether the rules blocked or only remarked."""
    assert report([]) == {"errors": [], "warnings": []}
    assert report([NO_BOUNDS])["errors"] == []
    assert report([NO_SUCH_CLIP])["warnings"] == []


def test_a_report_keeps_the_order_the_findings_arrived_in() -> None:
    """Ordering is ``ordered``'s job; packaging must not quietly re-sort on top of it."""
    packaged = report([NO_SUCH_CLIP, BAD_SCHEMA])

    assert [one["rule"] for one in packaged["errors"]] == ["E4", "E1"]


# --- refuse ------------------------------------------------------------------------------------


def test_a_pass_with_no_errors_is_not_refused() -> None:
    refuse(
        [NO_BOUNDS],
        error=CutInvalidError,
        what="cut file",
        consequence="nothing was built",
        detail={"cut_file": "cut.json"},
    )


def test_a_refusal_counts_the_errors_and_names_the_consequence() -> None:
    with pytest.raises(CutInvalidError) as raised:
        refuse(
            [BAD_SCHEMA, NO_SUCH_CLIP, NO_BOUNDS],
            error=CutInvalidError,
            what="cut file",
            consequence="nothing was built",
            detail={"cut_file": "cut.json"},
        )

    assert raised.value.cause == "The cut file has 2 error(s), so nothing was built."


def test_a_refusal_carries_the_provenance_and_every_rule_that_fired() -> None:
    """A count alone cannot be acted on: the refusal is what the agent fixes the file from."""
    with pytest.raises(TitlesInvalidError) as raised:
        refuse(
            [BAD_SCHEMA, NO_BOUNDS],
            error=TitlesInvalidError,
            what="titles file",
            consequence="nothing was applied",
            detail={"titles_file": "titles.json", "content_hash": "abc123"},
        )

    detail = raised.value.payload()["detail"]
    assert detail["titles_file"] == "titles.json"
    assert detail["content_hash"] == "abc123"
    assert detail["errors"] == [BAD_SCHEMA.as_dict()]
    assert detail["warnings"] == [NO_BOUNDS.as_dict()]


def test_a_refusal_raises_the_error_its_caller_owns() -> None:
    """The rule set names the failure; the packaging never picks one for it."""

    def _refuse(error: Any) -> None:
        refuse(
            [BAD_SCHEMA],
            error=error,
            what="cut file",
            consequence="no take was swapped",
            detail={},
        )

    for error in (CutInvalidError, TitlesInvalidError):
        with pytest.raises(ResolveMcpError) as raised:
            _refuse(error)
        assert isinstance(raised.value, error)


# --- one pre-flight shape ----------------------------------------------------------------------


def test_both_contracts_pre_flight_through_the_one_shape() -> None:
    """Byte-identical severity splits in two contracts is how the two drift apart."""
    assert issubclass(cut_read.Preflight, Preflight)
    assert issubclass(titles_read.Preflight, Preflight)


def test_the_shared_shape_splits_severity_for_both_contracts() -> None:
    findings = [BAD_SCHEMA, NO_BOUNDS]
    loaded = a_document()

    for checked in (
        cut_read.Preflight(loaded, findings, []),
        titles_read.Preflight(loaded, findings),
    ):
        assert checked.errors == [BAD_SCHEMA]
        assert checked.warnings == [NO_BOUNDS]


def test_each_contract_still_carries_what_its_own_apply_needs() -> None:
    """One shared shape, not one shape: the pool reading and the events are not interchangeable."""
    assert cut_read.Preflight(a_document(), [], []).sources == []
    assert titles_read.Preflight(a_document(), []).events == []
