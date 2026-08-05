"""The media pool tools, called in-process against the fake Resolve seam."""

from __future__ import annotations

import json
from pathlib import Path

from resolve_mcp.tools.media import (
    import_media,
    inspect_clip,
    list_media,
    organize_media,
    relink_media,
    set_clip_metadata,
)

from .conftest import Attach
from .fakes import FakeMediaPoolItem, media_pool, studio

AUDIO_MAPPING = json.dumps(
    {
        "embedded_audio_channels": 2,
        "linked_audio": {"1": {"channels": 8, "offset": -100, "path": "D:/audio/master.wav"}},
        "track_mapping": {"1": {"channel_idx": [1, 3], "mute": False, "type": "Stereo"}},
    }
)


def a_file(tmp_path: Path, name: str, content: bytes = b"media") -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def a_clip(path: Path | str, **properties: str) -> FakeMediaPoolItem:
    return FakeMediaPoolItem(Path(path).name, str(path), properties)


# --- import ----------------------------------------------------------------------------


def test_imports_a_video_and_a_png_sequence_into_a_named_bin(
    attach: Attach, tmp_path: Path
) -> None:
    video = a_file(tmp_path, "C0012.mp4")
    for index in range(1, 4):
        a_file(tmp_path, f"titles/song_{index:04d}.png")
    pool = media_pool()
    attach(studio(pool=pool))

    result = import_media(
        paths=[str(video)],
        bin="Concert/Titles",
        sequences=[
            {
                "path": str(tmp_path / "titles" / "song_%04d.png"),
                "start_index": 1,
                "end_index": 3,
            }
        ],
    )

    assert result["ok"] is True
    assert result["bin"] == "Concert/Titles"
    assert [clip["name"] for clip in result["imported"]] == ["C0012.mp4", "song_0001.png"]
    assert result["imported"][1]["frames"] == 3
    assert result["not_imported"] == []
    root = pool.GetRootFolder()
    titles = root.GetSubFolderList()[0].GetSubFolderList()[0]
    assert titles.GetName() == "Titles"
    assert len(titles.GetClipList()) == 2


def test_import_applies_the_still_duration_workaround_to_image_media(
    attach: Attach, tmp_path: Path
) -> None:
    still = a_file(tmp_path, "lower-third.png")
    pool = media_pool()
    attach(studio(pool=pool))

    result = import_media(paths=[str(still)])

    assert result["ok"] is True
    assert result["imported"][0]["still_duration_workaround"] is True
    clip = pool.GetRootFolder().GetClipList()[0]
    assert clip.property_writes == [("Out", "0")]


def test_import_does_not_touch_the_out_point_of_a_video(attach: Attach, tmp_path: Path) -> None:
    pool = media_pool()
    attach(studio(pool=pool))

    result = import_media(paths=[str(a_file(tmp_path, "C0012.mp4"))])

    assert result["imported"][0]["still_duration_workaround"] is False
    assert pool.GetRootFolder().GetClipList()[0].property_writes == []


def test_import_reports_the_paths_resolve_refused_and_keeps_the_rest(
    attach: Attach, tmp_path: Path
) -> None:
    pool = media_pool()
    attach(studio(pool=pool))
    missing = str(tmp_path / "gone.mp4")

    result = import_media(paths=[str(a_file(tmp_path, "C0012.mp4")), missing])

    assert result["ok"] is True
    assert [clip["name"] for clip in result["imported"]] == ["C0012.mp4"]
    assert result["not_imported"] == [missing]


def test_a_sequence_that_landed_is_never_reported_as_refused(
    attach: Attach, tmp_path: Path
) -> None:
    """Resolve names the imported clip, not the %0Nd pattern — matching on it would guess."""
    for index in range(1, 3):
        a_file(tmp_path, f"titles/song_{index:04d}.png")
    attach(studio(pool=media_pool()))
    missing = str(tmp_path / "gone.mp4")

    result = import_media(
        paths=[missing],
        sequences=[
            {"path": str(tmp_path / "titles" / "song_%04d.png"), "start_index": 1, "end_index": 2}
        ],
    )

    assert result["ok"] is True
    assert result["not_imported"] == [missing]


