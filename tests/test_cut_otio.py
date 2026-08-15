"""The document edit: what a tail puts into an exported OTIO, and what it reads back.

Plain dicts, no fakes and no Resolve. :mod:`resolve_mcp.cut.otio` is pure document surgery
— which track the picture ends on, whether the last shot is long enough to fade, what a
transition is in frames — so every decision it makes is answerable with a document alone.
The round trip that carries these documents to Resolve and back is the other half, and its
tests are in ``test_cut_tail.py``.
"""

from __future__ import annotations

from typing import Any

from resolve_mcp.cut import otio as cut_otio
from resolve_mcp.cut import tail as tail_device


def document(*tracks: dict[str, Any], rate: float | None = None) -> dict[str, Any]:
    """An exported cut. ``rate`` is the timeline's own, where Resolve puts it: the start time."""
    doc: dict[str, Any] = {"OTIO_SCHEMA": "Timeline.1", "tracks": {"children": list(tracks)}}
    if rate is not None:
        doc["global_start_time"] = {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": 0}
    return doc


def track(kind: str, name: str, *frames: int, rate: float = 24.0) -> dict[str, Any]:
    """One track. ``rate`` is the *media* rate its clips are stamped in, as OTIO stamps them."""
    return {
        "OTIO_SCHEMA": "Track.1",
        "kind": kind,
        "name": name,
        "children": [
            {
                "OTIO_SCHEMA": "Clip.2",
                "name": f"{name}-{index}",
                "source_range": {
                    "duration": {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": length}
                },
            }
            for index, length in enumerate(frames)
        ],
    }


def unnamed(one: dict[str, Any]) -> dict[str, Any]:
    """A track OTIO left without a name — nothing in the format promises one."""
    one.pop("name", None)
    return one


def pad(one: dict[str, Any], frames: int) -> dict[str, Any]:
    """The trailing black Resolve pads a short track with, out to the timeline's length."""
    one["children"].append(
        {
            "OTIO_SCHEMA": "Gap.1",
            "name": "",
            "source_range": {
                "duration": {"OTIO_SCHEMA": "RationalTime.1", "rate": 24.0, "value": frames}
            },
        }
    )
    return one


def kinds(doc: dict[str, Any]) -> list[tuple[str, int]]:
    return [(item["kind"], item["in_offset"]) for item in cut_otio.transitions(doc)]


def test_the_dissolve_goes_on_the_video_track_and_the_fade_on_the_audio() -> None:
    doc = document(track("Video", "Video 1", 100, 80), track("Audio", "Audio 1", 180))

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 30))

    assert placed == {
        "video_tracks": ["Video 1"],
        "audio_tracks": ["Audio 1"],
        "unfaded_video": [],
        "unfaded_audio": [],
    }
    assert kinds(doc) == [("Video", 40), ("Audio", 30)]


def test_the_transition_ends_the_track_and_appends_no_black_after_it() -> None:
    """A trailing gap is the cut file's own device, and Resolve drops one on import anyway."""
    doc = document(track("Video", "Video 1", 100))

    cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 0))

    children = doc["tracks"]["children"][0]["children"]
    assert [child["OTIO_SCHEMA"] for child in children] == ["Clip.2", "Transition.1"]


def test_an_overlay_that_stopped_earlier_keeps_its_own_ending() -> None:
    """V2 covering a seam in the middle has nothing to do with how the picture leaves."""
    doc = document(track("Video", "Video 1", 100, 80), track("Video", "Video 2", 30))

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 0))

    assert placed["video_tracks"] == ["Video 1"]


def test_a_hard_out_fades_only_the_mix() -> None:
    doc = document(track("Video", "Video 1", 100), track("Audio", "Audio 1", 100))

    placed = cut_otio.inject(doc, tail_device.Tail("hard_to_black", 0, 30))

    assert placed == {
        "video_tracks": [],
        "audio_tracks": ["Audio 1"],
        "unfaded_video": [],
        "unfaded_audio": [],
    }
    assert kinds(doc) == [("Audio", 30)]


def test_the_fade_goes_before_the_black_a_longer_mix_pads_in() -> None:
    """The corpus shape: the mix outlives the picture, so Resolve exports V1 ending on a gap.

    A transition appended to the end of *that* track would dissolve black into black — the
    failure this case was found by, live, on the first build of the real ending.
    """
    doc = document(pad(track("Video", "Video 1", 120, 200), 40), track("Audio", "Audio 1", 360))

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 142, 0))

    assert placed["video_tracks"] == ["Video 1"]
    children = doc["tracks"]["children"][0]["children"]
    assert [child["OTIO_SCHEMA"] for child in children] == [
        "Clip.2",
        "Clip.2",
        "Transition.1",
        "Gap.1",
    ]


def test_the_pad_does_not_make_a_short_overlay_look_like_the_end_of_the_picture() -> None:
    """Every track is padded to the same length, so track length cannot pick the layer."""
    doc = document(
        pad(track("Video", "Video 1", 120, 200), 40),
        pad(track("Video", "Video 2", 60), 300),
    )

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 142, 0))

    assert placed["video_tracks"] == ["Video 1"]


def test_a_last_clip_too_short_takes_no_transition() -> None:
    """Refused, never trimmed: a shortened device is a device the report would lie about."""
    doc = document(track("Video", "Video 1", 100, 40))

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 0))

    assert placed["video_tracks"] == []
    assert cut_otio.transitions(doc) == []


