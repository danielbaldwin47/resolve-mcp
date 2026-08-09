"""The two devices the #46 director recut needed and the schema could not say (#141).

1. **Black on V1** — a gap entry in ``segments``: ``{"id": "g001", "gap": 58}``. It takes
   record time and places nothing, so a cut can stage a false ending or open cold.
2. **Overlays above V2** — an optional ``track`` on an overlay, so two inserts can share
   frames on different layers instead of colliding on one.

Both verify at the fake tier, which is where every decision in them lives: the rules are
pure functions over a document, and the build's half is what it hands ``AppendToTimeline``.
The one thing no seam here covers is whether Resolve honours a ``recordFrame`` past the end
of a track's media rather than sliding the clip back — that is the live smoke AC, and
``_verify``'s read-back is what would catch it going wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.cut.validate import (
    overlay_positions,
    positions,
    total_frames,
    validate_structure,
)
from resolve_mcp.findings import Finding
from resolve_mcp.tools.cut import (
    build_timeline,
    get_cut_schema,
    swap_take,
    virtual_transcript,
)

from .conftest import Attach
from .cutfile import a_cut, a_pool, built, empty_project, placements, valid_doc

# --- helpers ------------------------------------------------------------------------------


def rules(findings: list[Finding]) -> list[str]:
    return [finding.rule for finding in findings]


def pure_doc(**overrides: Any) -> dict[str, Any]:
    """A cut with no media pool behind it — enough for every structural rule.

    No ``audio`` block, because W2 compares the master span against the V1 total and every
    gap in this file changes that total; a warning about the mix would drown the one under
    test.
    """
    doc: dict[str, Any] = {
        "schema": 1,
        "timeline": {"name": "sunset-set", "fps": 59.94},
        "sources": {
            "gtr_close": {"clip": "C0012.mp4"},
            "keys_wide": {"clip": "C0013.mp4"},
            "broll_pan": {"clip": "C0014.mp4"},
        },
        "segments": [
            {"id": "s001", "source": "gtr_close", "in": 1000, "out": 1200},
            {"id": "s002", "source": "keys_wide", "in": 2000, "out": 2200},
        ],
    }
    doc.update(overrides)
    return doc


def with_gap(gap: int = 58, at: int = 1, **overrides: Any) -> dict[str, Any]:
    """:func:`pure_doc` with ``gap`` frames of black spliced in at index ``at``."""
    doc = pure_doc(**overrides)
    doc["segments"].insert(at, {"id": "g001", "gap": gap})
    return doc


def overlay(id: str = "b01", **fields: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": id,
        "source": "broll_pan",
        "in": 1200,
        "out": 1300,
        "over": {"segment": "s001", "offset": 24},
    }
    entry.update(fields)
    return entry


# --- gaps: shape --------------------------------------------------------------------------


def test_a_gap_between_two_segments_is_a_valid_cut() -> None:
    assert validate_structure(with_gap()) == []


def test_a_gap_needs_no_source_alias() -> None:
    """Black plays no clip, so a gap is not an alias use and E4 has nothing to resolve."""
    doc = with_gap()
    doc["sources"].pop("broll_pan")

    assert rules(validate_structure(doc)) == []


def test_a_gap_may_carry_a_note() -> None:
    doc = with_gap()
    doc["segments"][1]["note"] = "false ending"

    assert validate_structure(doc) == []


def test_a_gap_duration_must_be_an_integer() -> None:
    assert rules(validate_structure(with_gap(gap="58"))) == ["E1"]  # type: ignore[arg-type]


def test_a_gap_must_run_at_least_one_frame() -> None:
    """Zero frames of black is no black at all — the same emptiness E3 refuses in a range."""
    findings = validate_structure(with_gap(gap=0))

    # E3 plus the same zero-length W1 cascade a zero-length segment already produces.
    assert rules(findings) == ["E3", "W1"]
    assert findings[0].id == "g001"


def test_a_gap_may_not_also_be_a_shot() -> None:
    """Half a gap and half a segment is an author mid-edit, not a device."""
    doc = with_gap()
    doc["segments"][1]["source"] = "gtr_close"

    findings = validate_structure(doc)
    assert rules(findings) == ["E1"]
    assert "source" in findings[0].message


def test_a_gap_may_not_carry_alternates() -> None:
    doc = with_gap()
    doc["segments"][1]["alternates"] = [{"source": "gtr_close", "in": 1, "out": 59}]

    assert rules(validate_structure(doc)) == ["E1"]


def test_a_cut_of_nothing_but_black_is_refused() -> None:
    """A timeline of pure black is not a cut; ``segments`` needs picture in it."""
    findings = validate_structure(pure_doc(segments=[{"id": "g001", "gap": 58}]))

    assert rules(findings) == ["E1"]
    assert "picture" in findings[0].message


def test_a_gap_shares_the_one_id_namespace() -> None:
    doc = with_gap()
    doc["segments"][1]["id"] = "s001"

    assert rules(validate_structure(doc)) == ["E2"]


def test_a_flash_of_black_trips_the_flash_guard() -> None:
    """W1 reads a two-frame gap the way it reads a two-frame shot: usually a typo."""
    findings = validate_structure(with_gap(gap=2))

    assert rules(findings) == ["W1"]
    assert findings[0].id == "g001"


# --- gaps: layout -------------------------------------------------------------------------


def test_a_gap_pushes_everything_after_it_later() -> None:
    """The whole point: record positions stay computed, and black takes record time."""
    assert positions(with_gap()) == {"s001": (0, 200), "g001": (200, 58), "s002": (258, 200)}


def test_a_leading_gap_opens_the_cut_on_black() -> None:
    assert positions(with_gap(at=0))["s001"] == (58, 200)


def test_a_gap_counts_toward_the_total() -> None:
    assert total_frames(with_gap()) == 458
    assert total_frames(pure_doc()) == 400


def test_the_master_audio_span_is_measured_against_the_black_too() -> None:
    """W2 compares the mix to the V1 span, and black is part of that span."""
    doc = with_gap(audio={"source": "master_mix", "in": 0, "out": 458})
    doc["sources"]["master_mix"] = {"clip": "sunset-master.wav"}

    assert validate_structure(doc) == []


# --- gaps under overlays --------------------------------------------------------------------


def test_an_overlay_can_be_anchored_over_black() -> None:
    """#46's opening: a moving-cam shot on V2 bridging black and the first picture."""
    doc = with_gap(at=0, overlays=[overlay(over={"segment": "g001", "offset": 10})])

    assert validate_structure(doc) == []
    assert overlay_positions(doc) == {"b01": (10, 100)}