def test_import_that_lands_nothing_is_a_failure_with_a_fix(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=media_pool()))

    result = import_media(paths=[str(tmp_path / "gone.mp4")])

    assert result["ok"] is False
    assert result["error"]["code"] == "import_failed"
    assert result["error"]["fix"]


def test_import_restores_the_folder_that_was_current_before_it(
    attach: Attach, tmp_path: Path
) -> None:
    pool = media_pool(bins={"Angles": []})
    attach(studio(pool=pool))
    before = pool.GetCurrentFolder()

    assert import_media(paths=[str(a_file(tmp_path, "C0012.mp4"))], bin="Angles")["ok"] is True

    assert pool.GetCurrentFolder() is before


# --- list ------------------------------------------------------------------------------


def test_list_summarises_clips_with_bin_paths_and_offline_state(
    attach: Attach, tmp_path: Path
) -> None:
    present = a_file(tmp_path, "C0012.mp4")
    moved = tmp_path / "C0099.mp4"  # never created: the file has moved away
    pool = media_pool(bins={"Angles/Cam A": [a_clip(present), a_clip(moved)]})
    attach(studio(pool=pool))

    result = list_media(bin="Angles")

    assert result["ok"] is True
    assert result["count"] == 2
    assert result["truncated"] is False
    by_name = {clip["name"]: clip for clip in result["clips"]}
    assert by_name["C0012.mp4"]["bin"] == "Angles/Cam A"
    assert by_name["C0012.mp4"]["offline"] is False
    assert by_name["C0012.mp4"]["frames"] == 100
    assert by_name["C0012.mp4"]["fps"] == 59.94
    assert by_name["C0099.mp4"]["offline"] is True


def test_list_filters_by_name_and_by_offline(attach: Attach, tmp_path: Path) -> None:
    pool = media_pool(
        bins={
            "": [a_clip(a_file(tmp_path, "C0012.mp4")), a_clip(a_file(tmp_path, "broll-pan.mov"))],
            "Angles": [a_clip(tmp_path / "C0099.mp4")],
        }
    )
    attach(studio(pool=pool))

    named = [clip["name"] for clip in list_media(name_contains="broll")["clips"]]
    offline = [clip["name"] for clip in list_media(offline_only=True)["clips"]]

    assert named == ["broll-pan.mov"]
    assert offline == ["C0099.mp4"]


def test_list_can_stay_in_one_bin(attach: Attach, tmp_path: Path) -> None:
    pool = media_pool(
        bins={"": [a_clip(a_file(tmp_path, "root.mp4"))], "Angles": [a_clip(tmp_path / "sub.mp4")]}
    )
    attach(studio(pool=pool))

    result = list_media(recursive=False)

    assert [clip["name"] for clip in result["clips"]] == ["root.mp4"]


def test_list_spills_the_full_listing_to_disk_past_the_cap(attach: Attach, tmp_path: Path) -> None:
    clips = [a_clip(tmp_path / f"C{index:04d}.mp4") for index in range(5)]
    attach(studio(pool=media_pool(bins={"Angles": clips})))

    result = list_media(bin="Angles", limit=2)

    assert result["ok"] is True
    assert result["count"] == 5
    assert result["truncated"] is True
    assert len(result["clips"]) == 2
    spilled = Path(result["spilled_to"])
    assert spilled.exists()
    assert len(json.loads(spilled.read_text(encoding="utf-8"))["clips"]) == 5


def test_a_bin_really_named_master_is_still_addressable(attach: Attach, tmp_path: Path) -> None:
    """The root is called Master, so a leading Master is only dropped when it is the root."""
    pool = media_pool(bins={"Master": [a_clip(a_file(tmp_path, "C0012.mp4"))], "": []})
    attach(studio(pool=pool))

    result = list_media(bin="Master", recursive=False)

    assert result["ok"] is True
    assert result["bin"] == "Master"
    assert [clip["name"] for clip in result["clips"]] == ["C0012.mp4"]


