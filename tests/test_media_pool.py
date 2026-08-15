"""The media pool adapter — bin addressing, clip lookup, clip reading — at the fake seam.

The adapter half of what used to be one media test file. These tests are about
:mod:`resolve_mcp.resolve.pool`: which clip an address reaches, what a refusal says it
searched, and what a clip's properties are read as. They drive it through the thinnest
media tool that reaches the behaviour, because the fake substitutes the Resolve singleton
at the connection — so the envelope a refusal is shaped into is verified alongside the
lookup that produced it. What the six operations themselves do lives in
``test_media_tools.py``.
"""

from __future__ import annotations

from pathlib import Path

from resolve_mcp.resolve.pool import frame_bounds
from resolve_mcp.tools.media import (
    import_media,
    inspect_clip,
    list_media,
    organize_media,
    relink_media,
    set_clip_metadata,
)

from .conftest import Attach
from .fakes import FakeMediaPool, FakeMediaPoolItem, media_pool, studio


def a_file(tmp_path: Path, name: str, content: bytes = b"media") -> Path:
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def a_clip(path: Path | str, **properties: str) -> FakeMediaPoolItem:
    return FakeMediaPoolItem(Path(path).name, str(path), properties)


# --- offline and sequence identity -------------------------------------------------------


def test_an_imported_sequence_whose_frames_are_on_disk_is_not_offline(
    attach: Attach, tmp_path: Path
) -> None:
    """Resolve paths a sequence by its bracketed label (#85), which never exists on disk.

    Offline must be judged by the frames behind the label, or every freshly imported
    sequence reads offline and list_media(offline_only=True) sends a relink chase at
    healthy media.
    """
    for index in range(1, 4):
        a_file(tmp_path, f"seq/shot_{index:04d}.png")
    attach(studio(pool=media_pool()))

    result = import_media(
        sequences=[
            {"path": str(tmp_path / "seq" / "shot_%04d.png"), "start_index": 1, "end_index": 3}
        ]
    )

    assert result["ok"] is True
    assert result["imported"][0]["name"] == "shot_[0001-0003].png"
    assert result["imported"][0]["offline"] is False
    assert list_media(offline_only=True)["count"] == 0


def test_a_sequence_whose_frames_moved_away_is_offline(attach: Attach, tmp_path: Path) -> None:
    """The folder still exists — offline is judged by the first frame, not the folder."""
    (tmp_path / "seq").mkdir()
    clip = a_clip(tmp_path / "seq" / "shot_[0001-0003].png")
    attach(studio(pool=media_pool(bins={"Stills": [clip]})))

    assert [found["name"] for found in list_media(offline_only=True)["clips"]] == [
        "shot_[0001-0003].png"
    ]


# --- bin addressing ----------------------------------------------------------------------


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


# --- reaching the pool -------------------------------------------------------------------


def test_media_tools_need_a_project(attach: Attach) -> None:
    attach(studio(project=None))

    result = list_media()

    assert result["ok"] is False
    assert result["error"]["code"] == "no_project_open"
    assert result["error"]["fix"]


def test_a_media_pool_resolve_will_not_hand_over_is_reported(attach: Attach) -> None:
    attach(studio(pool=None))

    result = list_media()

    assert result["ok"] is False
    assert result["error"]["code"] == "media_pool_unavailable"
    assert result["error"]["fix"]


# --- clip lookup -------------------------------------------------------------------------


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


def test_a_root_clip_is_reachable_when_its_name_is_also_used_in_a_bin(
    attach: Attach, tmp_path: Path
) -> None:
    """#122: bin="" is the root folder alone — the only way to name the root copy."""
    same = a_file(tmp_path, "C0012.mp4")
    root_copy = a_clip(same, Description="at the root")
    binned_copy = a_clip(same, Description="filed away")
    attach(studio(pool=media_pool(bins={"": [root_copy], "Angles/Cam A": [binned_copy]})))

    at_root = inspect_clip("C0012.mp4", bin="")

    assert at_root["ok"] is True
    assert at_root["clip"]["bin"] == ""
    assert at_root["properties"]["Description"] == "at the root"
    binned = inspect_clip("C0012.mp4", bin="Angles/Cam A")
    assert binned["clip"]["bin"] == "Angles/Cam A"
    assert binned["properties"]["Description"] == "filed away"


