"""Pure-function tests for the cut-file validation rules E1-E10, E12 and the warnings.

No Resolve, no fakes: the structural pass takes a parsed document, the project
pass takes gathered clip facts. Both are plain functions over plain data.

Where the entries land is ``test_cut_layout.py``'s, and E11 — the build-time rule, whose
condition is a live locked track — is ``test_build_timeline.py``'s (#218).
"""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest

from resolve_mcp.cut.validate import (
    ClipFacts,
    validate_project,
    validate_structure,
)
from resolve_mcp.findings import Finding


def valid_doc() -> dict[str, Any]:
    """A cut file that passes every rule."""
    return {
        "schema": 1,
        "timeline": {"name": "sunset-set", "fps": 59.94, "bin": "Cuts"},
        "sources": {
            "gtr_close": {"clip": "C0012.mp4", "bin": "Angles", "sync_offset": 1432},
            "keys_wide": {"clip": "C0013.mp4", "bin": "Angles"},
            "broll_pan": {"clip": "C0014.mp4"},
            "master_mix": {"clip": "sunset-master.wav"},
        },
        "audio": {"source": "master_mix", "in": 0, "out": 400},
        "segments": [
            {
                "id": "s001",
                "source": "gtr_close",
                "in": 1000,
                "out": 1200,
                "alternates": [{"source": "keys_wide", "in": 8100, "out": 8300}],
                "note": "drum fill response",
            },
            {"id": "s002", "source": "keys_wide", "in": 2000, "out": 2200},
        ],
        "overlays": [
            {
                "id": "b03",
                "source": "broll_pan",
                "in": 1200,
                "out": 1300,
                "over": {"segment": "s001", "offset": 24},
            }
        ],
    }


def clip_facts() -> list[ClipFacts]:
    """Media-pool facts matching :func:`valid_doc`."""
    return [
        ClipFacts(
            name="C0012.mp4", bin_path="Angles", start=0, end_exclusive=20000, fps=59.94
        ),
        ClipFacts(
            name="C0013.mp4", bin_path="Angles", start=0, end_exclusive=20000, fps=59.94
        ),
        ClipFacts(name="C0014.mp4", bin_path="Cuts", start=0, end_exclusive=20000, fps=59.94),
        ClipFacts(
            name="sunset-master.wav",
            bin_path=None,
            start=0,
            end_exclusive=400,
            fps=None,
            has_audio=True,
            is_still=False,
        ),
    ]


def rules(findings: list[Finding]) -> list[str]:
    return [f.rule for f in findings]


def only(findings: list[Finding], rule: str) -> Finding:
    """The single finding for ``rule`` — asserts exactly one fired."""
    matches = [f for f in findings if f.rule == rule]
    assert len(matches) == 1, f"expected exactly one {rule}, got {rules(findings)}"
    return matches[0]


# --- the clean case ---------------------------------------------------------------------


def test_valid_document_passes_clean() -> None:
    assert validate_structure(valid_doc()) == []


def test_valid_document_passes_the_project_pass_clean() -> None:
    assert validate_project(valid_doc(), clip_facts()) == []


# --- E1: parses, schema-valid, version supported ----------------------------------------


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d.pop("schema"), id="missing-schema"),
        pytest.param(lambda d: d.update(schema=2), id="unsupported-version"),
        pytest.param(lambda d: d.update(schema="1"), id="version-not-an-int"),
        pytest.param(lambda d: d.pop("timeline"), id="missing-timeline"),
        pytest.param(lambda d: d["timeline"].pop("name"), id="missing-timeline-name"),
        pytest.param(lambda d: d["timeline"].update(fps=0), id="fps-not-positive"),
        pytest.param(lambda d: d.pop("sources"), id="missing-sources"),
        pytest.param(lambda d: d["sources"].update(gtr_close={}), id="source-without-clip"),
        pytest.param(lambda d: d.update(segments=[]), id="no-segments"),
        pytest.param(lambda d: d["segments"][0].pop("id"), id="segment-without-id"),
        pytest.param(lambda d: d["segments"][0].update(**{"in": 1000.5}), id="in-not-an-int"),
        pytest.param(lambda d: d["overlays"][0].pop("over"), id="overlay-without-anchor"),
        pytest.param(lambda d: d["segments"][0].update(audio="yes"), id="audio-flag-not-bool"),
    ],
)
def test_e1_rejects_structurally_invalid_documents(mutate: Any) -> None:
    doc = valid_doc()
    mutate(doc)

    findings = validate_structure(doc)

    assert "E1" in rules(findings)


