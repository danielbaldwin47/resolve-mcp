"""Timeline interchange: export a cut to a file, import a file as a *new* cut.

Round-trip fidelity is a live question — only Resolve can say whether the OTIO it wrote
comes back as the same cut. What verifies here is every decision around that: which export
constant a format maps to on this build, where the file lands, and the rule that an import
never lands on a timeline the project already has.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.config import Config, set_config
from resolve_mcp.tools.timeline import export_timeline, import_timeline

from . import otio
from .conftest import Attach
from .fakes import (
    FakeTimeline,
    FakeTimelineItem,
    FakeTrack,
    media_pool,
    studio,
)


def a_cut(name: str = "sunset-set v3", fps: str = "59.94") -> FakeTimeline:
    return FakeTimeline(
        name,
        fps,
        video=[FakeTrack("Video 1", [FakeTimelineItem("C0012.mp4", 0, 60, source_start=1000)])],
        audio=[FakeTrack("Master", [FakeTimelineItem("master_mix.wav", 0, 60)])],
    )


def an_interchange_file(tmp_path: Path, name: str = "sunset-set v3.otio") -> Path:
    target = tmp_path / name
    target.write_text('{"OTIO_SCHEMA": "Timeline.1"}', encoding="utf-8")
    return target


# --- export ------------------------------------------------------------------------------


def test_export_writes_the_open_timeline_and_names_the_type_it_used(
    attach: Attach,
    tmp_path: Path,
) -> None:
    cut = a_cut()
    fake = studio(timeline=cut)
    attach(fake)
    set_config(Config.from_env({"RESOLVE_MCP_CACHE": str(tmp_path / "cache")}))

    result = export_timeline()

    assert result["ok"] is True
    assert result["timeline"] == "sunset-set v3"
    assert result["format"] == "otio"
    assert result["export_type"] == "EXPORT_OTIO"
    written = Path(result["path"])
    assert written.parent == tmp_path / "cache" / "interchange"
    assert written.suffix == ".otio"
    assert "sunset-set" in written.name
    assert written.exists()
    assert result["bytes"] > 0
    assert cut.exports[0][0] == str(written)
    assert cut.exports[0][1] == fake.EXPORT_OTIO  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("export_format", "suffix", "export_type"),
    [
        ("otio", ".otio", "EXPORT_OTIO"),
        ("fcpxml", ".fcpxml", "EXPORT_FCPXML_1_10"),
        ("drt", ".drt", "EXPORT_DRT"),
    ],
)
def test_export_maps_each_format_to_its_resolve_type(
    attach: Attach,
    export_format: str,
    suffix: str,
    export_type: str,
) -> None:
    cut = a_cut()
    attach(studio(timeline=cut))

    result = export_timeline(format=export_format)

    assert result["ok"] is True
    assert result["export_type"] == export_type
    assert Path(result["path"]).suffix == suffix


def test_export_falls_back_to_the_newest_fcpxml_this_build_has(attach: Attach) -> None:
    """FCPXML is versioned and Resolve gained versions over time.

    The newest one this build offers is the one to write; an older build must export rather
    than fail on a constant it never had.
    """
    attach(studio(timeline=a_cut(), export_types=("EXPORT_DRT", "EXPORT_FCPXML_1_8")))

    result = export_timeline(format="fcpxml")

    assert result["ok"] is True
    assert result["export_type"] == "EXPORT_FCPXML_1_8"


def test_export_uses_a_type_whose_value_is_zero(attach: Attach) -> None:
    """The constants are plain numbers, and the first of them is 0.

    A lookup that treated a falsy constant as "this build does not have it" would refuse the
    one format the build does support.
    """
    fake = studio(timeline=a_cut(), export_types=("EXPORT_OTIO",))
    attach(fake)

    result = export_timeline(format="otio")

    assert result["ok"] is True
    assert fake.EXPORT_OTIO == 0  # type: ignore[attr-defined]


def test_export_says_which_types_it_looked_for_when_the_build_has_none(attach: Attach) -> None:
    attach(studio(timeline=a_cut(), export_types=("EXPORT_OTIO", "EXPORT_DRT")))

    result = export_timeline(format="fcpxml")

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_export_failed"
    assert result["error"]["detail"]["tried"][0] == "EXPORT_FCPXML_1_10"
    assert result["error"]["fix"]


def test_export_rejects_a_format_it_does_not_know(attach: Attach) -> None:
    attach(studio(timeline=a_cut()))

    result = export_timeline(format="edl")

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert result["error"]["detail"]["available"] == ["otio", "fcpxml", "drt"]


def test_export_takes_a_named_timeline_and_says_when_it_is_not_there(attach: Attach) -> None:
    reference = FakeTimeline("sunset-set sync")
    attach(studio(timelines=[reference, a_cut()], timeline=a_cut()))

    found = export_timeline(timeline="sunset-set sync")
    missing = export_timeline(timeline="sunset-set v9")

    assert found["ok"] is True
    assert found["timeline"] == "sunset-set sync"
    assert missing["ok"] is False
    assert missing["error"]["code"] == "timeline_not_found"


def test_export_honours_an_explicit_path_and_corrects_the_suffix(
    attach: Attach,
    tmp_path: Path,
) -> None:
    attach(studio(timeline=a_cut()))
    asked = tmp_path / "handoff" / "for-the-editor.xml"

    result = export_timeline(format="fcpxml", path=str(asked))

    assert result["ok"] is True
    assert Path(result["path"]) == asked.with_suffix(".fcpxml")
    assert Path(result["path"]).exists()


def test_export_reports_a_refusal_from_resolve(attach: Attach) -> None:
    cut = a_cut()
    cut.export_result = False
    attach(studio(timeline=cut))

    result = export_timeline()

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_export_failed"
    assert result["error"]["fix"]


def test_export_does_not_report_a_file_that_was_never_written(attach: Attach) -> None:
    """Resolve can answer True and write nothing — a path in the reply would be a lie."""
    cut = a_cut()
    cut.export_writes_the_file = False
    attach(studio(timeline=cut))

    result = export_timeline()

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_export_failed"
    assert "wrote" in result["error"]["cause"]


def test_export_needs_a_project(attach: Attach) -> None:
    attach(studio(project=None))

    result = export_timeline()

    assert result["ok"] is False
    assert result["error"]["code"] == "no_project_open"


# --- import ------------------------------------------------------------------------------


def test_import_materialises_a_new_timeline(attach: Attach, tmp_path: Path) -> None:
    pool = media_pool()
    project_timelines: list[FakeTimeline | None] = [a_cut()]
    attach(studio(pool=pool, timelines=project_timelines, timeline=project_timelines[0]))
    source = an_interchange_file(tmp_path, "encore.otio")

    result = import_timeline(str(source))

    assert result["ok"] is True
    assert result["timeline"]["name"] == "encore"
    assert result["requested_name"] == "encore"
    assert result["renamed"] is False
    assert result["path"] == str(source)
    assert result["timeline"]["tracks"]["video"] == 1
    assert pool.timeline_imports[0][0] == str(source)


def test_import_never_lands_on_a_timeline_the_project_already_has(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """The name is made free *before* the call, not repaired afterwards.

    Nothing on record says what Resolve does with a colliding timelineName, and the one
    outcome that cannot be undone is it landing on the cut that is already there — so the
    name handed over is one no existing timeline answers to.
    """
    pool = media_pool()
    existing: list[FakeTimeline | None] = [a_cut("sunset-set v3"), a_cut("sunset-set v4")]
    attach(studio(pool=pool, timelines=existing, timeline=existing[0]))
    source = an_interchange_file(tmp_path, "sunset-set v3.otio")

    result = import_timeline(str(source))

    assert result["ok"] is True
    assert result["requested_name"] == "sunset-set v3"
    assert result["renamed"] is True
    assert result["timeline"]["name"] == "sunset-set v5"
    assert pool.timeline_imports[0][1]["timelineName"] == "sunset-set v5"


def test_import_versions_an_unversioned_name_that_collides(attach: Attach, tmp_path: Path) -> None:
    pool = media_pool()
    existing: list[FakeTimeline | None] = [FakeTimeline("sunset-set sync")]
    attach(studio(pool=pool, timelines=existing, timeline=existing[0]))
    source = an_interchange_file(tmp_path, "sunset-set sync.otio")

    result = import_timeline(str(source))

    assert result["ok"] is True
    assert result["timeline"]["name"] == "sunset-set sync v2"


def test_import_refuses_a_result_that_is_an_existing_timeline(
    attach: Attach,
    tmp_path: Path,
) -> None:
    """The AC as a runtime check: if Resolve hands back the old cut, that is not an import."""
    pool = media_pool()
    cut = a_cut("sunset-set v3")
    attach(studio(pool=pool, timelines=[cut], timeline=cut))
    pool.imported_timeline = cut
    source = an_interchange_file(tmp_path)

    result = import_timeline(str(source), name="encore")

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_import_failed"
    assert "sunset-set v3" in result["error"]["cause"]


def test_import_reports_a_file_that_is_not_there(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=media_pool(), timelines=[a_cut()]))

    result = import_timeline(str(tmp_path / "nothing-here.otio"))

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert "nothing-here.otio" in result["error"]["cause"]


def test_import_reports_a_refusal_from_resolve(attach: Attach, tmp_path: Path) -> None:
    pool = media_pool()
    attach(studio(pool=pool, timelines=[a_cut()]))
    pool.refuses_timeline_import = True

    result = import_timeline(str(an_interchange_file(tmp_path)))

    assert result["ok"] is False
    assert result["error"]["code"] == "timeline_import_failed"
    assert result["error"]["fix"]


def test_import_passes_the_media_options_through(attach: Attach, tmp_path: Path) -> None:
    pool = media_pool()
    attach(studio(pool=pool, timelines=[a_cut()]))
    source = an_interchange_file(tmp_path)

    result = import_timeline(
        str(source),
        name="encore v1",
        import_source_clips=False,
        source_media_path=str(tmp_path / "media"),
    )

    assert result["ok"] is True
    asked = pool.timeline_imports[0][1]
    assert asked["timelineName"] == "encore v1"
    assert asked["importSourceClips"] is False
    assert asked["sourceClipsPath"] == str(tmp_path / "media")


def test_import_leaves_source_clips_on_by_default(attach: Attach, tmp_path: Path) -> None:
    """An OTIO round trip that skipped its media would materialise a cut of nothing."""
    pool = media_pool()
    attach(studio(pool=pool, timelines=[a_cut()]))

    import_timeline(str(an_interchange_file(tmp_path)))

    asked = pool.timeline_imports[0][1]
    assert asked["importSourceClips"] is True
    assert "sourceClipsPath" not in asked


def test_import_needs_a_project(attach: Attach, tmp_path: Path) -> None:
    attach(studio(project=None))

    result = import_timeline(str(an_interchange_file(tmp_path)))

    assert result["ok"] is False
    assert result["error"]["code"] == "no_project_open"


# --- the hand-injected transition ----------------------------------------------------------
#
# What the live tier carries into Resolve. Whether Resolve reads these back as real
# dissolves is the live question; whether the document says what it means to say is this
# one, and it is the half that would otherwise be debugged by eye at the Resolve machine.


def a_document(*durations: int, rate: float = 24.0) -> dict[str, Any]:
    """An OTIO timeline of one video track holding clips of the given lengths."""
    return {
        "OTIO_SCHEMA": "Timeline.1",
        "name": "sunset-set v3",
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "children": [
                {
                    "OTIO_SCHEMA": "Track.1",
                    "kind": "Video",
                    "children": [
                        a_clip(f"C{index:04d}.mp4", frames, rate)
                        for index, frames in enumerate(durations)
                    ],
                },
                {"OTIO_SCHEMA": "Track.1", "kind": "Audio", "children": []},
            ],
        },
    }


def a_clip(name: str, frames: int, rate: float = 24.0) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Clip.1",
        "name": name,
        "source_range": {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": 0},
            "duration": {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": frames},
        },
    }


def a_gap(frames: int, rate: float = 24.0) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Gap.1",
        "name": "gap",
        "source_range": {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": 0},
            "duration": {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": frames},
        },
    }


def test_a_dissolve_lands_between_the_two_clips_it_joins() -> None:
    document = a_document(60, 90, rate=59.94)
    track = otio.video_tracks(document)[0]

    assert otio.inject_dissolve(track, frames=12) is True

    children = otio.children_of(track)
    assert [child["OTIO_SCHEMA"] for child in children] == ["Clip.1", "Transition.1", "Clip.1"]
    transition = children[1]
    assert transition["transition_type"] == otio.DISSOLVE
    assert transition["in_offset"] == {"OTIO_SCHEMA": "RationalTime.1", "rate": 59.94, "value": 12}
    assert transition["out_offset"] == transition["in_offset"]


def test_a_dissolve_is_not_cut_into_clips_too_short_to_carry_it() -> None:
    """A transition reaches into both neighbours; one that reaches past them is not a cut."""
    track = otio.video_tracks(a_document(4, 4))[0]

    assert otio.inject_dissolve(track, frames=6) is False
    assert len(otio.children_of(track)) == 2


def test_a_dissolve_needs_two_clips() -> None:
    track = otio.video_tracks(a_document(60))[0]

    assert otio.inject_dissolve(track) is False


def test_a_fade_to_black_uses_a_gap_the_cut_already_has() -> None:
    document = a_document(60, 60)
    track = otio.video_tracks(document)[0]
    track["children"].insert(1, a_gap(24))

    assert otio.inject_fade_to_black(track, frames=6) is True

    children = otio.children_of(track)
    assert [child["OTIO_SCHEMA"] for child in children] == [
        "Clip.1",
        "Transition.1",
        "Gap.1",
        "Clip.1",
    ]


def test_a_fade_to_black_makes_the_gap_when_the_cut_ends_on_a_clip() -> None:
    """The end of the cut is a clip↔gap boundary once there is a gap to fade into."""
    track = otio.video_tracks(a_document(60, 60))[0]

    assert otio.inject_fade_to_black(track, frames=6) is True

    children = otio.children_of(track)
    assert [child["OTIO_SCHEMA"] for child in children[-3:]] == ["Clip.1", "Transition.1", "Gap.1"]
    assert children[-1]["source_range"]["duration"]["value"] == 24


def test_an_empty_track_gets_no_fade() -> None:
    track = otio.video_tracks(a_document())[0]

    assert otio.inject_fade_to_black(track) is False