def test_a_duplicated_name_still_refuses_without_a_bin(attach: Attach, tmp_path: Path) -> None:
    """Narrowing the root must not turn the whole-pool search into a first-match."""
    same = a_file(tmp_path, "C0012.mp4")
    attach(studio(pool=media_pool(bins={"": [a_clip(same)], "Angles": [a_clip(same)]})))

    result = inspect_clip("C0012.mp4")

    assert result["ok"] is False
    assert result["error"]["code"] == "ambiguous_clip"
    assert result["error"]["detail"]["bins"] == ["", "Angles"]
    assert 'bin=""' in result["error"]["fix"]  # the root has to be expressible
    assert 'bin="Angles"' in result["error"]["fix"]
    assert inspect_clip("C0012.mp4", bin="")["ok"] is True  # and every value offered works
    assert inspect_clip("C0012.mp4", bin="Angles")["ok"] is True


def test_every_bin_a_listing_reports_reads_the_same_clip_back(
    attach: Attach, tmp_path: Path
) -> None:
    """The bin value list_media reports round-trips verbatim, root included."""
    same = a_file(tmp_path, "C0012.mp4")
    attach(
        studio(
            pool=media_pool(
                bins={
                    "": [a_clip(same)],
                    "Angles": [a_clip(a_file(tmp_path, "C0013.mp4"))],
                    "Angles/Cam A": [a_clip(same)],
                }
            )
        )
    )

    listed = list_media()["clips"]

    assert len(listed) == 3
    for entry in listed:
        read_back = inspect_clip(entry["name"], bin=entry["bin"])
        assert read_back["ok"] is True, entry
        assert read_back["clip"]["bin"] == entry["bin"]


def test_a_named_bin_still_reaches_a_clip_in_its_subfolder(
    attach: Attach, tmp_path: Path
) -> None:
    """Only the root narrows: a named bin keeps searching what is nested inside it."""
    attach(studio(pool=media_pool(bins={"Angles/Cam A": [a_clip(a_file(tmp_path, "C0012.mp4"))]})))

    result = inspect_clip("C0012.mp4", bin="Angles")

    assert result["ok"] is True
    assert result["clip"]["bin"] == "Angles/Cam A"


def test_an_ambiguity_offers_only_the_bins_that_reach_one_clip(
    attach: Attach, tmp_path: Path
) -> None:
    """A bin whose subfolder holds another copy is not an answer, so it is not offered."""
    same = a_file(tmp_path, "C0012.mp4")
    attach(
        studio(
            pool=media_pool(bins={"Angles": [a_clip(same)], "Angles/Cam A": [a_clip(same)]})
        )
    )

    result = inspect_clip("C0012.mp4", bin="Angles")

    assert result["ok"] is False
    assert result["error"]["code"] == "ambiguous_clip"
    assert result["error"]["fix"] == (
        'Pass one of these to say which: bin="Angles/Cam A". Or bin="Angles" with '
        "recursive=false for the copy in that bin itself."
    )  # bin="Angles" alone reaches the nested copy too, so it is only offered shallow
    assert inspect_clip("C0012.mp4", bin="Angles/Cam A")["ok"] is True


def test_two_copies_in_one_bin_are_refused_with_advice_that_is_not_a_bin(
    attach: Attach, tmp_path: Path
) -> None:
    """When no bin can answer, the fix says so instead of naming a value that fails."""
    same = a_file(tmp_path, "C0012.mp4")
    attach(studio(pool=media_pool(bins={"Angles": [a_clip(same), a_clip(same)]})))

    result = inspect_clip("C0012.mp4", bin="Angles")

    assert result["ok"] is False
    assert result["error"]["code"] == "ambiguous_clip"
    assert "bin=" not in result["error"]["fix"]
    assert "recursive" not in result["error"]["fix"]  # a shallow lookup cannot answer either
    assert "Rename one in the Resolve GUI" in result["error"]["fix"]