def test_an_overlay_over_black_may_run_on_into_the_picture() -> None:
    doc = with_gap(at=0, overlays=[overlay(out=1400, over={"segment": "g001", "offset": 10})])

    assert validate_structure(doc) == []
    assert overlay_positions(doc)["b01"] == (10, 200)


def test_an_offset_past_the_end_of_the_black_is_refused() -> None:
    """E9's offset leg reads a gap's duration exactly as it reads a segment's."""
    doc = with_gap(at=0, overlays=[overlay(over={"segment": "g001", "offset": 58})])

    findings = validate_structure(doc)
    assert rules(findings) == ["E9"]
    assert "58 frames" in findings[0].message


# --- ending on black ------------------------------------------------------------------------


def test_trailing_black_that_nothing_covers_is_warned_about() -> None:
    """Nothing is appended over a bare tail gap, so the built timeline just ends early."""
    findings = validate_structure(with_gap(at=2))

    assert rules(findings) == ["W8"]
    assert "58" in findings[0].message


def test_trailing_black_under_an_overlay_is_not_warned_about() -> None:
    """#46's ending: inserts on V2 are what make the black after the crash exist at all."""
    doc = with_gap(at=2, overlays=[overlay(over={"segment": "s002", "offset": 150})])

    assert validate_structure(doc) == []


def test_trailing_black_under_the_master_mix_is_not_warned_about() -> None:
    """The ordinary concert shape: the music plays on over the black, so the black is real.

    A timeline ends at its last item on *any* track, and the mix is appended to A1 for its
    own declared length — so a cut whose master audio runs past the last picture ends on
    black whether or not anything is on V2.
    """
    doc = with_gap(at=2, audio={"source": "master_mix", "in": 0, "out": 458})
    doc["sources"]["master_mix"] = {"clip": "sunset-master.wav"}

    assert validate_structure(doc) == []