def test_an_overlay_ending_inside_the_dissolve_is_reported_unfaded() -> None:
    """Opaque over part of the ramp, so the picture under it comes back mid-fade."""
    doc = document(track("Video", "Video 1", 100, 80), track("Video", "Video 2", 160))

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 0))

    assert placed["video_tracks"] == ["Video 1"]
    assert placed["unfaded_video"] == ["Video 2"]


def test_an_overlay_ending_before_the_dissolve_starts_is_not_reported() -> None:
    """The device covers the frames it covers: a layer that is gone by then is not in it."""
    doc = document(track("Video", "Video 1", 100, 80), track("Video", "Video 2", 100))

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 0))

    assert placed["unfaded_video"] == []


def test_an_unnamed_track_is_recorded_and_read_back_under_the_same_name() -> None:
    """One vocabulary for both sides, because ``_confirm`` matches them to each other by string.

    A fallback invented separately in each direction is not cosmetic: the dissolve would be
    recorded on ``'video'`` and read back on ``''``, so a tail that landed perfectly reads as
    a fade on the wrong track — and the refusal deletes the correct import on its way out.
    """
    doc = document(unnamed(track("Video", "", 100, 80)), unnamed(track("Audio", "", 180)))

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 30))

    assert placed["video_tracks"] == ["video"]
    assert placed["audio_tracks"] == ["audio"]
    assert [one["track"] for one in cut_otio.transitions(doc)] == ["video", "audio"]


def test_a_shot_at_another_media_rate_is_long_enough_for_the_dissolve() -> None:
    """The cut counts in timeline frames; OTIO stamps each clip in its own media rate.

    A mixed-rate multicam is the ordinary concert kit, and a 60-frame dissolve under an
    80-frame shot is a legal cut — but the shot's own stamp says 40, so the raw comparison
    refuses a build that is fine, after the shots are already on a staging timeline.
    """
    doc = document(track("Video", "Video 1", 50, 40, rate=12.0), rate=24.0)

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 60, 0))

    assert placed["video_tracks"] == ["Video 1"]
    assert placed["unfaded_video"] == []
    # Stamped in the timeline's rate, so the number the read-back compares is the number
    # the cut file asked for rather than the same count in a rate nobody asked about.
    transition = doc["tracks"]["children"][0]["children"][2]
    assert transition["in_offset"] == {"OTIO_SCHEMA": "RationalTime.1", "rate": 24.0, "value": 60}
    assert kinds(doc) == [("Video", 60)]


def test_the_layer_the_cut_ends_on_is_picked_in_timeline_frames() -> None:
    """Two layers stamped in two rates are two units, and the raw numbers pick the wrong one."""
    doc = document(
        track("Video", "Video 1", 50, 40, rate=12.0),
        pad(track("Video", "Video 2", 120, rate=24.0), 60),
        rate=24.0,
    )

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 0))

    # V1 runs 180 timeline frames and V2 120, so V1 ends the picture — even though V1's raw
    # stamps sum to 90 and V2's to 120.
    assert placed["video_tracks"] == ["Video 1"]
    assert placed["unfaded_video"] == []


def test_many_short_clips_at_another_rate_still_end_where_the_overlay_over_them_ends() -> None:
    """A span is a sum, so it is rounded once — half a frame per clip is frames per song.

    Twenty shots stamped at a rate the timeline does not count in come to 1008 frames
    exactly; rounded one clip at a time they come to 1000, which is far enough back to drop
    V1 out of the ending layers and into the window ``_inside`` refuses. The overlay ending
    on the same frame is then the only layer left to fade, and the build refuses a correct
    cut after the shots are already on a staging timeline.
    """
    doc = document(
        track("Video", "Video 1", *([21] * 20), rate=10.0),
        track("Video", "Video 2", 908, 100, rate=24.0),
        rate=24.0,
    )

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 0))

    assert placed["video_tracks"] == ["Video 1", "Video 2"]
    assert placed["unfaded_video"] == []


def test_a_rate_this_cannot_read_measures_the_clip_by_its_value() -> None:
    """Only ``value`` says how long something is; an unreadable rate is not a zero-length clip.

    Reading value and rate under one guard turns a stray rate into a track measuring nothing,
    and then every fade on it is refused for a reason that is not true of the cut.
    """
    doc = document(track("Video", "Video 1", 100, 80), rate=24.0)
    for child in doc["tracks"]["children"][0]["children"]:
        child["source_range"]["duration"]["rate"] = "twenty-four"

    placed = cut_otio.inject(doc, tail_device.Tail("dissolve_to_black", 40, 0))

    assert placed["video_tracks"] == ["Video 1"]
    assert placed["unfaded_video"] == []
    assert kinds(doc) == [("Video", 40)]


def test_an_audio_track_that_took_no_fade_while_another_did_is_reported() -> None:
    """Half a fade is a mix that ends on a cut, which is the thing the tail exists to avoid."""
    doc = document(track("Audio", "Audio 1", 100), track("Audio", "Audio 2", 20))

    placed = cut_otio.inject(doc, tail_device.Tail("hard_to_black", 0, 30))

    assert placed["audio_tracks"] == ["Audio 1"]
    assert placed["unfaded_audio"] == ["Audio 2"]