def test_e1_fires_when_the_document_is_not_an_object() -> None:
    assert "E1" in rules(validate_structure([1, 2, 3]))


def test_e1_stops_before_later_rules_run() -> None:
    """A malformed document cannot be meaningfully checked for E2-E10."""
    doc = valid_doc()
    doc["segments"] = "not a list"

    findings = validate_structure(doc)

    assert set(rules(findings)) == {"E1"}


# --- E2: ids unique across segments and overlays ----------------------------------------


def test_e2_catches_duplicate_segment_ids() -> None:
    doc = valid_doc()
    doc["segments"][1]["id"] = "s001"

    finding = only(validate_structure(doc), "E2")

    assert finding.id == "s001"


def test_e2_shares_one_namespace_with_overlays() -> None:
    doc = valid_doc()
    doc["overlays"][0]["id"] = "s002"

    finding = only(validate_structure(doc), "E2")

    assert finding.id == "s002"


# --- E3: in < out everywhere ------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected_id"),
    [
        pytest.param(("segments", 0), "s001", id="segment"),
        pytest.param(("overlays", 0), "b03", id="overlay"),
    ],
)
def test_e3_catches_non_positive_ranges(path: Any, expected_id: str) -> None:
    doc = valid_doc()
    target = doc[path[0]][path[1]]
    target["out"] = target["in"]

    finding = only(validate_structure(doc), "E3")

    assert finding.id == expected_id


def test_e3_covers_alternates() -> None:
    doc = valid_doc()
    doc["segments"][0]["alternates"][0]["out"] = 8000

    findings = validate_structure(doc)

    assert "E3" in rules(findings)


def test_e3_covers_the_audio_block() -> None:
    doc = valid_doc()
    doc["audio"]["out"] = 0

    findings = validate_structure(doc)

    assert "E3" in rules(findings)


# --- E4: aliases resolve to exactly one media-pool clip ---------------------------------


def test_e4_catches_an_alias_that_is_not_declared_in_sources() -> None:
    doc = valid_doc()
    doc["segments"][1]["source"] = "nope"

    finding = only(validate_structure(doc), "E4")

    assert "nope" in finding.message
    assert finding.fix_hint


def test_e4_catches_an_alias_with_no_matching_clip() -> None:
    doc = valid_doc()
    facts = [c for c in clip_facts() if c.name != "C0013.mp4"]

    finding = only(validate_project(doc, facts), "E4")

    assert "C0013.mp4" in finding.message


def test_e4_lists_candidates_when_an_alias_is_ambiguous() -> None:
    doc = valid_doc()
    doc["sources"]["gtr_close"].pop("bin")
    facts = [
        *clip_facts(),
        ClipFacts(name="C0012.mp4", bin_path="Backup", start=0, end_exclusive=20000, fps=59.94),
    ]

    finding = only(validate_project(doc, facts), "E4")

    assert "Angles" in finding.message and "Backup" in finding.message


def test_e4_matches_on_bin_when_one_is_declared() -> None:
    doc = valid_doc()
    doc["sources"]["gtr_close"]["bin"] = "Elsewhere"

    findings = validate_project(doc, clip_facts())

    assert "E4" in rules(findings)


# --- E5: in/out inside clip media bounds ------------------------------------------------


def test_e5_catches_an_out_point_past_the_end_of_the_media() -> None:
    doc = valid_doc()
    doc["segments"][0]["out"] = 99999

    finding = only(validate_project(doc, clip_facts()), "E5")

    assert finding.id == "s001"


