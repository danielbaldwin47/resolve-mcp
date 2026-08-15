"""The six media operations, called in-process against the fake Resolve seam.

What each operation does with what the pool adapter hands it: import and its suggested
bins, listing and its spill, the inspect envelope, metadata routing, bin operations and
relink. The adapter's own behaviour — bin addressing, clip lookup, clip reading — is
verified in ``test_media_pool.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from resolve_mcp.tools.media import (
    import_media,
    inspect_clip,
    list_media,
    organize_media,
    relink_media,
    set_clip_metadata,
)

from .conftest import Attach
from .fakes import FakeFolder, FakeMediaPool, FakeMediaPoolItem, media_pool, studio

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
    assert [clip["name"] for clip in result["imported"]] == ["C0012.mp4", "song_[0001-0003].png"]
    assert result["imported"][1]["frames"] == 3
    assert result["not_imported"] == []
    root = pool.GetRootFolder()
    titles = root.GetSubFolderList()[0].GetSubFolderList()[0]
    assert titles.GetName() == "Titles"
    assert len(titles.GetClipList()) == 2


def test_an_imported_sequence_reports_the_same_type_as_moving_footage(
    attach: Attach, tmp_path: Path
) -> None:
    """Pins the fake's fidelity, not a decision in ``src``: ``type`` is a pass-through.

    Resolve Studio 21.0.3.7 types a sequence ``Video``, exactly as it types a movie (#85
    body, #95 probe), so the reply draws no distinction between them. The fake claimed
    ``Image Sequence`` until #97; asserting both clips together is what makes the *absence*
    of the distinction the thing under test.
    """
    video = a_file(tmp_path, "C0012.mp4")
    for index in range(1, 4):
        a_file(tmp_path, f"seq/shot_{index:04d}.png")
    attach(studio(pool=media_pool()))

    result = import_media(
        paths=[str(video)],
        sequences=[
            {"path": str(tmp_path / "seq" / "shot_%04d.png"), "start_index": 1, "end_index": 3}
        ],
    )

    assert [clip["type"] for clip in result["imported"]] == ["Video", "Video"]


def test_import_applies_the_still_duration_workaround_to_image_media(
    attach: Attach, tmp_path: Path
) -> None:
    still = a_file(tmp_path, "lower-third.png")
    pool = media_pool()
    attach(studio(pool=pool))

    result = import_media(paths=[str(still)])

    assert result["ok"] is True
    assert result["imported"][0]["still_duration_workaround"] is True
    clip = bin_named(pool, "04_Assets").GetClipList()[0]
    assert clip.property_writes == [("Out", "0")]


def test_import_does_not_touch_the_out_point_of_a_video(attach: Attach, tmp_path: Path) -> None:
    pool = media_pool()
    attach(studio(pool=pool))

    result = import_media(paths=[str(a_file(tmp_path, "C0012.mp4"))])

    assert result["imported"][0]["still_duration_workaround"] is False
    assert bin_named(pool, "02_Footage").GetClipList()[0].property_writes == []


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


# --- import: suggested bins (#94) ------------------------------------------------------


def bin_named(pool: FakeMediaPool, path: str) -> FakeFolder:
    """Walk a slash path down from the root, or fail the test that asked."""
    folder = pool.GetRootFolder()
    for segment in path.split("/"):
        matches = [sub for sub in folder.GetSubFolderList() if sub.GetName() == segment]
        assert matches, f"no bin {segment!r} under {folder.GetName()!r}"
        folder = matches[0]
    return folder


def test_no_bin_import_suggests_a_bin_per_media_type(attach: Attach, tmp_path: Path) -> None:
    """One call, three media types: each lands in its #57 category, created on demand."""
    video = a_file(tmp_path, "C0012.mp4")
    audio = a_file(tmp_path, "mix.wav")
    still = a_file(tmp_path, "poster.png")
    pool = media_pool()
    attach(studio(pool=pool))

    result = import_media(paths=[str(video), str(audio), str(still)])

    assert result["ok"] is True
    assert result["bins"] == ["02_Footage", "03_Audio", "04_Assets"]
    placed = {clip["name"]: (clip["bin"], clip["bin_source"]) for clip in result["imported"]}
    assert placed == {
        "C0012.mp4": ("02_Footage", "fallback"),
        "mix.wav": ("03_Audio", "media_type"),
        "poster.png": ("04_Assets", "media_type"),
    }
    assert [clip.GetName() for clip in bin_named(pool, "02_Footage").GetClipList()] == ["C0012.mp4"]
    assert [clip.GetName() for clip in bin_named(pool, "03_Audio").GetClipList()] == ["mix.wav"]
    assert [clip.GetName() for clip in bin_named(pool, "04_Assets").GetClipList()] == ["poster.png"]


def test_no_bin_video_lands_in_a_camera_bin_read_from_metadata(
    attach: Attach, tmp_path: Path
) -> None:
    """The camera leaf exists only after import: the model is clip metadata, not the path."""
    video = a_file(tmp_path, "C0012.mp4")
    clip = FakeMediaPoolItem("C0012.mp4", str(video), metadata={"Camera TC Type": "ILME-FX6V"})
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["ok"] is True
    assert result["bins"] == ["02_Footage/ILME-FX6V"]
    assert result["imported"][0]["bin"] == "02_Footage/ILME-FX6V"
    assert result["imported"][0]["bin_source"] == "camera_metadata"
    leaf = bin_named(pool, "02_Footage/ILME-FX6V")
    assert [found.GetName() for found in leaf.GetClipList()] == ["C0012.mp4"]


def test_no_bin_sequence_is_suggested_into_assets(attach: Attach, tmp_path: Path) -> None:
    for index in range(1, 4):
        a_file(tmp_path, f"seq/shot_{index:04d}.png")
    pool = media_pool()
    attach(studio(pool=pool))

    result = import_media(
        sequences=[
            {"path": str(tmp_path / "seq" / "shot_%04d.png"), "start_index": 1, "end_index": 3}
        ]
    )

    assert result["ok"] is True
    assert result["imported"][0]["bin"] == "04_Assets"
    assert result["imported"][0]["bin_source"] == "media_type"


def test_no_bin_graphics_are_suggested_into_assets(attach: Attach, tmp_path: Path) -> None:
    """Graphics ride with stills in #57: a .psd is an asset, not footage."""
    graphic = a_file(tmp_path, "lower-third.psd")
    attach(studio(pool=media_pool()))

    result = import_media(paths=[str(graphic)])

    assert result["ok"] is True
    assert result["imported"][0]["bin"] == "04_Assets"
    assert result["imported"][0]["bin_source"] == "media_type"


def test_a_camera_model_in_clip_properties_also_lands_the_leaf(
    attach: Attach, tmp_path: Path
) -> None:
    """The spec words the source as clip properties; metadata is the second look."""
    video = a_file(tmp_path, "C0500.mp4")
    clip = FakeMediaPoolItem("C0500.mp4", str(video), properties={"Camera TC Type": "ILCE-7M4"})
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["imported"][0]["bin"] == "02_Footage/ILCE-7M4"
    assert result["imported"][0]["bin_source"] == "camera_metadata"


def test_the_model_key_beats_the_manufacturer_key(attach: Attach, tmp_path: Path) -> None:
    """Real FX6 media fills both: "Camera TC Type" holds the model, "Camera Type" only
    the make (live probe, 2026-08-07) — the make must never name the bin when the model
    is readable, or every Sony camera collapses into one 02_Footage/Sony."""
    video = a_file(tmp_path, "A016C008_260618GD.MXF")
    clip = FakeMediaPoolItem(
        "A016C008_260618GD.MXF",
        str(video),
        properties={"Camera TC Type": "ILME-FX6V", "Camera Type": "Sony"},
        metadata={"Camera TC Type": "ILME-FX6V", "Camera Type": "Sony"},
    )
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["imported"][0]["bin"] == "02_Footage/ILME-FX6V"
    assert result["imported"][0]["bin_source"] == "camera_metadata"


def test_an_explicit_bin_bypasses_suggestion_entirely(attach: Attach, tmp_path: Path) -> None:
    """No path is ever refused for being off-convention: bin= wins, even for audio."""
    audio = a_file(tmp_path, "mix.wav")
    pool = media_pool()
    attach(studio(pool=pool))

    result = import_media(paths=[str(audio)], bin="Anywhere/At All")

    assert result["ok"] is True
    assert result["bin"] == "Anywhere/At All"
    assert result["imported"][0]["bin"] == "Anywhere/At All"
    assert result["imported"][0]["bin_source"] == "explicit"
    assert "bins" not in result


def test_a_refused_camera_move_falls_back_to_the_footage_bin(
    attach: Attach, tmp_path: Path
) -> None:
    """A camera model that cannot land its leaf is a fallback, not a lie in the envelope."""
    video = a_file(tmp_path, "C0012.mp4")
    clip = FakeMediaPoolItem("C0012.mp4", str(video), metadata={"Camera TC Type": "ILME-FX6V"})
    pool = media_pool()
    pool.import_result = [clip]
    pool.move_result = False
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["ok"] is True
    assert result["imported"][0]["bin"] == "02_Footage"
    assert result["imported"][0]["bin_source"] == "fallback"


# --- the camera sidecar (#94) ----------------------------------------------------------
#
# Resolve reads camera metadata off the MXF wrapper and not off an MP4 on an M4ROOT card,
# so A7-series footage reports no camera key at all and used to bin as bare 02_Footage.
# The model is in an XML sidecar beside the clip. These use the real shape: namespaced,
# because the namespace differs between camera generations and a literal tag match would
# pass here and fail on the card.

SIDECAR_XML = """<?xml version="1.0" encoding="UTF-8"?>
<NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">
  <Duration value="24"/>
  <Device manufacturer="Sony" modelName="{model}" serialNo="4294967295"/>
  <VideoFormat><VideoFrame formatFps="23.98p"/></VideoFormat>
</NonRealTimeMeta>
"""


def a_sidecar(clip: Path, model: str, tail: str = "M01.XML") -> Path:
    target = clip.with_name(f"{clip.stem}{tail}")
    target.write_text(SIDECAR_XML.format(model=model), encoding="utf-8")
    return target


def test_a_camera_sidecar_names_the_bin_when_resolve_reports_no_camera(
    attach: Attach, tmp_path: Path
) -> None:
    """The A7 IV case this ticket failed on live: no camera key anywhere, model on disk."""
    video = a_file(tmp_path, "20260617_D_A7IV_0001.MP4")
    a_sidecar(video, "ILCE-7M4")
    clip = FakeMediaPoolItem("20260617_D_A7IV_0001.MP4", str(video))
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["ok"] is True
    assert result["bins"] == ["02_Footage/ILCE-7M4"]
    assert result["imported"][0]["bin"] == "02_Footage/ILCE-7M4"
    assert result["imported"][0]["bin_source"] == "camera_sidecar"
    leaf = bin_named(pool, "02_Footage/ILCE-7M4")
    assert [found.GetName() for found in leaf.GetClipList()] == ["20260617_D_A7IV_0001.MP4"]


def test_what_resolve_reports_outranks_the_sidecar(attach: Attach, tmp_path: Path) -> None:
    """The sidecar is the last look, not a preferred one: media Resolve reads is untouched."""
    video = a_file(tmp_path, "A016C008_260618GD.MXF")
    a_sidecar(video, "SOMETHING-ELSE")
    clip = FakeMediaPoolItem(
        "A016C008_260618GD.MXF", str(video), properties={"Camera TC Type": "ILME-FX6V"}
    )
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["imported"][0]["bin"] == "02_Footage/ILME-FX6V"
    assert result["imported"][0]["bin_source"] == "camera_metadata"


def test_a_sidecar_index_and_extension_case_still_match(attach: Attach, tmp_path: Path) -> None:
    """Both vary on real cards and neither changes which clip the sidecar belongs to."""
    video = a_file(tmp_path, "C0500.MP4")
    a_sidecar(video, "ILCE-7M4", tail="M02.xml")
    clip = FakeMediaPoolItem("C0500.MP4", str(video))
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["imported"][0]["bin"] == "02_Footage/ILCE-7M4"
    assert result["imported"][0]["bin_source"] == "camera_sidecar"


def test_a_sidecar_for_a_different_clip_is_not_read(attach: Attach, tmp_path: Path) -> None:
    """Cards hold every clip's sidecar in one directory; only this clip's may answer."""
    video = a_file(tmp_path, "C0500.MP4")
    a_sidecar(tmp_path / "C0501.MP4", "ILCE-7M4")
    clip = FakeMediaPoolItem("C0500.MP4", str(video))
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["imported"][0]["bin"] == "02_Footage"
    assert result["imported"][0]["bin_source"] == "fallback"


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("<NonRealTimeMeta><Device modelName=", id="truncated"),
        pytest.param("not xml at all", id="not-xml"),
        pytest.param(
            '<NonRealTimeMeta><Device manufacturer="Sony"/></NonRealTimeMeta>', id="no-model"
        ),
        pytest.param(
            '<NonRealTimeMeta><Device modelName="  "/></NonRealTimeMeta>', id="blank-model"
        ),
        pytest.param("<NonRealTimeMeta><Duration value='24'/></NonRealTimeMeta>", id="no-device"),
    ],
)
def test_an_unusable_sidecar_falls_back_rather_than_failing_the_import(
    attach: Attach, tmp_path: Path, content: str
) -> None:
    """A sidecar is untrusted input off a card: it can cost a suggestion, never an import."""
    video = a_file(tmp_path, "C0500.MP4")
    video.with_name("C0500M01.XML").write_text(content, encoding="utf-8")
    clip = FakeMediaPoolItem("C0500.MP4", str(video))
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["ok"] is True
    assert result["imported"][0]["bin"] == "02_Footage"
    assert result["imported"][0]["bin_source"] == "fallback"