def test_a_mix_that_stops_at_the_last_picture_does_not_make_the_black_real() -> None:
    """The warning turns on what outlives the picture, not on whether audio exists at all."""
    doc = with_gap(at=2, audio={"source": "master_mix", "in": 0, "out": 400})
    doc["sources"]["master_mix"] = {"clip": "sunset-master.wav"}

    # W2 too: the 400-frame mix no longer matches the 458-frame V1 span.
    assert rules(validate_structure(doc)) == ["W2", "W8"]


# --- overlays above V2 ------------------------------------------------------------------------


def test_an_overlay_track_must_be_an_integer() -> None:
    assert rules(validate_structure(pure_doc(overlays=[overlay(track="2")]))) == ["E1"]


def test_v1_is_not_an_overlay_track() -> None:
    """V1 belongs to the segments; an overlay claiming it would collide with the cut."""
    findings = validate_structure(pure_doc(overlays=[overlay(track=1)]))

    assert rules(findings) == ["E1"]
    assert "2" in findings[0].message


def test_an_out_of_range_overlay_track_is_refused() -> None:
    """A typo'd index would have the build silently add every track below it."""
    assert rules(validate_structure(pure_doc(overlays=[overlay(track=99)]))) == ["E1"]


def test_two_overlays_on_one_track_may_not_share_frames() -> None:
    doc = pure_doc(overlays=[overlay("b01"), overlay("b02", **{"in": 1, "out": 101})])

    assert rules(validate_structure(doc)) == ["E10"]


def test_two_overlays_on_different_tracks_may_share_frames() -> None:
    """E10 exists because one track cannot hold two clips at once; two tracks can."""
    doc = pure_doc(
        overlays=[overlay("b01"), overlay("b02", track=3, **{"in": 1, "out": 101})]
    )

    assert validate_structure(doc) == []


# --- the build --------------------------------------------------------------------------------


def a_built_gap(gap: int = 58, at: int = 1, **overrides: Any) -> dict[str, Any]:
    """:func:`cutfile.valid_doc` — three shots over a master mix — with black spliced in."""
    doc = valid_doc(**overrides)
    doc["segments"].insert(at, {"id": "g001", "gap": gap})
    doc["audio"]["out"] = total_frames(doc)
    return doc


def test_a_gap_leaves_a_hole_in_the_v1(attach: Attach, tmp_path: Path) -> None:
    """The device: 58 frames of nothing between the second and third shots."""
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, a_built_gap(at=2)))

    assert result["ok"] is True
    assert placements(built(resolve, "sunset-set v1")) == [
        ("C0012.mp4", 0, 100),
        ("C0031.mp4", 100, 80),
        ("C0012.mp4", 238, 60),
    ]


def test_a_gap_appends_nothing(attach: Attach, tmp_path: Path) -> None:
    """Black is the absence of a clip, not a clip: nothing is sent for it."""
    pool = a_pool()
    attach(empty_project(pool))

    build_timeline(a_cut(tmp_path, a_built_gap(at=2)))

    assert [append["recordFrame"] for append in pool.appends if append["mediaType"] == 1] == [
        0,
        100,
        238,
    ]


def test_the_report_counts_the_black(attach: Attach, tmp_path: Path) -> None:
    attach(empty_project(a_pool()))

    result = build_timeline(a_cut(tmp_path, a_built_gap(at=2)))

    assert result["placed"]["segments"] == 3
    assert result["placed"]["gaps"] == 1


def test_a_swap_after_a_gap_finds_the_shot_the_gap_moved(
    attach: Attach, tmp_path: Path
) -> None:
    """One derivation of the layout, so a swap reads the record the build wrote."""
    doc = a_built_gap(at=1)
    doc["segments"][2]["alternates"] = [{"source": "keys_wide", "in": 4500, "out": 4580}]
    resolve = empty_project(a_pool())
    attach(resolve)
    cut_file = a_cut(tmp_path, doc)
    build_timeline(cut_file)

    result = swap_take(cut_file, "s002", 2, timeline="sunset-set v1")

    # The swap finds its shot by record frame alone, so it only succeeds if the layout it
    # computed put s002 at 158 — where the gap moved it — rather than at 100.
    assert result["ok"] is True
    assert result["changed"] is True