def test_list_of_an_unknown_bin_names_the_bins_that_exist(attach: Attach) -> None:
    attach(studio(pool=media_pool(bins={"Angles/Cam A": []})))

    result = list_media(bin="angles")

    assert result["ok"] is False
    assert result["error"]["code"] == "bin_not_found"
    assert "Angles/Cam A" in result["error"]["fix"]


def test_media_tools_need_a_project(attach: Attach) -> None:
    attach(studio(project=None))

    result = list_media()

    assert result["ok"] is False
    assert result["error"]["code"] == "no_project_open"
    assert result["error"]["fix"]


# --- inspect ---------------------------------------------------------------------------


def test_inspect_returns_metadata_audio_mapping_markers_and_bounds(
    attach: Attach, tmp_path: Path
) -> None:
    clip = FakeMediaPoolItem(
        "C0012.mp4",
        str(a_file(tmp_path, "C0012.mp4")),
        properties={"Start": "0", "End": "99", "Frames": "100", "FPS": "59.94"},
        metadata={"Description": "guitar close", "Scene": "set 1"},
        markers={96.0: {"color": "Green", "duration": 1.0, "note": "fill", "name": "Marker 1"}},
        audio_mapping=AUDIO_MAPPING,
        mark_in_out={"video": {"in": 12, "out": 134}},
    )
    attach(studio(pool=media_pool(bins={"Angles": [clip]})))

    result = inspect_clip("C0012.mp4")

    assert result["ok"] is True
    assert result["clip"]["bin"] == "Angles"
    assert result["clip"]["offline"] is False
    assert result["metadata"]["Description"] == "guitar close"
    assert result["properties"]["Resolution"] == "1920x1080"
    assert result["audio_mapping"]["linked_audio"]["1"]["offset"] == -100
    assert result["markers"] == [
        {
            "frame": 96,
            "color": "Green",
            "duration": 1.0,
            "name": "Marker 1",
            "note": "fill",
            "custom_data": "",
        }
    ]
    bounds = result["bounds"]
    assert bounds["media"]["in"] == {
        "frames": 0,
        "seconds": 0.0,
        "timecode": "00:00:00:00",
        "fps": 59.94,
    }
    assert bounds["media"]["out"]["frames"] == 100  # half-open: End + 1
    assert bounds["media"]["duration"]["frames"] == 100
    assert bounds["marks"]["video"]["in"]["frames"] == 12
    assert bounds["marks"]["video"]["out"]["timecode"] == "00:00:02:14"


def test_inspect_survives_a_clip_with_no_audio_mapping_or_markers(
    attach: Attach, tmp_path: Path
) -> None:
    clip = a_clip(a_file(tmp_path, "C0012.mp4"))
    attach(studio(pool=media_pool(bins={"": [clip]})))

    result = inspect_clip("C0012.mp4")

    assert result["ok"] is True
    assert result["audio_mapping"] is None
    assert result["markers"] == []
    assert result["clip"]["bin"] == ""


def test_inspect_of_an_unknown_clip_says_what_is_there(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=media_pool(bins={"Angles": [a_clip(a_file(tmp_path, "C0012.mp4"))]})))

    result = inspect_clip("C0013.mp4")

    assert result["ok"] is False
    assert result["error"]["code"] == "clip_not_found"
    assert result["error"]["fix"]


def test_inspect_refuses_an_ambiguous_clip_name(attach: Attach, tmp_path: Path) -> None:
    same = a_file(tmp_path, "C0012.mp4")
    attach(
        studio(pool=media_pool(bins={"Angles": [a_clip(same)], "Broll": [a_clip(same)]}))
    )

    result = inspect_clip("C0012.mp4")

    assert result["ok"] is False
    assert result["error"]["code"] == "ambiguous_clip"
    assert "Angles" in result["error"]["fix"]
    assert inspect_clip("C0012.mp4", bin="Broll")["ok"] is True