def test_a_sidecar_model_with_a_slash_cannot_open_a_bin_level(
    attach: Attach, tmp_path: Path
) -> None:
    """The sidecar is off a card, so its model gets the same folding a property's does."""
    video = a_file(tmp_path, "C0500.MP4")
    a_sidecar(video, "ILCE/7M4")
    clip = FakeMediaPoolItem("C0500.MP4", str(video))
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["imported"][0]["bin"] == "02_Footage/ILCE-7M4"


def test_a_clip_with_no_path_reads_no_sidecar(attach: Attach, tmp_path: Path) -> None:
    """Multicam and compound clips are pathless, not offline — and have nothing beside them."""
    video = a_file(tmp_path, "C0500.MP4")
    clip = FakeMediaPoolItem("C0500.MP4", "")
    pool = media_pool()
    pool.import_result = [clip]
    attach(studio(pool=pool))

    result = import_media(paths=[str(video)])

    assert result["ok"] is True
    assert result["imported"][0]["bin_source"] == "fallback"


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


def test_a_recursive_key_that_is_not_a_boolean_is_refused_not_coerced(
    attach: Attach, tmp_path: Path
) -> None:
    """A JSON string reads as True under bool(), which would search the opposite way."""
    same = a_file(tmp_path, "C0012.mp4")
    pool = media_pool(
        bins={
            "Angles": [a_clip(same, Description="in Angles")],
            "Angles/Cam A": [a_clip(same, Description="nested")],
        }
    )
    attach(studio(pool=pool))

    result = set_clip_metadata(
        [
            {
                "clip": "C0012.mp4",
                "bin": "Angles",
                "recursive": "false",
                "fields": {"Description": "no"},
            },
            {"clip": "C0012.mp4", "bin": "Angles/Cam A", "fields": {"Description": "yes"}},
        ]
    )

    first, second = result["results"]
    assert first["ok"] is False
    assert first["error"]["code"] == "invalid_request"
    assert second["ok"] is True  # one bad item never sinks the batch
    assert pool.GetRootFolder().GetSubFolderList()[0].GetClipList()[0].property_writes == []


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


def test_relink_to_a_file_reports_the_state_before_the_replace(
    attach: Attach, tmp_path: Path
) -> None:
    """ReplaceClip renames the pool clip (#85), so the reply must carry the before state.

    was_offline is read before the replace, and the reply's clip name is the one the pool
    clip answers to afterwards — the caller's name is gone.
    """
    clip = a_clip(tmp_path / "old" / "relink_me.png")
    renamed = a_file(tmp_path, "relink_moved/relink_me_renamed.png")
    attach(studio(pool=media_pool(bins={"Angles": [clip]})))
    assert list_media(offline_only=True)["count"] == 1

    result = relink_media(["relink_me.png"], str(renamed))

    assert result["ok"] is True
    assert result["results"] == [
        {
            "clip": "relink_me_renamed.png",
            "bin": "Angles",
            "ok": True,
            "file_path": str(renamed),
            "offline": False,
            "was_offline": True,
        }
    ]


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
