"""apply_titles at the Resolve seam: what lands on the Titles track, and what never starts.

Three things are under test and nothing else matters as much:

* a valid titles file becomes frame-exact Text+ instances on one owned track, each with
  its own words and its own opacity spline;
* running it again produces the same track rather than a second copy of it, which is the
  whole point of a declarative apply — rebuild the cut, re-apply the titles;
* everything that could half-apply one — an invalid file, a song with no marker, a locked
  track, a clear that did not clear, an append that slid, instances sharing one comp —
  stops with a structured failure, and the ones that can stop *before* the track is
  cleared do.

The fake models the append footguns confirmed on live Resolve (#18) and the Fusion ones
found by the Text+ probe (#41): a template whose instances share a comp, a build that
answers ``None`` for a method it does not have, an input that will not animate. A wrapper
that trusted Resolve's return value fails these tests rather than a real concert.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from resolve_mcp.tools.titles import apply_titles, get_titles_schema, validate_titles

from .conftest import Attach
from .fakes import (
    FakeFusionComp,
    FakeFusionTool,
    FakeMediaPool,
    FakeMediaPoolItem,
    FakeTimeline,
    FakeTimelineItem,
    FakeTrack,
    media_pool,
    studio,
    text_plus_template,
)

TITLES_TRACK = 2
"""Where the tool puts its own track on a timeline that arrives with one video track."""

SONG_ONE = 3600
"""Record frame of the first blue marker — the timeline starts at an hour of timecode."""

SONG_TWO = 9000


def a_titles_file(tmp_path: Path, doc: Any, name: str = "titles.json") -> str:
    path = tmp_path / name
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def valid_doc(**overrides: Any) -> dict[str, Any]:
    """A title card and a personnel card on one song, a title on the next."""
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
                    {"id": "t03", "kind": "title", "text": "Night Ferry", "in": 120, "out": 480}
                ],
            },
        ],
    }
    doc.update(overrides)
    return doc


def one_event(**overrides: Any) -> dict[str, Any]:
    """The smallest file that places something: one song, one title, no fade."""
    event: dict[str, Any] = {
        "id": "t01",
        "kind": "title",
        "text": "Sunset Boulevard",
        "in": 240,
        "out": 720,
    }
    event.update(overrides)
    return valid_doc(songs=[{"key": "sunset-boulevard", "events": [event]}])


def a_marker(name: str, color: str = "Blue") -> dict[str, Any]:
    return {"color": color, "name": name, "note": "", "duration": 1, "customData": ""}


def a_timeline(
    name: str = "sunset-set v3",
    video: list[FakeTrack] | None = None,
    markers: dict[Any, dict[str, Any]] | None = None,
    missing: set[str] | None = None,
) -> FakeTimeline:
    """A built cut at an hour of timecode, with its songs marked in blue."""
    return FakeTimeline(
        name,
        start_frame=SONG_ONE,
        end_frame=20000,
        missing=missing,
        video=video
        if video is not None
        else [FakeTrack("Video 1", [FakeTimelineItem("C0012.mp4", SONG_ONE, 12000)])],
        markers=markers
        if markers is not None
        else {
            0: a_marker("sunset-boulevard"),
            SONG_TWO - SONG_ONE: a_marker("night-ferry"),
            120: a_marker("tighten this", color="Red"),
        },
    )


def a_pool(**templates: FakeMediaPoolItem) -> FakeMediaPool:
    return media_pool(
        {
            "Titles/Templates": [templates.get("title", text_plus_template("Song Title"))],
            "Titles": [templates.get("personnel", text_plus_template("Personnel"))],
        }
    )


def a_session(
    attach: Attach,
    timeline: FakeTimeline | None = None,
    pool: FakeMediaPool | None = None,
    timelines: list[FakeTimeline] | None = None,
) -> FakeTimeline:
    """Attach a Studio with one marked cut open and the two templates in the pool."""
    cut = timeline if timeline is not None else a_timeline()
    held: list[FakeTimeline | None] = list(timelines or [cut])
    attach(studio(timeline=cut, timelines=held, pool=pool or a_pool()))
    return cut


def titles_on(timeline: FakeTimeline, track: int = TITLES_TRACK) -> list[FakeTimelineItem]:
    return list(timeline.GetItemListInTrack("video", track) or [])


def text_of(item: FakeTimelineItem) -> Any:
    return item.comps[0].tools[0].GetInput("StyledText")


def keyframes_of(item: FakeTimelineItem) -> dict[float, float]:
    spline = item.comps[0].tools[0].animated.get("Opacity1")
    return {} if spline is None else spline.keyframes


# --- the happy path ---------------------------------------------------------------------


def test_a_titles_file_lands_on_its_own_track_at_the_right_frames(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(attach)
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["ok"] is True
    assert [(item.GetStart(), item.GetDuration()) for item in titles_on(timeline)] == [
        (SONG_ONE + 240, 480),
        (SONG_ONE + 960, 360),
        (SONG_TWO + 120, 360),
    ]


def test_every_instance_gets_its_own_words(attach: Attach, tmp_path: Path) -> None:
    timeline = a_session(attach)
    apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert [text_of(item) for item in titles_on(timeline)] == [
        "Sunset Boulevard",
        "Bass — Ana Ruiz",
        "Night Ferry",
    ]


def test_the_track_is_created_named_and_left_on_top(attach: Attach, tmp_path: Path) -> None:
    timeline = a_session(attach)
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["track"] == {"index": TITLES_TRACK, "name": "Titles", "created": True}
    assert timeline.GetTrackName("video", TITLES_TRACK) == "Titles"
    assert timeline.GetTrackCount("video") == TITLES_TRACK


def test_the_cut_underneath_is_never_touched(attach: Attach, tmp_path: Path) -> None:
    timeline = a_session(attach)
    apply_titles(a_titles_file(tmp_path, valid_doc()))

    v1 = timeline.GetItemListInTrack("video", 1) or []
    assert [(item.GetName(), item.GetStart()) for item in v1] == [("C0012.mp4", SONG_ONE)]


def test_the_report_names_every_title_it_placed(attach: Attach, tmp_path: Path) -> None:
    a_session(attach)
    placed = apply_titles(a_titles_file(tmp_path, valid_doc()))["placed"]

    assert [one["id"] for one in placed] == ["t01", "t02", "t03"]
    assert placed[0]["song"] == "sunset-boulevard"
    assert placed[0]["template"] == "title"
    assert placed[0]["record"]["frames"] == SONG_ONE + 240
    assert placed[0]["duration"]["frames"] == 480
    assert placed[0]["node"]["name"] == "Template Text"


def test_the_report_echoes_the_file_it_applied(attach: Attach, tmp_path: Path) -> None:
    a_session(attach)
    file = a_titles_file(tmp_path, valid_doc())
    result = apply_titles(file)

    assert result["titles_file"] == file
    assert len(result["content_hash"]) == 32


def test_a_marked_song_with_no_titles_is_reported_as_a_warning(
    attach: Attach,
    tmp_path: Path,
) -> None:
    a_session(attach)
    result = apply_titles(a_titles_file(tmp_path, one_event()))

    assert [warning["rule"] for warning in result["warnings"]] == ["W2"]
    assert result["ok"] is True


# --- re-running -------------------------------------------------------------------------


def test_applying_twice_replaces_the_titles_rather_than_stacking_them(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(attach)
    file = a_titles_file(tmp_path, valid_doc())

    first = apply_titles(file)
    second = apply_titles(file)

    assert (first["cleared"], second["cleared"]) == (0, 3)
    assert second["track"]["created"] is False
    assert len(titles_on(timeline)) == 3


def test_a_re_run_after_an_edit_shows_the_new_words_and_nothing_of_the_old(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(attach)
    apply_titles(a_titles_file(tmp_path, one_event()))
    apply_titles(a_titles_file(tmp_path, one_event(text="Sunset Blvd.", out=600)))

    assert [(text_of(item), item.GetDuration()) for item in titles_on(timeline)] == [
        ("Sunset Blvd.", 360)
    ]


def test_the_clear_is_never_a_ripple_delete(attach: Attach, tmp_path: Path) -> None:
    """A ripple would drag the cut on the tracks below along with the titles."""
    timeline = a_session(attach)
    file = a_titles_file(tmp_path, valid_doc())
    apply_titles(file)
    apply_titles(file)

    assert [ripple for _, ripple in timeline.deleted_clips] == [False]


def test_an_existing_titles_track_is_adopted_rather_than_added(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(
        attach,
        a_timeline(video=[FakeTrack("Video 1"), FakeTrack("Titles")]),
    )
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["track"] == {"index": TITLES_TRACK, "name": "Titles", "created": False}
    assert timeline.GetTrackCount("video") == TITLES_TRACK


def test_the_topmost_titles_track_wins_when_the_project_has_two(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(
        attach,
        a_timeline(video=[FakeTrack("Titles"), FakeTrack("Video 2"), FakeTrack("Titles")]),
    )
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["track"]["index"] == 3
    assert len(titles_on(timeline, 3)) == 3


def test_the_track_name_can_be_chosen_in_the_file(attach: Attach, tmp_path: Path) -> None:
    timeline = a_session(attach)
    result = apply_titles(a_titles_file(tmp_path, valid_doc(track="Lower Thirds")))

    assert result["track"]["name"] == "Lower Thirds"
    assert timeline.GetTrackName("video", TITLES_TRACK) == "Lower Thirds"


# --- fades ------------------------------------------------------------------------------


def test_a_fade_is_written_as_opacity_keyframes_over_the_instance(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(attach)
    apply_titles(a_titles_file(tmp_path, valid_doc()))

    # The instance is 480 frames, so its comp renders 0-479: up over 24, down over 36.
    assert keyframes_of(titles_on(timeline)[0]) == {0.0: 0.0, 24.0: 1.0, 443.0: 1.0, 479.0: 0.0}


def test_a_fade_that_read_back_is_reported_as_verified(attach: Attach, tmp_path: Path) -> None:
    a_session(attach)
    placed = apply_titles(a_titles_file(tmp_path, valid_doc()))["placed"]

    assert placed[0]["fade"]["verified"] is True
    assert (placed[0]["fade"]["in"], placed[0]["fade"]["out"]) == (24, 36)


def test_an_event_with_no_fade_block_gets_no_spline_at_all(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(attach)
    placed = apply_titles(a_titles_file(tmp_path, valid_doc()))["placed"]

    assert keyframes_of(titles_on(timeline)[1]) == {}
    assert placed[1]["fade"] == {"in": 0, "out": 0, "keyframes": [], "verified": True,
                                 "detail": "no fade asked for"}


def test_a_fade_in_only_leaves_the_title_up_at_the_end(attach: Attach, tmp_path: Path) -> None:
    timeline = a_session(attach)
    apply_titles(a_titles_file(tmp_path, one_event(fade={"in": 24})))

    assert keyframes_of(titles_on(timeline)[0]) == {0.0: 0.0, 24.0: 1.0}


def test_a_build_that_cannot_animate_still_places_the_title(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """The words are the title; the fade is how it arrives. Losing one must not lose both."""
    template = FakeMediaPoolItem(
        "Song Title",
        properties={"Type": "Generator", "File Path": ""},
        template_comp=FakeFusionComp([FakeFusionTool()], missing={"BezierSpline"}),
    )
    timeline = a_session(attach, pool=a_pool(title=template))
    result = apply_titles(a_titles_file(tmp_path, one_event(fade={"in": 24, "out": 24})))

    assert result["ok"] is True
    assert text_of(titles_on(timeline)[0]) == "Sunset Boulevard"
    assert result["placed"][0]["fade"]["verified"] is False
    assert "BezierSpline" in result["placed"][0]["fade"]["detail"]


def test_a_text_plus_node_without_the_opacity_input_reports_an_unwritten_fade(
    attach: Attach,
    tmp_path: Path,
) -> None:
    template = FakeMediaPoolItem(
        "Song Title",
        properties={"Type": "Generator", "File Path": ""},
        template_comp=FakeFusionComp([FakeFusionTool(animatable=False)]),
    )
    a_session(attach, pool=a_pool(title=template))
    result = apply_titles(a_titles_file(tmp_path, one_event(fade={"in": 24, "out": 24})))

    assert result["ok"] is True
    assert "Opacity1" in result["placed"][0]["fade"]["detail"]


def test_a_build_that_will_not_read_an_input_at_a_time_reports_the_fade_unverified(
    attach: Attach,
    tmp_path: Path,
) -> None:
    template = FakeMediaPoolItem(
        "Song Title",
        properties={"Type": "Generator", "File Path": ""},
        template_comp=FakeFusionComp([FakeFusionTool(reads_at_a_time=False)]),
    )
    timeline = a_session(attach, pool=a_pool(title=template))
    result = apply_titles(a_titles_file(tmp_path, one_event(fade={"in": 24, "out": 24})))

    assert result["placed"][0]["fade"]["verified"] is False
    assert keyframes_of(titles_on(timeline)[0]) != {}


def test_a_spline_that_refuses_a_keyframe_reports_the_fade_unwritten(
    attach: Attach,
    tmp_path: Path,
) -> None:
    template = FakeMediaPoolItem(
        "Song Title",
        properties={"Type": "Generator", "File Path": ""},
        template_comp=FakeFusionComp([FakeFusionTool()], takes_keyframes=False),
    )
    a_session(attach, pool=a_pool(title=template))
    result = apply_titles(a_titles_file(tmp_path, one_event(fade={"in": 24, "out": 24})))

    assert result["ok"] is True
    assert "keyframe" in result["placed"][0]["fade"]["detail"]


def test_a_comp_that_will_not_say_its_range_fades_over_the_instance_instead(
    attach: Attach,
    tmp_path: Path,
) -> None:
    template = FakeMediaPoolItem(
        "Song Title",
        properties={"Type": "Generator", "File Path": ""},
        template_comp=FakeFusionComp([FakeFusionTool()], render_range=None),
    )
    timeline = a_session(attach, pool=a_pool(title=template))
    apply_titles(a_titles_file(tmp_path, one_event(fade={"in": 24, "out": 24})))

    assert keyframes_of(titles_on(timeline)[0]) == {0.0: 0.0, 24.0: 1.0, 455.0: 1.0, 479.0: 0.0}


# --- what never starts --------------------------------------------------------------------


def test_an_invalid_file_leaves_the_timeline_exactly_as_it_was(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(attach)
    result = apply_titles(a_titles_file(tmp_path, valid_doc(schema=9)))

    assert result["ok"] is False
    assert result["error"]["code"] == "titles_invalid"
    assert timeline.GetTrackCount("video") == 1


def test_a_song_with_no_blue_marker_is_refused_before_anything_is_cleared(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(attach, a_timeline(markers={0: a_marker("a-different-song")}))
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["error"]["code"] == "titles_invalid"
    assert {finding["rule"] for finding in result["error"]["detail"]["errors"]} == {"T7"}
    assert timeline.GetTrackCount("video") == 1


def test_a_marker_of_another_colour_does_not_anchor_a_song(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """Blue is reserved for song starts; a red review note is the director's, not a key."""
    a_session(attach, a_timeline(markers={0: a_marker("sunset-boulevard", color="Red")}))
    result = apply_titles(a_titles_file(tmp_path, one_event()))

    assert result["error"]["code"] == "titles_invalid"