def test_e5_catches_an_in_point_before_the_start_of_the_media() -> None:
    doc = valid_doc()
    doc["segments"][0]["in"] = -5

    findings = validate_project(doc, clip_facts())

    assert "E5" in rules(findings)


def test_e5_treats_the_range_as_half_open() -> None:
    """out == end_exclusive is the last legal frame boundary, not an overrun."""
    doc = valid_doc()
    doc["segments"][0]["in"] = 19800
    doc["segments"][0]["out"] = 20000
    doc["segments"][0]["alternates"] = []

    findings = validate_project(doc, clip_facts())

    assert findings == []


def test_e5_exempts_stills_which_have_one_frame_and_any_duration() -> None:
    doc = valid_doc()
    doc["segments"][0]["out"] = 99999
    doc["segments"][0]["alternates"] = []
    facts = [
        c if c.name != "C0012.mp4" else replace(c, end_exclusive=1, fps=None, is_still=True)
        for c in clip_facts()
    ]

    findings = validate_project(doc, facts)

    assert "E5" not in rules(findings)


def test_e5_covers_alternates_and_overlays() -> None:
    doc = valid_doc()
    doc["segments"][0]["alternates"][0]["out"] = 99999
    doc["overlays"][0]["out"] = 99999

    findings = validate_project(doc, clip_facts())

    assert rules(findings).count("E5") == 2


# --- W9: E5 fails open on unknown bounds, and says so (#186) ----------------------------


def test_w9_warns_when_a_clips_media_bounds_were_never_reported() -> None:
    """Fail-open is right — 'Resolve did not say' is not an overrun — but not silent."""
    doc = valid_doc()
    doc["segments"][0]["alternates"] = []
    facts = [
        c if c.name != "C0012.mp4" else replace(c, start=None, end_exclusive=None)
        for c in clip_facts()
    ]

    finding = only(validate_project(doc, facts), "W9")

    assert finding.id == "s001"
    assert "C0012.mp4" in finding.message
    assert "E5" not in rules(validate_project(doc, facts))


def test_w9_covers_the_master_mix_whose_bounds_e7_checks_the_same_way() -> None:
    doc = valid_doc()
    facts = [
        c if c.name != "sunset-master.wav" else replace(c, start=None, end_exclusive=None)
        for c in clip_facts()
    ]

    finding = only(validate_project(doc, facts), "W9")

    assert finding.id is None
    assert "sunset-master.wav" in finding.message


def test_w9_exempts_stills_which_have_no_bounds_to_check() -> None:
    doc = valid_doc()
    doc["segments"][0]["alternates"] = []
    facts = [
        c
        if c.name != "C0012.mp4"
        else replace(c, start=None, end_exclusive=None, fps=None, is_still=True)
        for c in clip_facts()
    ]

    assert "W9" not in rules(validate_project(doc, facts))


def test_w9_stays_quiet_when_the_bounds_are_known() -> None:
    assert "W9" not in rules(validate_project(valid_doc(), clip_facts()))


# --- E6: source fps matches timeline fps ------------------------------------------------


def test_e6_catches_a_source_at_a_different_frame_rate() -> None:
    doc = valid_doc()
    facts = [c if c.name != "C0013.mp4" else replace(c, fps=29.97) for c in clip_facts()]

    findings = validate_project(doc, facts)

    assert "E6" in rules(findings)


def test_e6_tolerates_float_noise_in_the_reported_rate() -> None:
    doc = valid_doc()
    facts = [c if c.fps is None else replace(c, fps=59.9400599) for c in clip_facts()]

    findings = validate_project(doc, facts)

    assert findings == []


def test_e6_exempts_stills() -> None:
    doc = valid_doc()
    facts = [
        c if c.name != "C0013.mp4" else replace(c, fps=29.97, is_still=True)
        for c in clip_facts()
    ]

    findings = validate_project(doc, facts)

    assert "E6" not in rules(findings)


# --- E7: the audio block resolves, has audio, bounds valid ------------------------------