def test_naming_the_root_master_reaches_the_root_clip_only(
    attach: Attach, tmp_path: Path
) -> None:
    """Master is the root's own name, so it narrows the same way the empty string does."""
    same = a_file(tmp_path, "C0012.mp4")
    attach(
        studio(
            pool=media_pool(
                bins={"": [a_clip(same, Description="at the root")], "Angles": [a_clip(same)]}
            )
        )
    )

    result = inspect_clip("C0012.mp4", bin="Master")

    assert result["ok"] is True
    assert result["clip"]["bin"] == ""
    assert result["properties"]["Description"] == "at the root"


def test_inspect_of_a_missing_root_clip_says_it_searched_the_root(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(pool=media_pool(bins={"Angles": [a_clip(a_file(tmp_path, "C0012.mp4"))]})))

    result = inspect_clip("C0012.mp4", bin="")

    assert result["ok"] is False
    assert result["error"]["code"] == "clip_not_found"
    assert result["error"]["detail"]["searched"] == "the media pool root"


# --- recursive -------------------------------------------------------------------------


def a_shallow_copy_pool(tmp_path: Path) -> FakeMediaPool:
    """The #134 shape: one clip name held by a bin *and* by a bin nested inside it."""
    same = a_file(tmp_path, "C0012.mp4")
    return media_pool(
        bins={
            "Angles": [a_clip(same, Description="in Angles")],
            "Angles/Cam A": [a_clip(same, Description="nested")],
        }
    )


def test_a_shallow_lookup_reaches_the_copy_held_by_the_named_bin_itself(
    attach: Attach, tmp_path: Path
) -> None:
    """#134: recursive=False says this bin alone, so the shadowed copy has an address."""
    attach(studio(pool=a_shallow_copy_pool(tmp_path)))

    result = inspect_clip("C0012.mp4", bin="Angles", recursive=False)

    assert result["ok"] is True
    assert result["clip"]["bin"] == "Angles"
    assert result["properties"]["Description"] == "in Angles"


def test_a_named_bin_stays_recursive_unless_recursive_is_passed(
    attach: Attach, tmp_path: Path
) -> None:
    """The flag is opt-in: the default keeps searching what is nested inside the bin."""
    attach(studio(pool=a_shallow_copy_pool(tmp_path)))

    assert inspect_clip("C0012.mp4", bin="Angles")["error"]["code"] == "ambiguous_clip"
    assert inspect_clip("C0012.mp4", bin="Angles/Cam A", recursive=False)["ok"] is True


def test_an_ambiguity_offers_the_shallow_form_when_that_is_what_reaches_a_copy(
    attach: Attach, tmp_path: Path
) -> None:
    """Every value the fix names must work — including the one that needs recursive=false."""
    attach(studio(pool=a_shallow_copy_pool(tmp_path)))

    fix = inspect_clip("C0012.mp4", bin="Angles")["error"]["fix"]

    assert 'bin="Angles/Cam A"' in fix
    assert "recursive=false" in fix
    assert inspect_clip("C0012.mp4", bin="Angles/Cam A")["ok"] is True
    assert inspect_clip("C0012.mp4", bin="Angles", recursive=False)["ok"] is True


def test_a_shallow_lookup_without_a_bin_stays_in_the_root(
    attach: Attach, tmp_path: Path
) -> None:
    """No bin means the root, and recursive=False narrows it the way list_media does."""
    same = a_file(tmp_path, "C0012.mp4")
    attach(
        studio(
            pool=media_pool(
                bins={
                    "": [a_clip(same, Description="at the root")],
                    "Angles": [a_clip(same, Description="filed away")],
                }
            )
        )
    )

    result = inspect_clip("C0012.mp4", recursive=False)

    assert result["ok"] is True
    assert result["clip"]["bin"] == ""
    assert result["properties"]["Description"] == "at the root"


def test_a_shallow_lookup_that_finds_nothing_says_it_did_not_descend(
    attach: Attach, tmp_path: Path
) -> None:
    """The refusal names what was searched, so the fix is to drop the flag, not the bin."""
    attach(studio(pool=media_pool(bins={"Angles/Cam A": [a_clip(a_file(tmp_path, "C0012.mp4"))]})))

    result = inspect_clip("C0012.mp4", bin="Angles", recursive=False)

    assert result["ok"] is False
    assert result["error"]["code"] == "clip_not_found"
    assert result["error"]["detail"]["searched"] == "the bin 'Angles' alone"