def test_a_template_missing_from_the_pool_is_refused_with_its_name(
    attach: Attach,
    tmp_path: Path,
) -> None:
    a_session(attach, pool=media_pool({"Titles": [text_plus_template("Personnel")]}))
    result = apply_titles(a_titles_file(tmp_path, one_event()))

    errors = result["error"]["detail"]["errors"]
    assert [finding["rule"] for finding in errors] == ["T5"]
    assert "Song Title" in errors[0]["message"]


def test_a_template_in_the_wrong_bin_does_not_answer_for_the_declared_one(
    attach: Attach,
    tmp_path: Path,
) -> None:
    a_session(attach, pool=media_pool({"Archive": [text_plus_template("Song Title")]}))
    result = apply_titles(a_titles_file(tmp_path, one_event()))

    assert result["error"]["detail"]["errors"][0]["rule"] == "T5"


def test_a_locked_titles_track_is_refused_before_the_clear(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(
        attach,
        a_timeline(
            video=[
                FakeTrack("Video 1"),
                FakeTrack("Titles", [FakeTimelineItem("old title", SONG_ONE, 100)], locked=True),
            ]
        ),
    )
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["error"]["code"] == "titles_apply_failed"
    assert len(titles_on(timeline)) == 1


def test_a_build_without_delete_clips_says_so_rather_than_doubling_the_titles(
    attach: Attach,
    tmp_path: Path,
) -> None:
    a_session(
        attach,
        a_timeline(
            video=[
                FakeTrack("Video 1"),
                FakeTrack("Titles", [FakeTimelineItem("old title", SONG_ONE, 100)]),
            ],
            missing={"DeleteClips"},
        ),
    )
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["error"]["code"] == "titles_apply_failed"
    assert "DeleteClips" in result["error"]["cause"]


def test_a_clear_that_leaves_the_titles_standing_stops_the_apply(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(
        attach,
        a_timeline(
            video=[
                FakeTrack("Video 1"),
                FakeTrack("Titles", [FakeTimelineItem("old title", SONG_ONE, 100)]),
            ]
        ),
    )
    timeline.delete_clips_leaves_them = True
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["error"]["code"] == "titles_apply_failed"
    assert result["error"]["detail"]["remaining"] == 1


def test_a_track_that_cannot_be_named_is_refused_so_the_next_apply_finds_it(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(attach)
    timeline.set_track_name_result = False
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["error"]["code"] == "titles_apply_failed"
    assert result["error"]["detail"]["track_index"] == TITLES_TRACK


def test_an_append_that_lands_nowhere_is_caught_by_reading_the_track_back(
    attach: Attach,
    tmp_path: Path,
) -> None:
    pool = a_pool()
    pool.appends_land_nowhere = True
    a_session(attach, pool=pool)
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["error"]["code"] == "titles_apply_failed"
    assert len(result["error"]["detail"]["adrift"]) == 3


def test_a_template_too_short_for_the_span_is_named_as_such(
    attach: Attach,
    tmp_path: Path,
) -> None:
    pool = a_pool()
    pool.append_result = []
    a_session(attach, pool=pool)
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["error"]["code"] == "titles_apply_failed"
    assert "shorter than the span" in result["error"]["fix"]


def test_instances_sharing_one_comp_are_caught_by_reading_every_title_back(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """The failure mode the Text+ probe exists to detect: one comp, so one title, twice."""
    pool = a_pool()
    pool.appends_share_one_comp = True
    a_session(attach, pool=pool)
    result = apply_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["error"]["code"] == "titles_apply_failed"
    assert "share" in result["error"]["fix"]


def test_a_template_clip_with_no_fusion_comp_is_not_a_title_template(
    attach: Attach,
    tmp_path: Path,
) -> None:
    plain = FakeMediaPoolItem("Song Title", properties={"Type": "Video"})
    a_session(attach, pool=a_pool(title=plain))
    result = apply_titles(a_titles_file(tmp_path, one_event()))

    assert result["error"]["code"] == "title_template_unusable"


def test_a_comp_with_no_text_plus_node_says_what_it_holds_instead(
    attach: Attach,
    tmp_path: Path,
) -> None:
    template = FakeMediaPoolItem(
        "Song Title",
        properties={"Type": "Generator", "File Path": ""},
        template_comp=FakeFusionComp([FakeFusionTool(tool_id="Background", name="Backdrop")]),
    )
    a_session(attach, pool=a_pool(title=template))
    result = apply_titles(a_titles_file(tmp_path, one_event()))

    assert result["error"]["code"] == "title_template_unusable"
    assert "Backdrop" in result["error"]["cause"]


def test_a_missing_titles_file_is_a_wrong_call_not_a_finding(
    attach: Attach,
    tmp_path: Path,
) -> None:
    a_session(attach)
    result = apply_titles(str(tmp_path / "nothing.json"))

    assert result["error"]["code"] == "invalid_request"


def test_a_file_that_is_not_json_comes_back_as_t1(attach: Attach, tmp_path: Path) -> None:
    a_session(attach)
    path = tmp_path / "titles.json"
    path.write_text("{ nope", encoding="utf-8")
    result = apply_titles(str(path))

    assert result["error"]["detail"]["errors"][0]["rule"] == "T1"


# --- reaching the right timeline ----------------------------------------------------------


def test_the_named_timeline_is_opened_before_anything_is_appended(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """AppendToTimeline writes to whatever is current, so titling the wrong cut is one
    missed switch away."""
    wanted = a_timeline("sunset-set v3")
    open_cut = a_timeline("holiday-gig v1", markers={})
    a_session(attach, timeline=open_cut, timelines=[open_cut, wanted])

    result = apply_titles(a_titles_file(tmp_path, valid_doc(timeline="sunset-set v3")))

    assert result["ok"] is True
    assert len(titles_on(wanted)) == 3
    assert open_cut.GetTrackCount("video") == 1


def test_a_timeline_the_project_does_not_have_is_named(attach: Attach, tmp_path: Path) -> None:
    a_session(attach)
    result = apply_titles(a_titles_file(tmp_path, valid_doc(timeline="sunset-set v9")))

    assert result["error"]["code"] == "timeline_not_found"


# --- the dry run and the contract ---------------------------------------------------------


def test_the_dry_run_reports_the_same_findings_without_touching_the_timeline(
    attach: Attach,
    tmp_path: Path,
) -> None:
    timeline = a_session(attach, a_timeline(markers={0: a_marker("a-different-song")}))
    result = validate_titles(a_titles_file(tmp_path, valid_doc()))

    assert result["valid"] is False
    assert {finding["rule"] for finding in result["errors"]} == {"T7"}
    assert timeline.GetTrackCount("video") == 1


def test_the_dry_run_passes_a_file_the_apply_would_accept(
    attach: Attach,
    tmp_path: Path,
) -> None:
    a_session(attach)
    result = validate_titles(a_titles_file(tmp_path, valid_doc()))

    assert (result["valid"], result["events"], result["timeline"]) == (True, 3, "sunset-set v3")


def test_the_schema_serves_the_example_and_every_rule() -> None:
    result = get_titles_schema()

    assert result["schema"] == 1
    assert '"in": 240' in result["annotated_example"]
    assert {rule["rule"] for rule in result["rules"]} >= {"T1", "T7", "T8", "W1", "W2"}
    assert {rule["severity"] for rule in result["rules"]} == {"error", "warning"}
