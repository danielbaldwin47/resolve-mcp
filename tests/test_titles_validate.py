"""The titles-file rules, as pure functions over a document and a reading of the project.

Everything here is a decision the server makes about a file, so it all verifies at the
fake tier — no Resolve, and for the structural pass not even a fake one. The project pass
takes the marker and pool facts as data, which is what lets "that song is marked twice"
and "that template name matches two clips" be tested without either ever existing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from resolve_mcp.findings import Finding
from resolve_mcp.titles.validate import (
    TemplateFacts,
    plan,
    validate_project,
    validate_structure,
)


def valid_doc(**overrides: Any) -> dict[str, Any]:
    """Two songs, three events — a title card and a personnel card, then a second song."""
    doc: dict[str, Any] = {
        "schema": 1,
        "templates": {
            "title": {"clip": "Song Title", "bin": "Titles/Templates"},
            "personnel": {"clip": "Personnel"},
        },
        "songs": [
            {
                "key": "sunset-boulevard",
                "events": [
                    {
                        "id": "t01",
                        "kind": "title",
                        "text": "Sunset Boulevard",
                        "in": 240,
                        "out": 720,
                        "fade": {"in": 24, "out": 36},
                    },
                    {
                        "id": "t02",
                        "kind": "personnel",
                        "text": "Bass — Ana Ruiz",
                        "in": 960,
                        "out": 1320,
                    },
                ],
            },
            {
                "key": "night-ferry",
                "events": [
                    {"id": "t03", "kind": "title", "text": "Night Ferry", "in": 120, "out": 480},
                ],
            },
        ],
    }
    doc.update(overrides)
    return doc


def rules_of(findings: Sequence[Finding]) -> list[str]:
    return [finding.rule for finding in findings]


def only(findings: Sequence[Finding], rule: str) -> list[Finding]:
    return [finding for finding in findings if finding.rule == rule]


def an_event(**overrides: Any) -> dict[str, Any]:
    event = {"id": "t01", "kind": "title", "text": "Sunset Boulevard", "in": 0, "out": 240}
    event.update(overrides)
    return event


def one_song(*events: dict[str, Any], key: str = "sunset-boulevard") -> dict[str, Any]:
    return valid_doc(songs=[{"key": key, "events": list(events)}])


# --- the structural pass ------------------------------------------------------------------


def test_a_well_formed_file_has_nothing_to_say_about_it() -> None:
    assert validate_structure(valid_doc()) == []


def test_a_file_that_is_not_an_object_is_t1() -> None:
    assert rules_of(validate_structure([1, 2, 3])) == ["T1"]


def test_an_unsupported_schema_version_is_t1() -> None:
    findings = validate_structure(valid_doc(schema=2))
    assert rules_of(findings) == ["T1"]
    assert "schema 1" in findings[0].message


def test_a_file_with_no_templates_is_t1_because_a_title_needs_one() -> None:
    findings = validate_structure(valid_doc(templates={}))
    assert "templates" in findings[0].message


def test_an_event_with_no_text_is_t1() -> None:
    doc = one_song({"id": "t01", "kind": "title", "in": 0, "out": 240})
    assert "text" in only(validate_structure(doc), "T1")[0].message


def test_a_blank_text_is_t1_because_the_server_never_writes_the_words() -> None:
    doc = one_song(an_event(text="   "))
    assert "text" in only(validate_structure(doc), "T1")[0].message


def test_a_non_integer_offset_is_t1() -> None:
    doc = one_song(an_event(**{"in": 1.5}))
    assert "integer frame offset" in only(validate_structure(doc), "T1")[0].message


def test_an_unknown_kind_is_t1() -> None:
    doc = one_song(an_event(kind="lower-third"))
    assert "kind" in only(validate_structure(doc), "T1")[0].message


def test_a_negative_fade_is_t1() -> None:
    doc = one_song(an_event(fade={"in": -12}))
    assert "fade.in" in only(validate_structure(doc), "T1")[0].message


def test_a_fade_that_is_not_an_object_is_t1() -> None:
    doc = one_song(an_event(fade=24))
    assert "fade" in only(validate_structure(doc), "T1")[0].message


def test_a_shape_failure_silences_the_later_rules() -> None:
    """Rules past T1 read fields whose types they can no longer trust."""
    doc = one_song(an_event(id="t01", **{"out": 0}), an_event(id="t01", text=None))
    assert set(rules_of(validate_structure(doc))) == {"T1"}


def test_two_events_with_one_id_are_t2() -> None:
    doc = one_song(an_event(id="t01"), an_event(id="t01", **{"in": 300, "out": 500}))
    assert rules_of(validate_structure(doc)) == ["T2"]


def test_two_songs_with_one_key_are_t2() -> None:
    doc = valid_doc(
        songs=[
            {"key": "sunset-boulevard", "events": [an_event(id="t01")]},
            {"key": "sunset-boulevard", "events": [an_event(id="t02")]},
        ]
    )
    assert rules_of(validate_structure(doc)) == ["T2"]


def test_an_event_with_no_frames_in_it_is_t3() -> None:
    doc = one_song(an_event(**{"in": 240, "out": 240}))
    assert rules_of(validate_structure(doc)) == ["T3"]


def test_fades_longer_than_the_title_are_t4() -> None:
    doc = one_song(an_event(**{"in": 0, "out": 48}, fade={"in": 36, "out": 36}))
    findings = only(validate_structure(doc), "T4")
    assert "48 frames" in findings[0].message


def test_fades_that_exactly_fill_the_title_are_allowed() -> None:
    doc = one_song(an_event(**{"in": 0, "out": 48}, fade={"in": 24, "out": 24}))
    assert validate_structure(doc) == []


def test_t4_stays_quiet_on_an_event_t3_has_already_failed() -> None:
    doc = one_song(an_event(**{"in": 0, "out": 0}, fade={"in": 24, "out": 24}))
    assert rules_of(validate_structure(doc)) == ["T3"]


def test_an_undeclared_template_is_t5() -> None:
    doc = one_song(an_event(template="lower-third"))
    findings = only(validate_structure(doc), "T5")
    assert "lower-third" in findings[0].message


def test_kind_picks_the_template_when_the_event_does_not() -> None:
    doc = one_song(an_event(kind="personnel", text="Bass — Ana Ruiz"))
    assert validate_structure(doc) == []


def test_a_custom_event_must_name_its_own_template() -> None:
    doc = one_song(an_event(kind="custom"))
    assert rules_of(validate_structure(doc)) == ["T5"]


def test_the_png_route_is_refused_until_the_png_tool_lands() -> None:
    doc = one_song(an_event(route="png"))
    findings = only(validate_structure(doc), "T6")
    assert "textplus" in findings[0].message


def test_a_song_with_no_events_is_only_a_warning() -> None:
    doc = valid_doc(songs=[{"key": "sunset-boulevard", "events": []}])
    findings = validate_structure(doc)
    assert rules_of(findings) == ["W1"]
    assert findings[0].severity == "warning"


# --- the project pass ---------------------------------------------------------------------

ANCHORS: Mapping[str, Sequence[int]] = {"sunset-boulevard": [3600], "night-ferry": [9000]}
SPAN = (3600, 20000)
TEMPLATES = (
    TemplateFacts("title", "Song Title", "Titles/Templates", 1, ("Titles/Templates",)),
    TemplateFacts("personnel", "Personnel", None, 1, ("Titles",)),
)


def project_findings(
    doc: dict[str, Any],
    anchors: Mapping[str, Sequence[int]] = ANCHORS,
    templates: Sequence[TemplateFacts] = TEMPLATES,
    span: tuple[int, int] = SPAN,
) -> list[Finding]:
    return validate_project(doc, anchors=anchors, templates=templates, span=span)


def test_a_file_that_matches_the_project_has_nothing_to_say_about_it() -> None:
    assert project_findings(valid_doc()) == []


def test_a_song_with_no_marker_is_t7_and_says_what_is_marked() -> None:
    findings = only(project_findings(valid_doc(), anchors={"night-ferry": [9000]}), "T7")
    assert "sunset-boulevard" in findings[0].message
    assert "night-ferry" in findings[0].message


def test_a_song_marked_twice_is_t7_because_the_key_must_choose() -> None:
    anchors = {**ANCHORS, "night-ferry": [9000, 14000]}
    findings = only(project_findings(valid_doc(), anchors=anchors), "T7")
    assert "9000, 14000" in findings[0].message


def test_a_template_that_is_in_no_bin_is_t5() -> None:
    absent = (TemplateFacts("title", "Song Title", "Titles/Templates", 0), *TEMPLATES[1:])
    findings = only(project_findings(valid_doc(), templates=absent), "T5")
    assert "Song Title" in findings[0].message


def test_a_template_name_matching_two_clips_is_t5() -> None:
    twice = (
        TemplateFacts("title", "Song Title", None, 2, ("Titles", "Archive/Titles")),
        *TEMPLATES[1:],
    )
    findings = only(project_findings(valid_doc(), templates=twice), "T5")
    assert "Archive/Titles" in findings[0].message


def test_an_event_past_the_end_of_the_timeline_is_t9() -> None:
    findings = only(project_findings(valid_doc(), span=(3600, 4000)), "T9")
    assert {finding.id for finding in findings} == {"t01", "t02", "t03"}


def test_an_event_before_the_timeline_starts_is_t9() -> None:
    doc = one_song(an_event(**{"in": -3600, "out": -3000}))
    assert [finding.rule for finding in project_findings(doc) if finding.severity == "error"] == [
        "T9"
    ]


def test_two_titles_over_one_frame_are_t8_because_the_track_shows_one() -> None:
    doc = one_song(
        an_event(id="t01", **{"in": 0, "out": 480}),
        an_event(id="t02", **{"in": 240, "out": 600}),
    )
    findings = only(project_findings(doc), "T8")
    assert findings[0].id == "t02"
    assert "'t01'" in findings[0].message


def test_titles_that_only_touch_at_a_boundary_do_not_overlap() -> None:
    doc = one_song(
        an_event(id="t01", **{"in": 0, "out": 480}),
        an_event(id="t02", **{"in": 480, "out": 600}),
    )
    assert only(project_findings(doc), "T8") == []


def test_overlap_is_judged_across_songs_not_only_inside_one() -> None:
    doc = valid_doc(
        songs=[
            {"key": "sunset-boulevard", "events": [an_event(id="t01", **{"in": 0, "out": 6000})]},
            {"key": "night-ferry", "events": [an_event(id="t02", **{"in": 0, "out": 240})]},
        ]
    )
    assert rules_of(project_findings(doc)) == ["T8"]


def test_a_marked_song_with_no_title_is_only_a_warning() -> None:
    doc = one_song(an_event(), key="sunset-boulevard")
    findings = project_findings(doc)
    assert rules_of(findings) == ["W2"]
    assert findings[0].id == "night-ferry"


def test_a_song_whose_key_is_unresolved_is_left_out_of_the_later_rules() -> None:
    """T7 already said so; positions derived from a guessed anchor would be noise."""
    doc = one_song(an_event(**{"in": 0, "out": 999999}))
    assert rules_of(project_findings(doc, anchors={})) == ["T7"]


# --- the plan ------------------------------------------------------------------------------


def test_events_are_positioned_forward_from_their_song_marker() -> None:
    placed = plan(valid_doc(), ANCHORS)
    assert [(event.id, event.record_in, event.duration) for event in placed] == [
        ("t01", 3840, 480),
        ("t02", 4560, 360),
        ("t03", 9120, 360),
    ]


def test_a_plan_carries_the_fades_and_the_template_each_event_resolved_to() -> None:
    first, second, _ = plan(valid_doc(), ANCHORS)
    assert (first.fade_in, first.fade_out) == (24, 36)
    assert (second.fade_in, second.fade_out) == (0, 0)
    assert (first.template, second.template) == ("title", "personnel")


def test_a_planned_event_knows_where_it_ends() -> None:
    first = plan(valid_doc(), ANCHORS)[0]
    assert first.record_out == first.record_in + first.duration == 4320