# --- metadata --------------------------------------------------------------------------


def test_metadata_batch_routes_each_field_by_what_the_clip_reports(
    attach: Attach, tmp_path: Path
) -> None:
    clip = a_clip(a_file(tmp_path, "C0012.mp4"))
    attach(studio(pool=media_pool(bins={"Angles": [clip]})))

    result = set_clip_metadata(
        [{"clip": "C0012.mp4", "fields": {"Description": "guitar close", "FPS": "59.94"}}]
    )

    assert result["ok"] is True
    assert result["results"] == [
        {
            "clip": "C0012.mp4",
            "bin": "Angles",
            "ok": True,
            "applied": {"Description": "metadata", "FPS": "clip_property"},
            "failed": {},
        }
    ]
    assert clip.metadata_writes == [("Description", "guitar close")]
    assert clip.property_writes == [("FPS", "59.94")]


def test_metadata_batch_reports_per_item_failures_without_losing_the_batch(
    attach: Attach, tmp_path: Path
) -> None:
    stubborn = a_clip(a_file(tmp_path, "C0012.mp4"))
    stubborn.refuse_metadata = {"Description"}
    willing = a_clip(a_file(tmp_path, "C0013.mp4"))
    attach(studio(pool=media_pool(bins={"Angles": [stubborn, willing]})))

    result = set_clip_metadata(
        [
            {"clip": "C0012.mp4", "fields": {"Description": "no"}},
            {"clip": "C0404.mp4", "fields": {"Description": "missing clip"}},
            {"clip": "C0013.mp4", "fields": {"Description": "yes"}},
        ]
    )

    assert result["ok"] is True
    first, second, third = result["results"]
    assert first["ok"] is False
    assert first["failed"]["Description"]
    assert second["ok"] is False
    assert second["error"]["code"] == "clip_not_found"
    assert third["ok"] is True
    assert willing.metadata_writes == [("Description", "yes")]


# --- organize --------------------------------------------------------------------------


def test_organize_creates_nested_bins_and_moves_clips_into_them(
    attach: Attach, tmp_path: Path
) -> None:
    clip = a_clip(a_file(tmp_path, "C0012.mp4"))
    pool = media_pool(bins={"": [clip]})
    attach(studio(pool=pool))

    result = organize_media(
        [
            {"op": "create_bin", "bin": "Concert/Angles"},
            {"op": "move_clips", "clips": ["C0012.mp4"], "to_bin": "Concert/Angles"},
        ]
    )

    assert result["ok"] is True
    assert [item["ok"] for item in result["results"]] == [True, True]
    angles = pool.GetRootFolder().GetSubFolderList()[0].GetSubFolderList()[0]
    assert [item.GetName() for item in angles.GetClipList()] == ["C0012.mp4"]
    assert pool.GetRootFolder().GetClipList() == []


def test_organize_treats_an_existing_bin_as_done(attach: Attach) -> None:
    attach(studio(pool=media_pool(bins={"Concert/Angles": []})))

    result = organize_media([{"op": "create_bin", "bin": "Concert/Angles"}])

    assert result["results"][0]["ok"] is True
    assert result["results"][0]["created"] is False


def test_organize_reports_a_bad_operation_per_item(attach: Attach) -> None:
    attach(studio(pool=media_pool(bins={"Angles": []})))

    result = organize_media([{"op": "burn_it_down", "bin": "Angles"}, {"op": "create_bin"}])

    assert result["ok"] is True
    assert [item["ok"] for item in result["results"]] == [False, False]
    assert result["results"][0]["error"]["code"] == "invalid_request"
    assert result["results"][1]["error"]["fix"]


