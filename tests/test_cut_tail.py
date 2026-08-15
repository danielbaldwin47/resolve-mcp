"""The tail device: what the rules refuse, what the round trip does, what the build delivers.

Three seams, one device. The rules decide whether a tail can be placed at all (E1 shape,
E12 lengths); :mod:`resolve_mcp.resolve.tail` carries the cut out to OTIO and back and
decides what happens to the staging timeline; and the build decides that a cut with a tail
is round-tripped rather than delivered with a hard edge where its tail should be. The
document edit itself has no Resolve in it and is tested in ``test_cut_otio.py``.

The last of those is the one worth being explicit about. A dissolve that did not land and a
cut that never asked for one produce the *same* timeline, so nothing downstream can tell
them apart — which is exactly how the ending piece lost a round 0-3. Every refusal below
exists so that difference is loud somewhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.cut import otio as cut_otio
from resolve_mcp.cut import tail as tail_device
from resolve_mcp.cut.validate import validate_structure
from resolve_mcp.errors import BuildFailedError
from resolve_mcp.resolve import pool as mediapool
from resolve_mcp.resolve import tail as tail_route
from resolve_mcp.resolve import timeline as timeline_read
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools.cut import build_timeline, validate_cut

from .conftest import Attach
from .cutfile import (
    a_cut,
    a_pool,
    built,
    doc_with_alternates,
    empty_project,
    placements,
    valid_doc,
)
from .fakes import FakeTimeline

# --- the rules ------------------------------------------------------------------------------


def with_tail(**tail: Any) -> dict[str, Any]:
    """The shared three-shot cut, ending the way the corpus ends: dissolve plus a fade."""
    doc = valid_doc()
    doc["tail"] = {"type": "dissolve_to_black", "duration_frames": 40, **tail}
    return doc


def rules(doc: dict[str, Any]) -> list[str]:
    return [finding.rule for finding in validate_structure(doc)]


def messages(doc: dict[str, Any], rule: str) -> list[str]:
    return [finding.message for finding in validate_structure(doc) if finding.rule == rule]


def test_a_tail_the_corpus_would_author_validates_clean() -> None:
    """A dissolve shorter than the last shot, over a fade shorter than the mix."""
    assert rules(with_tail(audio_fade_frames=35)) == []


def test_a_cut_with_no_tail_is_still_the_ordinary_shape() -> None:
    """Every cut written before this device stays valid and stays a hard out."""
    assert rules(valid_doc()) == []
    assert tail_device.read(valid_doc()) is None


def test_a_tail_that_is_not_an_object_is_unreadable() -> None:
    doc = valid_doc()
    doc["tail"] = "dissolve_to_black"

    assert rules(doc) == ["E1"]


def test_an_unknown_tail_key_is_refused_rather_than_ignored() -> None:
    """``audio_fade`` for ``audio_fade_frames`` would build a dissolve over a hot mix."""
    doc = with_tail()
    doc["tail"]["audio_fade"] = 125

    assert "E1" in rules(doc)
    assert "audio_fade" in messages(doc, "E1")[0]


def test_a_tail_typo_does_not_silence_the_rest_of_the_document() -> None:
    """E1 stops the pass because the later rules stop being answerable — the tail is the leaf.

    Nothing outside E12 reads ``tail``, so a typo there leaves every other rule as answerable
    as it was, and suppressing them buys a validate/fix round trip per mistake. E12 itself
    stays silenced: its numbers come out of the block that failed.
    """
    doc = with_tail(audio_fade=125)
    doc["segments"][1]["id"] = "s001"

    assert rules(doc) == ["E1", "E2"]


def test_an_unknown_tail_type_is_refused() -> None:
    assert rules(with_tail(type="fade_up")) == ["E1"]


@pytest.mark.parametrize("field", ["duration_frames", "audio_fade_frames"])
def test_a_tail_frame_count_must_be_an_integer(field: str) -> None:
    assert "E1" in rules(with_tail(**{field: 5.9}))


def test_a_dissolve_needs_a_length() -> None:
    doc = valid_doc()
    doc["tail"] = {"type": "dissolve_to_black"}

    assert rules(doc) == ["E12"]


def test_a_dissolve_of_no_frames_is_refused() -> None:
    assert rules(with_tail(duration_frames=0)) == ["E12"]


def test_a_dissolve_longer_than_the_shot_it_fades_is_refused() -> None:
    """The transition reaches back into the last shot, so it cannot outrun it."""
    findings = messages(with_tail(duration_frames=60), "E12")

    assert findings
    assert "s003" in findings[0]


def test_a_cut_ending_on_black_has_nothing_to_dissolve() -> None:
    doc = with_tail()
    doc["segments"].append({"id": "g001", "gap": 30})

    findings = messages(doc, "E12")

    assert findings
    assert "g001" in findings[0]


def with_overlay(offset: int, frames: int, track: int = 2, **tail: Any) -> dict[str, Any]:
    """The corpus cut with one lower-third over ``s003``, the shot that ends it.

    ``s003`` runs frames 180-240 of a 240-frame cut, so an overlay's own ending is
    ``180 + offset + frames`` — which is what a dissolve has to agree with.
    """
    doc = with_tail(**tail)
    doc["overlays"] = [
        {
            "id": "b01",
            "source": "keys_wide",
            "in": 6000,
            "out": 6000 + frames,
            "over": {"segment": "s003", "offset": offset},
            "track": track,
        }
    ]
    return doc


def test_an_overlay_stopping_inside_the_dissolve_is_refused_by_the_rules() -> None:
    """The injector's own precondition, asked before Resolve is written rather than after.

    An overlay that is opaque over part of the ramp and then stops lets the picture
    underneath come back partway through the fade — a second ending. The build refuses it,
    but only once the shots are on a staging timeline and the delivery name holds nothing,
    so the rule has to catch it first.
    """
    findings = messages(with_overlay(offset=20, frames=30), "E12")

    assert findings
    assert "b01" in findings[0]
    assert "230" in findings[0] and "200" in findings[0]


def test_an_overlay_gone_before_the_dissolve_starts_validates_clean() -> None:
    """The device covers the frames it covers: a layer gone by then is not in it."""
    assert rules(with_overlay(offset=0, frames=20)) == []


def test_an_overlay_ending_the_cut_alongside_v1_must_carry_the_dissolve_too() -> None:
    """Both layers reach the last frame, so both are faded — and both need the frames for it."""
    findings = messages(with_overlay(offset=30, frames=30), "E12")

    assert findings
    assert "b01" in findings[0]
    assert "V2" in findings[0]


def test_an_overlay_hanging_off_the_end_of_the_cut_is_e9_alone() -> None:
    """E9 reports the layout and the pass keeps going, so this rule does see documents it
    can say nothing honest about.

    With an overlay running past the last frame, the end of the picture would be measured
    off a layer V1 does not reach: every other overlay judged against the wrong window, and
    V1's own refusal — the one the injector would raise — never mirrored at all.
    """
    assert rules(with_overlay(offset=50, frames=30)) == ["E9"]


def test_an_overlay_ending_the_cut_and_long_enough_to_fade_validates_clean() -> None:
    """A full-width lower-third over the last shot is legal, and the injector fades it too."""
    assert rules(with_overlay(offset=0, frames=60)) == []


def test_a_hard_out_carries_no_dissolve_length() -> None:
    doc = valid_doc()
    doc["tail"] = {"type": "hard_to_black", "duration_frames": 40}

    assert rules(doc) == ["E12"]


def test_a_hard_out_that_only_fades_the_mix_is_the_corpus_shape() -> None:
    """Two of the five surveyed deliverables cut hard and let the mix fade under black."""
    doc = valid_doc()
    doc["tail"] = {"type": "hard_to_black", "audio_fade_frames": 100}

    assert rules(doc) == []


def test_an_audio_fade_needs_a_mix_to_fade() -> None:
    doc = with_tail(audio_fade_frames=100)
    del doc["audio"]

    assert "E12" in rules(doc)


def test_an_audio_fade_longer_than_the_mix_is_refused() -> None:
    findings = messages(with_tail(audio_fade_frames=240), "E12")

    assert findings
    assert "240" in findings[0]


def test_an_audio_fade_of_no_frames_is_refused() -> None:
    assert "E12" in rules(with_tail(audio_fade_frames=0))


def test_the_dry_run_reports_a_bad_tail_before_resolve_is_touched(
    attach: Attach, tmp_path: Path
) -> None:
    attach(empty_project(a_pool()))

    result = validate_cut(a_cut(tmp_path, with_tail(duration_frames=600)))

    assert result["ok"] is True
    assert [error["rule"] for error in result["errors"]] == ["E12"]


# --- the build ------------------------------------------------------------------------------


def kinds(doc: dict[str, Any]) -> list[tuple[str, int]]:
    """Every transition in a document Resolve handed back, as ``(kind, length in frames)``."""
    return [(item["kind"], item["in_offset"]) for item in cut_otio.transitions(doc)]


def test_a_tailed_cut_lands_under_its_version_name(attach: Attach, tmp_path: Path) -> None:
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    assert result["ok"] is True, result.get("error")
    assert result["timeline"]["name"] == "sunset-set v1"
    assert placements(built(resolve, "sunset-set v1")) == [
        ("C0012.mp4", 0, 100),
        ("C0031.mp4", 100, 80),
        ("C0012.mp4", 180, 60),
    ]


def test_the_build_reports_the_tail_it_placed(attach: Attach, tmp_path: Path) -> None:
    attach(empty_project(a_pool()))

    result = build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    document = result["tail"].pop("document")
    result["tail"].pop("confirmed")
    assert result["tail"] == {
        "type": "dissolve_to_black",
        "duration_frames": 40,
        "audio_fade_frames": 35,
        "video_tracks": ["Video 1"],
        "audio_tracks": ["Audio 1"],
        "route": "otio_round_trip",
    }
    # The edited document is the evidence for what the tail did, so the report says where it is.
    assert Path(document).suffix == ".otio"


def test_the_transitions_are_on_the_timeline_that_came_back(
    attach: Attach, tmp_path: Path
) -> None:
    """Read off the imported cut, because the API has no getter for a transition at all."""
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    landed = built(resolve, "sunset-set v1")
    assert [(one["kind"], one["in_offset"]) for one in landed.transitions] == [
        ("Video", 40),
        ("Audio", 35),
    ]


def test_the_staging_timeline_is_gone_once_the_import_lands(
    attach: Attach, tmp_path: Path
) -> None:
    resolve = empty_project(a_pool())
    attach(resolve)

    build_timeline(a_cut(tmp_path, with_tail()))

    project = resolve.current_project
    names = [
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)
    ]
    assert names == ["sunset-set v1"]


def test_a_mix_outliving_the_picture_still_gets_its_dissolve(
    attach: Attach, tmp_path: Path
) -> None:
    """The ordinary concert shape, and the one the live build failed on before the fix."""
    doc = with_tail(audio_fade_frames=35)
    doc["audio"]["out"] = 300  # 60 frames of black under the mix after the last shot
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is True, result.get("error")
    assert result["tail"]["video_tracks"] == ["Video 1"]
    assert [
        (one["kind"], one["in_offset"]) for one in built(resolve, "sunset-set v1").transitions
    ] == [("Video", 40), ("Audio", 35)]


def test_a_cut_with_no_tail_never_takes_the_round_trip(attach: Attach, tmp_path: Path) -> None:
    """The route costs an export and an import; a hard out must not pay for them."""
    pool = a_pool()
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, valid_doc()))

    assert result["tail"] is None
    assert pool.timeline_imports == []


def test_a_build_that_cannot_export_refuses_and_names_the_staging_cut(
    attach: Attach, tmp_path: Path
) -> None:
    """The shots exist somewhere; a caller told only 'export failed' would never find them."""
    pool = a_pool()
    pool.new_timeline_export_result = False
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_tail()))

    assert result["ok"] is False
    assert result["error"]["detail"]["staging_timeline"] == "sunset-set v1 (tail staging)"
    assert "sunset-set v1 (tail staging)" in result["error"]["fix"]


def test_a_build_whose_import_is_renamed_refuses(attach: Attach, tmp_path: Path) -> None:
    """A tail that landed under a name nobody asked for is a cut nobody will look at."""
    pool = a_pool()
    resolve = empty_project(pool)
    attach(resolve)
    pool.imported_timeline = FakeTimeline("sunset-set v1 (1)")

    result = build_timeline(a_cut(tmp_path, with_tail()))

    assert result["ok"] is False
    assert "sunset-set v1 (1)" in result["error"]["cause"]


def test_an_import_that_dropped_the_tail_is_refused(attach: Attach, tmp_path: Path) -> None:
    """The point of the whole device: a lost dissolve must not look like a cut without one."""
    pool = a_pool()
    pool.import_drops_transitions = True
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    assert result["ok"] is False
    assert "kept 0 of the 1 video fade(s)" in result["error"]["cause"]


def test_the_confirmed_tail_is_read_off_the_cut_that_landed(
    attach: Attach, tmp_path: Path
) -> None:
    """Not the document that was imported — the timeline Resolve made out of it."""
    attach(empty_project(a_pool()))

    result = build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    assert [(one["kind"], one["name"]) for one in result["tail"]["confirmed"]] == [
        ("Video", "Cross Dissolve"),
        ("Audio", "Cross Fade 0 dB"),
    ]


def test_the_exported_document_is_the_one_that_gets_imported(
    attach: Attach, tmp_path: Path
) -> None:
    """The edit is on disk, not in memory — an import reads the file, never the caller."""
    pool = a_pool()
    attach(empty_project(pool))

    build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    path, _ = pool.timeline_imports[-1]
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    assert kinds(document) == [("Video", 40), ("Audio", 35)]


# --- what the round trip must not leave behind ------------------------------------------------


def timeline_names(resolve: Any) -> list[str]:
    project = resolve.current_project
    return [
        project.GetTimelineByIndex(index).GetName()
        for index in range(1, project.GetTimelineCount() + 1)
    ]


def test_a_hard_out_that_fades_nothing_builds_without_the_round_trip(
    attach: Attach, tmp_path: Path
) -> None:
    """Nothing to inject: the round trip would spend an export and an import on no device."""
    pool = a_pool()
    resolve = empty_project(pool)
    attach(resolve)
    doc = valid_doc()
    doc["tail"] = {"type": "hard_to_black"}

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is True, result.get("error")
    assert pool.timeline_imports == []
    assert timeline_names(resolve) == ["sunset-set v1"]
    # The cut file did ask for a tail, so the report says which one and that it took no route.
    assert result["tail"] == {
        "type": "hard_to_black",
        "duration_frames": 0,
        "audio_fade_frames": 0,
        "video_tracks": [],
        "audio_tracks": [],
        "route": "direct",
        "confirmed": [],
    }


def test_an_overlay_ending_inside_the_dissolve_never_reaches_resolve(
    attach: Attach, tmp_path: Path
) -> None:
    """An opaque V2 over the ramp is a second ending, and no fade this module places covers it.

    The injector refuses it, but only with the shots already on a staging timeline — E12 has
    the same answer from the cut file alone, so the refusal costs nothing and leaks nothing.
    """
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_overlay(offset=20, frames=30)))

    assert result["ok"] is False
    assert [error["rule"] for error in result["error"]["detail"]["errors"]] == ["E12"]
    assert timeline_names(resolve) == []


def test_an_overlay_that_ends_the_cut_is_faded_with_the_shot_under_it(
    attach: Attach, tmp_path: Path
) -> None:
    """The case E12 lets through: both layers reach the last frame, so both take a dissolve."""
    resolve = empty_project(a_pool())
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_overlay(offset=0, frames=60)))

    assert result["ok"] is True, result.get("error")
    assert result["tail"]["video_tracks"] == ["Video 1", "Video 2"]
    assert timeline_names(resolve) == ["sunset-set v1"]


def test_a_round_trip_that_slid_the_shots_is_refused(attach: Attach, tmp_path: Path) -> None:
    """The cut that ships is the imported one, so it is the one the placements are read on."""
    pool = a_pool()
    pool.import_slides_clips = 5
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    assert result["ok"] is False
    assert "did not land where the cut puts them" in result["error"]["cause"]
    assert timeline_names(resolve) == ["sunset-set v1 (tail staging)"]


def test_an_import_that_chose_its_own_start_timecode_is_not_a_slid_cut(
    attach: Attach, tmp_path: Path
) -> None:
    """Placement is an offset from the timeline's own first frame, never an absolute frame.

    The round trip delivers a *second* timeline, and Resolve is entitled to start what it
    imports where it likes — an hour in where the staging cut began at zero. Every shot is
    still exactly where the cut puts it; only the frame numbers moved. Compared absolutely,
    that reads as the whole cut sliding, and the build deletes a correct import over it.
    """
    pool = a_pool()
    pool.import_starts_at = 86_400
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    assert result["ok"] is True, result.get("error")
    assert placements(built(resolve, "sunset-set v1")) == [
        ("C0012.mp4", 86_400, 100),
        ("C0031.mp4", 86_500, 80),
        ("C0012.mp4", 86_580, 60),
    ]
    assert timeline_names(resolve) == ["sunset-set v1"]


def test_a_selector_is_attached_on_the_started_over_cut_the_import_made(
    attach: Attach, tmp_path: Path
) -> None:
    """Takes are found by record frame, so they follow the import's start rather than staging's."""
    doc = doc_with_alternates()
    doc["tail"] = {"type": "dissolve_to_black", "duration_frames": 40}
    pool = a_pool()
    pool.import_starts_at = 86_400
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, doc))

    assert result["ok"] is True, result.get("error")
    assert result["placed"]["selectors"] == 2


def test_a_confirm_failure_takes_the_import_down_with_it(
    attach: Attach, tmp_path: Path
) -> None:
    """A failed import left under the delivery name collides with the advice to rename staging."""
    pool = a_pool()
    pool.import_drops_transitions = True
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_tail()))

    assert result["ok"] is False
    assert timeline_names(resolve) == ["sunset-set v1 (tail staging)"]
    assert "imported_timeline" not in result["error"]["detail"]


def test_a_fade_the_import_shortened_is_refused(attach: Attach, tmp_path: Path) -> None:
    """Resolve trims a dissolve to the handles the shot has, and the count still matches."""
    pool = a_pool()
    pool.import_trims_transitions_to = 12
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    assert result["ok"] is False
    assert "came back 12 frames long rather than the 40" in result["error"]["cause"]
    assert timeline_names(resolve) == ["sunset-set v1 (tail staging)"]


def test_an_import_under_another_name_leaves_both_timelines_named(
    attach: Attach, tmp_path: Path
) -> None:
    """When the failed import cannot be deleted, the error is the only map of the project."""
    pool = a_pool()
    resolve = empty_project(pool)
    attach(resolve)
    pool.imported_timeline = FakeTimeline("sunset-set v1 (1)")

    result = build_timeline(a_cut(tmp_path, with_tail()))

    assert result["ok"] is False
    assert result["error"]["detail"]["imported_timeline"] == "sunset-set v1 (1)"
    assert "sunset-set v1 (tail staging)" in result["error"]["cause"]
    # Never blamed on Resolve alone: an import is always asked for a name the project has free.
    assert "either the project already held that name" in result["error"]["cause"]


def test_an_import_the_project_does_not_hold_is_a_build_failure(
    attach: Attach, tmp_path: Path
) -> None:
    """Looking the imported cut up is one more call that can fail, and it fails structured."""
    pool = a_pool()
    resolve = empty_project(pool)
    attach(resolve)
    pool.imported_timeline = FakeTimeline("sunset-set v1")

    result = build_timeline(a_cut(tmp_path, with_tail()))

    assert result["ok"] is False
    assert result["error"]["code"] == "build_failed"
    assert "the project holds no timeline of that name" in result["error"]["cause"]
    assert result["error"]["detail"]["staging_timeline"] == "sunset-set v1 (tail staging)"


def test_the_edit_lands_beside_the_export_rather_than_over_it(
    attach: Attach, tmp_path: Path
) -> None:
    """Resolve holds what it exported open (#26), so the edited document takes a fresh name."""
    pool = a_pool()
    attach(empty_project(pool))

    result = build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    edited = Path(result["tail"]["document"])
    assert edited.stem.endswith(" (tail)")
    original = edited.with_name(f"{edited.stem[: -len(' (tail)')]}{edited.suffix}")
    assert original.exists()
    # Untouched: the export is the cut before the tail, the edit is the cut after it.
    assert kinds(json.loads(original.read_text(encoding="utf-8"))) == []


def test_a_held_open_export_does_not_stop_the_tail(attach: Attach, tmp_path: Path) -> None:
    """The failure #26 records: the file Resolve wrote cannot be rewritten while it holds it."""
    guard = tmp_path / "read-only.probe"
    guard.write_text("x", encoding="utf-8")
    guard.chmod(0o444)
    try:
        guard.write_text("y", encoding="utf-8")
    except OSError:
        pass
    else:
        pytest.skip("this filesystem lets a read-only file be rewritten")

    pool = a_pool()
    pool.new_timeline_locks_exports = True
    resolve = empty_project(pool)
    attach(resolve)

    result = build_timeline(a_cut(tmp_path, with_tail(audio_fade_frames=35)))

    assert result["ok"] is True, result.get("error")
    assert timeline_names(resolve) == ["sunset-set v1"]


def test_a_retry_after_a_failed_round_trip_stages_under_a_free_name(
    attach: Attach, tmp_path: Path
) -> None:
    """The staging name is invisible to the version scan, so a retry would ask for it again."""
    pool = a_pool()
    pool.new_timeline_export_result = False
    resolve = empty_project(pool)
    attach(resolve)
    cut_file = a_cut(tmp_path, with_tail())

    assert build_timeline(cut_file)["ok"] is False
    pool.new_timeline_export_result = True
    result = build_timeline(cut_file)

    assert result["ok"] is True, result.get("error")
    assert pool.created_timelines[-1] == "sunset-set v1 (tail staging) v2"
    assert timeline_names(resolve) == ["sunset-set v1 (tail staging)", "sunset-set v1"]


# --- the landed cut's contract ----------------------------------------------------------------


def a_staging_cut(resolve: Any, tmp_path: Path) -> tail_route.Staging:
    """A built cut with no tail on it, stood up as the staging timeline for a round trip.

    The shots are real (fake-tier real): the round trip exports this timeline, so a hand-made
    stub would export as a document with nothing in it to fade.
    """
    assert build_timeline(a_cut(tmp_path, valid_doc()))["ok"] is True
    connection = get_connection()
    project = timeline_read.open_project(connection)
    return tail_route.Staging(
        project, mediapool.media_pool(connection), built(resolve, "sunset-set v1"), "sunset-set v1"
    )


def test_the_round_trip_leaves_staging_standing_for_the_caller_to_release(
    attach: Attach, tmp_path: Path
) -> None:
    """The cut that ships is one nobody has checked yet, so the fallback outlives the import."""
    resolve = empty_project(a_pool())
    attach(resolve)
    staging = a_staging_cut(resolve, tmp_path)

    landed = tail_route.materialise(
        get_connection(), staging, "sunset-set v2", tail_device.Tail("dissolve_to_black", 40, 0)
    )

    assert timeline_names(resolve) == ["sunset-set v1", "sunset-set v2"]
    assert landed.timeline.GetName() == "sunset-set v2"
    assert landed.applied["route"] == "otio_round_trip"

    landed.release()

    assert timeline_names(resolve) == ["sunset-set v2"]


def test_a_caller_that_refuses_the_import_still_has_its_shots_on_staging(
    attach: Attach, tmp_path: Path
) -> None:
    """The whole point of holding the delete: a refusal leaves the cut somewhere findable."""
    resolve = empty_project(a_pool())
    attach(resolve)
    staging = a_staging_cut(resolve, tmp_path)
    landed = tail_route.materialise(
        get_connection(), staging, "sunset-set v2", tail_device.Tail("dissolve_to_black", 40, 0)
    )

    error = landed.refuse("the round trip moved the shots")

    assert isinstance(error, BuildFailedError)
    assert "the round trip moved the shots" in error.cause
    assert error.detail["staging_timeline"] == "sunset-set v1"
    assert timeline_names(resolve) == ["sunset-set v1"]