def test_e7_catches_a_master_clip_with_no_audio() -> None:
    doc = valid_doc()
    facts = [
        c if c.name != "sunset-master.wav" else replace(c, has_audio=False) for c in clip_facts()
    ]

    findings = validate_project(doc, facts)

    assert "E7" in rules(findings)


def test_e7_catches_audio_bounds_past_the_end_of_the_master_clip() -> None:
    doc = valid_doc()
    doc["audio"]["out"] = 99999

    findings = validate_project(doc, clip_facts())

    assert "E7" in rules(findings)


def test_e7_catches_an_audio_alias_that_is_not_declared() -> None:
    doc = valid_doc()
    doc["audio"]["source"] = "nope"

    findings = validate_structure(doc)

    assert "E7" in rules(findings)


def test_a_document_without_an_audio_block_is_legal() -> None:
    doc = valid_doc()
    doc.pop("audio")

    structural_findings = validate_structure(doc)
    project_findings = validate_project(doc, clip_facts())

    assert structural_findings == []
    assert project_findings == []


# --- E8: alternate duration equals main duration ----------------------------------------


def test_e8_catches_an_alternate_of_a_different_duration() -> None:
    doc = valid_doc()
    doc["segments"][0]["alternates"][0]["out"] = 8250

    finding = only(validate_structure(doc), "E8")

    assert finding.id == "s001"
    assert "200" in finding.message and "150" in finding.message


# --- E9: overlay anchoring --------------------------------------------------------------


def test_e9_catches_an_anchor_that_does_not_exist() -> None:
    doc = valid_doc()
    doc["overlays"][0]["over"]["segment"] = "s999"

    finding = only(validate_structure(doc), "E9")

    assert finding.id == "b03"


def test_e9_catches_an_offset_past_the_end_of_its_anchor() -> None:
    doc = valid_doc()
    doc["overlays"][0]["over"]["offset"] = 500

    findings = validate_structure(doc)

    assert "E9" in rules(findings)


def test_e9_catches_a_negative_offset() -> None:
    doc = valid_doc()
    doc["overlays"][0]["over"]["offset"] = -1

    findings = validate_structure(doc)

    assert "E9" in rules(findings)


def test_e9_allows_an_overlay_running_past_its_anchor() -> None:
    """Seam coverage: the overlay may outlast the segment it is anchored to."""
    doc = valid_doc()
    doc["overlays"][0]["over"]["offset"] = 190
    doc["overlays"][0]["out"] = 1300  # 100 frames, anchor ends 10 frames in

    findings = validate_structure(doc)

    assert findings == []


def test_e9_catches_an_overlay_running_past_the_end_of_the_cut() -> None:
    doc = valid_doc()
    doc["overlays"][0]["over"]["offset"] = 190
    doc["overlays"][0]["out"] = 1600  # 400 frames from frame 190 of a 400-frame cut

    findings = validate_structure(doc)

    assert "E9" in rules(findings)


# --- E10: overlays do not overlap each other --------------------------------------------


def test_e10_catches_two_overlays_covering_the_same_frames() -> None:
    doc = valid_doc()
    doc["overlays"].append(
        {
            "id": "b04",
            "source": "broll_pan",
            "in": 1400,
            "out": 1500,
            "over": {"segment": "s001", "offset": 100},
        }
    )

    finding = only(validate_structure(doc), "E10")

    assert "b03" in finding.message and "b04" in finding.message


def test_e10_allows_overlays_that_touch_at_a_boundary() -> None:
    """Half-open ranges: one ending where the next begins is not an overlap."""
    doc = valid_doc()
    doc["overlays"].append(
        {
            "id": "b04",
            "source": "broll_pan",
            "in": 1400,
            "out": 1500,
            "over": {"segment": "s001", "offset": 124},
        }
    )

    findings = validate_structure(doc)

    assert findings == []