def test_black_cannot_be_swapped(attach: Attach, tmp_path: Path) -> None:
    """A gap has no source and no selector; asking to swap one is a mistake, not a no-op."""
    resolve = empty_project(a_pool())
    attach(resolve)
    cut_file = a_cut(tmp_path, a_built_gap(at=1))
    build_timeline(cut_file)

    result = swap_take(cut_file, "g001", 2, timeline="sunset-set v1")

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert "g001" in result["error"]["cause"]


def test_an_overlay_lands_on_the_track_it_names(attach: Attach, tmp_path: Path) -> None:
    resolve = empty_project(a_pool())
    attach(resolve)
    doc = valid_doc(
        overlays=[
            {
                "id": "b01",
                "source": "gtr_close",
                "in": 3000,
                "out": 3030,
                "over": {"segment": "s002", "offset": 10},
                "track": 3,
            }
        ]
    )

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is True
    assert placements(built(resolve, "sunset-set v1"), "video", 3) == [("C0012.mp4", 110, 30)]
    assert placements(built(resolve, "sunset-set v1"), "video", 2) == []
    assert result["placed"]["overlays"] == 1


def test_the_director_recut_shape_builds(attach: Attach, tmp_path: Path) -> None:
    """#46 in miniature: open on black under a V2 bridge, and end on black under an insert.

    This is the whole ticket in one document — neither half of it could be written before,
    and the two interact: the ending insert is only legal because the trailing gap is part
    of the cut's total span.
    """
    doc = valid_doc(
        overlays=[
            {
                "id": "bridge",
                "source": "keys_wide",
                "in": 6000,
                "out": 6100,
                "over": {"segment": "g_open", "offset": 0},
            },
            {
                "id": "insert",
                "source": "keys_wide",
                "in": 7000,
                "out": 7040,
                "over": {"segment": "g_end", "offset": 0},
            },
        ]
    )
    doc["segments"].insert(0, {"id": "g_open", "gap": 40, "note": "cold open"})
    doc["segments"].append({"id": "g_end", "gap": 58, "note": "end on black"})
    doc["audio"]["out"] = total_frames(doc)
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is True
    assert result["placed"]["gaps"] == 2
    # V1 opens 40 frames late and stops at 280; V2 covers the black at both ends.
    assert placements(built(resolve, "sunset-set v1"))[0] == ("C0012.mp4", 40, 100)
    assert placements(built(resolve, "sunset-set v1"), "video", 2) == [
        ("C0031.mp4", 0, 100),
        ("C0031.mp4", 280, 40),
    ]


# --- reading the cut back ------------------------------------------------------------------------


def test_black_between_two_shots_of_one_source_is_not_a_jump_cut(tmp_path: Path) -> None:
    """W7 exists to catch two takes butt-joined; 58 frames of black is not that join."""
    doc = valid_doc()
    doc["segments"] = [
        {"id": "s001", "source": "gtr_close", "in": 1000, "out": 1100},
        {"id": "g001", "gap": 58},
        {"id": "s002", "source": "gtr_close", "in": 2500, "out": 2560},
    ]
    doc["audio"]["out"] = total_frames(doc)

    result = virtual_transcript(a_cut(tmp_path, doc))

    assert result["ok"] is True
    assert result["seams"] == []
    assert [read["id"] for read in result["segments"]] == ["s001", "s002"]


def test_a_reading_places_words_after_the_black(tmp_path: Path) -> None:
    """The read-back positions come from ``positions``, so black moves the words too."""
    doc = valid_doc()
    doc["segments"].insert(1, {"id": "g001", "gap": 58})
    doc["audio"]["out"] = total_frames(doc)

    result = virtual_transcript(a_cut(tmp_path, doc))

    assert result["ok"] is True
    assert result["segments"][1]["at"]["frames"] == 158


@pytest.mark.parametrize("field", ['"gap"', '"track"'])
def test_the_served_example_shows_the_new_field(field: str) -> None:
    """An agent authors against the served example alone; an unshown field is unusable."""
    result = get_cut_schema()

    assert result["ok"] is True
    assert field in result["annotated_example"]