def test_metadata_writes_to_the_copy_a_shallow_item_names(
    attach: Attach, tmp_path: Path
) -> None:
    pool = a_shallow_copy_pool(tmp_path)
    attach(studio(pool=pool))

    result = set_clip_metadata(
        [
            {
                "clip": "C0012.mp4",
                "bin": "Angles",
                "recursive": False,
                "fields": {"Description": "the shallow copy"},
            }
        ]
    )

    assert result["results"][0]["ok"] is True
    assert result["results"][0]["bin"] == "Angles"
    angles = pool.GetRootFolder().GetSubFolderList()[0]
    assert angles.GetClipList()[0].property_writes == [("Description", "the shallow copy")]
    nested = angles.GetSubFolderList()[0].GetClipList()[0]
    assert nested.property_writes == []
    assert nested.metadata_writes == []


def test_organize_moves_the_copy_a_shallow_from_bin_names(
    attach: Attach, tmp_path: Path
) -> None:
    pool = a_shallow_copy_pool(tmp_path)
    attach(studio(pool=pool))

    result = organize_media(
        [
            {
                "op": "move_clips",
                "clips": ["C0012.mp4"],
                "from_bin": "Angles",
                "recursive": False,
                "to_bin": "Broll",
            }
        ]
    )

    assert result["results"][0]["ok"] is True
    root = pool.GetRootFolder()
    angles = root.GetSubFolderList()[0]
    broll = [sub for sub in root.GetSubFolderList() if sub.GetName() == "Broll"][0]
    assert angles.GetClipList() == []  # the copy Angles held itself
    assert [item.GetName() for item in broll.GetClipList()] == ["C0012.mp4"]
    assert len(angles.GetSubFolderList()[0].GetClipList()) == 1  # the nested copy stayed


def test_relink_takes_the_shallow_form_too(attach: Attach, tmp_path: Path) -> None:
    moved = a_file(tmp_path, "new/C0012.mp4")
    attach(
        studio(
            pool=media_pool(
                bins={
                    "Angles": [a_clip(tmp_path / "old" / "C0012.mp4")],
                    "Angles/Cam A": [a_clip(tmp_path / "old" / "C0012.mp4")],
                }
            )
        )
    )

    result = relink_media(["C0012.mp4"], str(moved.parent), bin="Angles", recursive=False)

    assert result["ok"] is True
    assert result["results"][0]["bin"] == "Angles"
    assert result["results"][0]["offline"] is False
    assert list_media(offline_only=True)["count"] == 1  # the nested copy is still offline


# --- frame bounds -----------------------------------------------------------------------


def test_frame_bounds_falls_back_to_duration_for_an_audio_only_clip() -> None:
    """Audio-only clips report Start/End/Frames as empty strings (#46, live-verified);
    Duration is the only length they carry, counted at the caller's rate because audio
    reports no rate of its own either."""
    reported = {
        "Type": "Audio",
        "FPS": "",
        "Frames": "",
        "Start": "",
        "End": "",
        "Duration": "01:26:38:09",
        "Sample Rate": "48000",
        "Audio Ch": "1",
    }

    assert frame_bounds(reported, fps=23.976) == (0, 124761)


def test_frame_bounds_with_no_rate_at_all_stays_unknown() -> None:
    reported = {"FPS": "", "Frames": "", "Start": "", "End": "", "Duration": "01:26:38:09"}

    assert frame_bounds(reported) == (None, None)


def test_the_duration_fallback_applies_whenever_the_out_point_is_unreadable() -> None:
    """A reported Start does not disqualify the fallback: only End/Frames being blank
    leaves the out point unknown, and Duration stands in from wherever start is."""
    reported = {"FPS": "", "Frames": "", "Start": "0", "End": "", "Duration": "01:26:38:09"}

    assert frame_bounds(reported, fps=23.976) == (0, 124761)


def test_a_zero_frame_duration_is_empty_media_not_unknown_media() -> None:
    """[0, 0) is a statement about the media; only an unparseable length is unknown."""
    reported = {"FPS": "", "Frames": "", "Start": "", "End": "", "Duration": "00:00:00:00"}

    assert frame_bounds(reported, fps=24.0) == (0, 0)