def test_e10_catches_an_overlay_sitting_wholly_inside_a_longer_one() -> None:
    """Sorted by start, a contained overlay is not adjacent to the one covering it."""
    doc = valid_doc()
    doc["overlays"][0]["over"]["offset"] = 0
    doc["overlays"][0]["out"] = 1500  # b03 covers 0-300
    doc["overlays"] += [
        {
            "id": "b04",
            "source": "broll_pan",
            "in": 1200,
            "out": 1210,
            "over": {"segment": "s001", "offset": 10},
        },
        {
            "id": "b05",
            "source": "broll_pan",
            "in": 1200,
            "out": 1210,
            "over": {"segment": "s001", "offset": 30},
        },
    ]

    findings = validate_structure(doc)

    assert [f.id for f in findings if f.rule == "E10"] == ["b04", "b05"]


def test_e10_compares_positions_across_different_anchors() -> None:
    """Anchored offsets resolve to absolute positions before overlap is judged."""
    doc = valid_doc()
    doc["overlays"][0]["over"] = {"segment": "s002", "offset": 0}
    doc["overlays"].append(
        {
            "id": "b04",
            "source": "broll_pan",
            "in": 1400,
            "out": 1500,
            "over": {"segment": "s001", "offset": 150},
        }
    )

    findings = validate_structure(doc)

    assert "E10" in rules(findings)


# --- W1: flash-frame guard --------------------------------------------------------------


def test_w1_warns_on_a_segment_shorter_than_the_minimum() -> None:
    doc = valid_doc()
    doc["segments"][1]["out"] = doc["segments"][1]["in"] + 5

    finding = only(validate_structure(doc), "W1")

    assert finding.id == "s002"
    assert finding.severity == "warning"


def test_w1_threshold_defaults_to_twelve_frames_and_is_tunable() -> None:
    doc = valid_doc()
    doc["segments"][1]["out"] = doc["segments"][1]["in"] + 12

    default_findings = validate_structure(doc)
    tuned_findings = validate_structure(doc, min_segment_frames=13)

    assert "W1" not in rules(default_findings)
    assert "W1" in rules(tuned_findings)


# --- W2: V1 total against the master-audio span -----------------------------------------


def test_w2_warns_when_the_cut_does_not_match_the_master_audio_span() -> None:
    doc = valid_doc()
    doc["audio"]["out"] = 900

    finding = only(validate_structure(doc), "W2")

    assert finding.severity == "warning"
    assert "400" in finding.message and "900" in finding.message


def test_w2_is_silent_without_an_audio_block() -> None:
    doc = valid_doc()
    doc.pop("audio")

    findings = validate_structure(doc)

    assert "W2" not in rules(findings)


# --- findings are structured ------------------------------------------------------------


def test_every_finding_carries_a_rule_message_and_fix_hint() -> None:
    doc = valid_doc()
    doc["segments"][1]["id"] = "s001"
    doc["segments"][1]["out"] = doc["segments"][1]["in"]

    findings = validate_structure(doc)

    for finding in findings:
        assert finding.rule and finding.message and finding.fix_hint
        assert set(finding.as_dict()) == {"rule", "id", "message", "fix_hint"}


def test_findings_are_ordered_by_rule_with_warnings_last() -> None:
    doc = valid_doc()
    doc["segments"][1]["id"] = "s001"  # E2
    doc["segments"][0]["out"] = doc["segments"][0]["in"]  # E3, and a zero-length cascade

    findings = validate_structure(doc)

    assert rules(findings) == ["E2", "E3", "E8", "W1", "W2"]


def test_rule_numbers_order_numerically_not_lexicographically() -> None:
    """E10 sorts after E3 — the trap a plain string sort falls into."""
    doc = valid_doc()
    doc["audio"]["out"] = doc["audio"]["in"]  # E3 on the audio block, and W2
    doc["overlays"].append(
        {
            "id": "b04",
            "source": "broll_pan",
            "in": 1400,
            "out": 1500,
            "over": {"segment": "s001", "offset": 100},
        }
    )  # E10

    findings = validate_structure(doc)

    assert rules(findings) == ["E3", "E10", "W2"]


def test_validation_does_not_mutate_the_document() -> None:
    doc = valid_doc()
    before = copy.deepcopy(doc)

    validate_structure(doc)
    validate_project(doc, clip_facts())

    assert doc == before
