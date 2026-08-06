"""list_titles and edit_title at the Resolve seam: fixing one title without re-applying.

The claim under test is narrow and the whole of #43: a typo fix is one call that reaches
exactly one placed Text+ instance, changes its words or its exposed inputs, and leaves
everything else — the other titles, their fades, the clips themselves — exactly as it
found them.

So three things carry the weight here:

* **In place means in place.** The tests hold on to the very ``FakeTimelineItem`` objects
  that were on the track before the edit and assert the same objects are there after, with
  their opacity keyframes intact. A tool that quietly re-placed the title would produce a
  correct-looking report and fail these.
* **The neighbours are read, not assumed.** A template whose instances share one Fusion
  comp is modelled by handing two items the *same* ``FakeFusionComp``, which is what #41
  went looking for live. Editing one title then re-words the other, and the test asserts
  that is refused rather than reported as a success.
* **A write that reports nothing is not evidence.** ``SetInput`` answers ``None`` whether
  it wrote or not, so ``refuses`` models the write that is taken and dropped, and an input
  id this template has not got reads back as ``None``. Both must fail loudly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from resolve_mcp.tools.titles import apply_titles, edit_title, list_titles

from .conftest import Attach
from .fakes import (
    FakeFusionComp,
    FakeFusionTool,
    FakeTimeline,
    FakeTimelineItem,
    FakeTrack,
    media_pool,
    studio,
    text_plus_template,
)

TITLES_TRACK = 2
"""Where the Titles track sits on a timeline that also carries one video track."""

FIRST = 3840
SECOND = 4560


def a_title(
    text: str,
    record: int,
    duration: int = 360,
    *,
    inputs: dict[str, Any] | None = None,
    defaults: dict[str, Any] | None = None,
    internal: set[str] | None = None,
    comp: FakeFusionComp | None = None,
    refuses: set[str] | None = None,
    missing: set[str] | None = None,
) -> FakeTimelineItem:
    """One Text+ instance standing on the Titles track, as an apply would have left it.

    ``comp`` is passed in when two instances have to *share* one, which is the failure
    mode the neighbour check exists for; otherwise each gets its own.
    """
    own = comp or FakeFusionComp(
        [
            FakeFusionTool(
                inputs={"StyledText": text, **(inputs or {})},
                refuses=refuses,
                missing=missing,
                defaults=defaults,
                internal=internal,
            )
        ]
    )
    return FakeTimelineItem("Song Title", record, duration, comps=[own])


def a_timeline(
    titles: list[FakeTimelineItem] | None = None,
    name: str = "sunset-set v3",
) -> FakeTimeline:
    """A cut with a Titles track above it, holding whatever the test put there."""
    tracks = [FakeTrack("Video 1", [FakeTimelineItem("C0012.mp4", 3600, 12000)])]
    if titles is not None:
        tracks.append(FakeTrack("Titles", titles))
    return FakeTimeline(name, start_frame=3600, end_frame=20000, video=tracks)


def a_session(
    attach: Attach,
    timeline: FakeTimeline | None = None,
    timelines: list[FakeTimeline] | None = None,
) -> FakeTimeline:
    cut = timeline if timeline is not None else a_timeline([a_title("Sunset Boulevard", FIRST)])
    held: list[FakeTimeline | None] = list(timelines or [cut])
    attach(studio(timeline=cut, timelines=held))
    return cut


def items_on(timeline: FakeTimeline, track: int = TITLES_TRACK) -> list[FakeTimelineItem]:
    return list(timeline.GetItemListInTrack("video", track) or [])


def text_of(item: FakeTimelineItem) -> Any:
    return item.comps[0].tools[0].GetInput("StyledText")


def input_of(item: FakeTimelineItem, key: str) -> Any:
    return item.comps[0].tools[0].GetInput(key)


def error(result: dict[str, Any]) -> dict[str, Any]:
    assert result["ok"] is False, result
    return dict(result["error"])


# --- list_titles: what is on the track --------------------------------------------------


def test_every_title_on_the_track_is_listed_in_timeline_order(attach: Attach) -> None:
    a_session(
        attach,
        a_timeline([a_title("Night Ferry", SECOND), a_title("Sunset Boulevard", FIRST)]),
    )
    result = list_titles()

    assert result["ok"] is True
    assert [(one["position"], one["text"]) for one in result["titles"]] == [
        (1, "Sunset Boulevard"),
        (2, "Night Ferry"),
    ]
    assert [one["record"]["frames"] for one in result["titles"]] == [FIRST, SECOND]
    assert result["track"] == {"index": TITLES_TRACK, "name": "Titles", "created": False}


def test_the_inputs_the_template_sets_are_listed_by_their_fusion_ids(attach: Attach) -> None:
    a_session(
        attach,
        a_timeline(
            [
                a_title(
                    "Sunset Boulevard",
                    FIRST,
                    inputs={"Size": 0.09, "Font": "Open Sans", "Wrap": 1.0},
                    defaults={"Size": 0.08, "Wrap": 1.0},
                )
            ]
        ),
    )
    result = list_titles()

    params = result["titles"][0]["params"]
    assert params["read"] is True
    # Size was moved off its default and Font carries a choice, so both are the template's
    # own. Wrap is sitting where it shipped, and StyledText is left out on purpose: the
    # words have their own field, and two places to write one value is how they drift.
    assert params["values"] == {"Size": 0.09, "Font": "Open Sans"}


def test_a_listing_says_how_many_inputs_it_passed_over(attach: Attach) -> None:
    """A live Text+ lists 309 inputs; the reader has to know the listing is a summary."""
    a_session(
        attach,
        a_timeline(
            [
                a_title(
                    "Sunset Boulevard",
                    FIRST,
                    inputs={"Size": 0.09, "Wrap": 1.0, "Angle": 0.0, "Nest": 0.0},
                    defaults={"Size": 0.08, "Wrap": 1.0, "Angle": 0.0, "Nest": 0.0},
                    internal={"Nest"},
                )
            ]
        ),
    )
    result = list_titles()

    params = result["titles"][0]["params"]
    assert params["values"] == {"Size": 0.09}
    # StyledText, Size, Wrap and Angle are editable; Nest is Fusion's own furniture.
    assert params["detail"] == "1 input(s) this template sets, of 3 editable of 5 listed"


def test_an_input_that_is_not_a_control_is_never_listed(attach: Attach) -> None:
    a_session(
        attach,
        a_timeline(
            [
                a_title(
                    "Sunset Boulevard",
                    FIRST,
                    inputs={"SettingsNest": "grouping", "Font": "Open Sans"},
                    internal={"SettingsNest"},
                )
            ]
        ),
    )
    result = list_titles()

    assert result["titles"][0]["params"]["values"] == {"Font": "Open Sans"}


def test_an_input_whose_value_is_not_a_scalar_is_left_out_of_the_listing(
    attach: Attach,
) -> None:
    a_session(
        attach,
        a_timeline(
            [
                a_title(
                    "Sunset Boulevard",
                    FIRST,
                    inputs={"Size": 0.09, "Image1": object()},
                    defaults={"Size": 0.08},
                )
            ]
        ),
    )
    result = list_titles()

    assert result["titles"][0]["params"]["values"] == {"Size": 0.09}


def test_a_flood_of_set_inputs_is_capped_and_says_so(attach: Attach) -> None:
    many = {f"Input{index:03d}": float(index + 1) for index in range(60)}
    stock = dict.fromkeys(many, 0.0)
    a_session(
        attach,
        a_timeline([a_title("Sunset Boulevard", FIRST, inputs=many, defaults=stock)]),
    )
    result = list_titles()

    params = result["titles"][0]["params"]
    assert len(params["values"]) == 40
    assert sorted(params["values"])[0] == "Input000"
    assert params["detail"].endswith("showing the first 40 by id")


def test_a_build_that_will_not_enumerate_inputs_says_so_and_still_reads_the_text(
    attach: Attach,
) -> None:
    a_session(
        attach,
        a_timeline([a_title("Sunset Boulevard", FIRST, missing={"GetInputList"})]),
    )
    result = list_titles()

    assert result["titles"][0]["text"] == "Sunset Boulevard"
    assert result["titles"][0]["params"] == {
        "values": {},
        "read": False,
        "detail": "this build's TextPlus node has no GetInputList",
    }


def test_a_timeline_with_no_titles_track_lists_nothing_rather_than_failing(
    attach: Attach,
) -> None:
    a_session(attach, a_timeline())
    result = list_titles()

    assert result["ok"] is True
    assert result["track"] is None
    assert result["titles"] == []


def test_a_clip_on_the_titles_track_that_is_not_a_title_is_listed_with_the_reason(
    attach: Attach,
) -> None:
    stray = FakeTimelineItem("B-roll.mp4", SECOND, 200)
    a_session(attach, a_timeline([a_title("Sunset Boulevard", FIRST), stray]))
    result = list_titles()

    assert [one["text"] for one in result["titles"]] == ["Sunset Boulevard", None]
    assert "carries no Fusion comp" in result["titles"][1]["unreadable"]
    assert result["titles"][1]["params"] is None


def test_a_named_timeline_is_read_rather_than_the_current_one(attach: Attach) -> None:
    other = a_timeline([a_title("Night Ferry", FIRST)], name="holiday-gig v1")
    a_session(attach, timelines=[a_timeline([a_title("Sunset Boulevard", FIRST)]), other])

    result = list_titles(timeline="holiday-gig v1")

    assert [one["text"] for one in result["titles"]] == ["Night Ferry"]


# --- edit_title: the typo fix -----------------------------------------------------------


def test_the_words_of_one_title_are_changed_in_place(attach: Attach) -> None:
    timeline = a_session(attach, a_timeline([a_title("Sunset Boulevar", FIRST)]))
    standing = items_on(timeline)

    result = edit_title(title="Sunset Boulevar", text="Sunset Boulevard")

    assert result["ok"] is True
    assert text_of(standing[0]) == "Sunset Boulevard"
    # The same clip object, still on the track: nothing was cleared, placed or re-appended.
    assert items_on(timeline) == standing
    assert result["edited"] == ["StyledText"]


def test_an_exposed_param_is_changed_in_place(attach: Attach) -> None:
    timeline = a_session(
        attach,
        a_timeline([a_title("Sunset Boulevard", FIRST, inputs={"Size": 0.05})]),
    )

    result = edit_title(title="Sunset Boulevard", params={"Size": 0.08})

    assert result["ok"] is True
    assert input_of(items_on(timeline)[0], "Size") == 0.08
    assert text_of(items_on(timeline)[0]) == "Sunset Boulevard"
    assert result["edited"] == ["Size"]


def test_words_and_params_change_together_and_both_are_reported(attach: Attach) -> None:
    started = a_title("Sunset Boulevar", FIRST, inputs={"Size": 0.05}, defaults={"Size": 0.1})
    timeline = a_session(attach, a_timeline([started]))

    result = edit_title(
        title="Sunset Boulevar",
        text="Sunset Boulevard",
        params={"Size": 0.08},
    )

    assert result["edited"] == ["StyledText", "Size"]
    assert result["was"]["text"] == "Sunset Boulevar"
    assert result["title"]["text"] == "Sunset Boulevard"
    assert result["title"]["params"]["values"]["Size"] == 0.08
    assert input_of(items_on(timeline)[0], "Size") == 0.08


def test_the_neighbouring_titles_keep_their_words_and_are_counted(attach: Attach) -> None:
    timeline = a_session(
        attach,
        a_timeline(
            [
                a_title("Sunset Boulevar", FIRST),
                a_title("Bass — Ana Ruiz", SECOND),
                a_title("Night Ferry", 5400),
            ]
        ),
    )

    result = edit_title(title="Sunset Boulevar", text="Sunset Boulevard")

    assert [text_of(item) for item in items_on(timeline)] == [
        "Sunset Boulevard",
        "Bass — Ana Ruiz",
        "Night Ferry",
    ]
    assert result["other_titles_unchanged"] == 2


def test_the_edited_title_keeps_the_fade_the_apply_gave_it(attach: Attach) -> None:
    timeline = a_session(attach, a_timeline([a_title("Sunset Boulevar", FIRST)]))
    spline = items_on(timeline)[0].comps[0].BezierSpline()
    spline[0] = 0.0
    spline[24] = 1.0
    items_on(timeline)[0].comps[0].tools[0].Opacity1 = spline

    edit_title(title="Sunset Boulevar", text="Sunset Boulevard")

    kept = items_on(timeline)[0].comps[0].tools[0].animated["Opacity1"]
    assert kept.keyframes == {0.0: 0.0, 24.0: 1.0}


def test_a_record_frame_picks_between_two_titles_that_read_the_same(attach: Attach) -> None:
    timeline = a_session(
        attach,
        a_timeline([a_title("Intro", FIRST), a_title("Intro", SECOND)]),
    )

    result = edit_title(title="Intro", at=SECOND, text="Reprise")

    assert [text_of(item) for item in items_on(timeline)] == ["Intro", "Reprise"]
    assert result["title"]["record"]["frames"] == SECOND


def test_a_record_frame_alone_names_a_title_whose_words_are_hard_to_type(
    attach: Attach,
) -> None:
    timeline = a_session(attach, a_timeline([a_title("Bass — Ana Ruiz", FIRST)]))

    result = edit_title(at=FIRST, text="Bass — Ana Ruíz")

    assert result["ok"] is True
    assert text_of(items_on(timeline)[0]) == "Bass — Ana Ruíz"


def test_a_named_timeline_is_edited_rather_than_the_current_one(attach: Attach) -> None:
    other = a_timeline([a_title("Night Fery", FIRST)], name="holiday-gig v1")
    current = a_timeline([a_title("Sunset Boulevard", FIRST)])
    a_session(attach, timelines=[current, other])

    edit_title(title="Night Fery", text="Night Ferry", timeline="holiday-gig v1")

    assert text_of(items_on(other)[0]) == "Night Ferry"
    assert text_of(items_on(current)[0]) == "Sunset Boulevard"


# --- edit_title: what it refuses --------------------------------------------------------


def test_words_that_match_nothing_are_refused_with_the_track_listed(attach: Attach) -> None:
    a_session(attach, a_timeline([a_title("Sunset Boulevard", FIRST)]))

    failure = error(edit_title(title="Sunset Blvd", text="Sunset Boulevard"))

    assert failure["code"] == "title_not_found"
    assert [one["text"] for one in failure["detail"]["on_track"]] == ["Sunset Boulevard"]


def test_two_titles_reading_the_same_are_refused_rather_than_guessed_between(
    attach: Attach,
) -> None:
    timeline = a_session(
        attach,
        a_timeline([a_title("Intro", FIRST), a_title("Intro", SECOND)]),
    )

    failure = error(edit_title(title="Intro", text="Reprise"))

    assert failure["code"] == "title_not_found"
    assert [one["record"]["frames"] for one in failure["detail"]["matching"]] == [FIRST, SECOND]
    assert [text_of(item) for item in items_on(timeline)] == ["Intro", "Intro"]


def test_a_timeline_with_no_titles_track_refuses_the_edit(attach: Attach) -> None:
    a_session(attach, a_timeline())

    failure = error(edit_title(title="Sunset Boulevard", text="Sunset Blvd"))

    assert failure["code"] == "title_not_found"
    assert "no Titles track" in failure["cause"]


def test_an_edit_that_changes_nothing_is_refused(attach: Attach) -> None:
    a_session(attach)

    failure = error(edit_title(title="Sunset Boulevard"))

    assert failure["code"] == "invalid_request"
    assert "change nothing" in failure["cause"]


def test_an_edit_that_names_no_title_is_refused(attach: Attach) -> None:
    a_session(attach)

    failure = error(edit_title(text="Sunset Blvd"))

    assert failure["code"] == "invalid_request"
    assert "not told which title" in failure["cause"]


def test_the_words_cannot_be_smuggled_in_as_a_param(attach: Attach) -> None:
    timeline = a_session(attach)

    failure = error(edit_title(title="Sunset Boulevard", params={"StyledText": "Sunset Blvd"}))

    assert failure["code"] == "invalid_request"
    assert text_of(items_on(timeline)[0]) == "Sunset Boulevard"


def test_a_param_value_a_fusion_input_cannot_take_is_refused_before_anything_is_written(
    attach: Attach,
) -> None:
    timeline = a_session(attach)

    failure = error(
        edit_title(title="Sunset Boulevard", text="Sunset Blvd", params={"Size": {"x": 1}})
    )

    assert failure["code"] == "invalid_request"
    # Refused before Resolve was touched: the text it would also have written is unchanged.
    assert text_of(items_on(timeline)[0]) == "Sunset Boulevard"


def test_a_write_that_is_taken_and_dropped_is_reported_rather_than_believed(
    attach: Attach,
) -> None:
    a_session(
        attach,
        a_timeline(
            [a_title("Sunset Boulevar", FIRST, inputs={"Size": 0.05}, refuses={"Size"})],
        ),
    )

    failure = error(edit_title(title="Sunset Boulevar", params={"Size": 0.08}))

    assert failure["code"] == "title_edit_failed"
    assert failure["detail"]["strayed"] == [{"input": "Size", "wrote": 0.08, "reads": 0.05}]


def test_an_input_id_this_template_has_not_got_is_reported(attach: Attach) -> None:
    a_session(
        attach,
        a_timeline([a_title("Sunset Boulevard", FIRST, refuses={"StyleSize"})]),
    )

    failure = error(edit_title(title="Sunset Boulevard", params={"StyleSize": 0.08}))

    assert failure["code"] == "title_edit_failed"
    assert failure["detail"]["strayed"] == [
        {"input": "StyleSize", "wrote": 0.08, "reads": None},
    ]


def test_instances_sharing_one_fusion_comp_are_refused_by_reading_the_neighbour(
    attach: Attach,
) -> None:
    shared = FakeFusionComp([FakeFusionTool(inputs={"StyledText": "Sunset Boulevar"})])
    a_session(
        attach,
        a_timeline(
            [
                a_title("Sunset Boulevar", FIRST, comp=shared),
                a_title("Sunset Boulevar", SECOND, comp=shared),
            ]
        ),
    )

    failure = error(edit_title(at=FIRST, text="Sunset Boulevard"))

    assert failure["code"] == "title_edit_failed"
    assert "share one Fusion comp" in failure["fix"]
    assert failure["detail"]["changed"] == [
        {
            "position": 2,
            "input": "StyledText",
            "was": "Sunset Boulevar",
            "reads": "Sunset Boulevard",
        }
    ]


def test_a_clip_on_the_track_that_is_not_a_title_cannot_be_edited(attach: Attach) -> None:
    a_session(attach, a_timeline([FakeTimelineItem("B-roll.mp4", FIRST, 200)]))

    failure = error(edit_title(at=FIRST, text="Sunset Boulevard"))

    assert failure["code"] == "title_edit_failed"
    assert "is not a Text+ title" in failure["cause"]


# --- apply, then fix the typo -----------------------------------------------------------


def test_a_typo_is_fixed_on_an_applied_track_without_re_applying_the_file(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """The ticket's whole story end to end: apply the file, then fix one word by hand."""
    cut = FakeTimeline(
        "sunset-set v3",
        start_frame=3600,
        end_frame=20000,
        video=[FakeTrack("Video 1", [FakeTimelineItem("C0012.mp4", 3600, 12000)])],
        markers={0: {"color": "Blue", "name": "sunset-boulevard", "note": "", "duration": 1}},
    )
    attach(
        studio(
            timeline=cut,
            timelines=[cut],
            pool=media_pool({"Titles": [text_plus_template("Song Title")]}),
        )
    )
    events = [
        {"id": "t01", "kind": "title", "text": "Sunset Boulevar", "in": 240, "out": 720},
        {"id": "t02", "kind": "title", "text": "Night Ferry", "in": 960, "out": 1320},
    ]
    doc = {
        "schema": 1,
        "templates": {"title": {"clip": "Song Title"}},
        "songs": [{"key": "sunset-boulevard", "events": events}],
    }
    path = tmp_path / "titles.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    assert apply_titles(str(path))["ok"] is True
    placed = items_on(cut)

    result = edit_title(title="Sunset Boulevar", text="Sunset Boulevard")

    assert result["ok"] is True
    assert [text_of(item) for item in items_on(cut)] == ["Sunset Boulevard", "Night Ferry"]
    # Same two clips, never cleared and re-placed — and the apply's fade is still on them.
    assert items_on(cut) == placed
    assert cut.deleted_clips == []