def test_organize_move_reports_a_clip_it_cannot_find(attach: Attach) -> None:
    attach(studio(pool=media_pool(bins={"Angles": []})))

    result = organize_media([{"op": "move_clips", "clips": ["ghost.mp4"], "to_bin": "Angles"}])

    assert result["results"][0]["ok"] is False
    assert result["results"][0]["error"]["code"] == "clip_not_found"


# --- relink ----------------------------------------------------------------------------


def test_relink_brings_an_offline_clip_back_from_its_new_folder(
    attach: Attach, tmp_path: Path
) -> None:
    clip = a_clip(tmp_path / "old" / "C0012.mp4")  # the old path no longer exists
    moved = a_file(tmp_path, "new/C0012.mp4")
    attach(studio(pool=media_pool(bins={"Angles": [clip]})))
    assert list_media(offline_only=True)["count"] == 1

    result = relink_media(["C0012.mp4"], str(moved.parent))

    assert result["ok"] is True
    assert result["results"] == [
        {
            "clip": "C0012.mp4",
            "bin": "Angles",
            "ok": True,
            "file_path": str(moved),
            "offline": False,
            "was_offline": True,
        }
    ]
    assert list_media(offline_only=True)["count"] == 0


def test_relink_to_an_explicit_file_replaces_that_one_clip(attach: Attach, tmp_path: Path) -> None:
    clip = a_clip(tmp_path / "old" / "C0012.mp4")
    renamed = a_file(tmp_path, "new/C0012-take2.mp4")
    attach(studio(pool=media_pool(bins={"Angles": [clip]})))

    result = relink_media(["C0012.mp4"], str(renamed))

    assert result["ok"] is True
    assert result["results"][0]["file_path"] == str(renamed)
    assert result["results"][0]["offline"] is False


def test_relink_to_a_file_refuses_more_than_one_clip(attach: Attach, tmp_path: Path) -> None:
    renamed = a_file(tmp_path, "new/C0012-take2.mp4")
    pool = media_pool(bins={"Angles": [a_clip(tmp_path / "a.mp4"), a_clip(tmp_path / "b.mp4")]})
    attach(studio(pool=pool))

    result = relink_media(["a.mp4", "b.mp4"], str(renamed))

    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_request"
    assert result["error"]["fix"]


def test_relink_says_so_when_the_media_is_not_in_the_new_folder(
    attach: Attach, tmp_path: Path
) -> None:
    empty = tmp_path / "elsewhere"
    empty.mkdir()
    attach(studio(pool=media_pool(bins={"Angles": [a_clip(tmp_path / "old" / "C0012.mp4")]})))

    result = relink_media(["C0012.mp4"], str(empty))

    assert result["ok"] is True
    assert result["results"][0]["ok"] is False
    assert result["results"][0]["offline"] is True


def test_relink_needs_a_path_that_exists(attach: Attach, tmp_path: Path) -> None:
    attach(studio(pool=media_pool(bins={"Angles": [a_clip(tmp_path / "C0012.mp4")]})))

    result = relink_media(["C0012.mp4"], str(tmp_path / "nowhere"))

    assert result["ok"] is False
    assert result["error"]["code"] == "relink_failed"
    assert result["error"]["fix"]


# --- connection behaviour ---------------------------------------------------------------


def test_media_tools_survive_a_handle_that_dies_mid_call(attach: Attach, tmp_path: Path) -> None:
    clips: list[FakeMediaPoolItem] = [a_clip(a_file(tmp_path, "C0012.mp4"))]
    dying = studio(pool=media_pool(bins={"Angles": clips}))
    dying.die_after(1)
    attach(dying, studio(pool=media_pool(bins={"Angles": clips})))

    result = list_media(bin="Angles")

    assert result["ok"] is True
    assert result["count"] == 1


def test_a_media_pool_resolve_will_not_hand_over_is_reported(attach: Attach) -> None:
    attach(studio(pool=None))

    result = list_media()

    assert result["ok"] is False
    assert result["error"]["code"] == "media_pool_unavailable"
    assert result["error"]["fix"]
