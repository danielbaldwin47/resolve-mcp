"""Measuring a cut against its music: what lands on disk, and what comes back inline.

The seam is the fake Resolve singleton for the shots and real files on disk for the
analysis, because that is exactly what this tool joins: shots read out of Resolve, times
read out of the files another job wrote. The transient detector is injected (ADR 0002), so
the arithmetic under test never waits for a decode.

The fixture cut is deliberately off the grid in both directions — one shot lands two frames
late, the next one frame early — because a measurement that cannot tell early from late is
no use to a style profile.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import beats as beats_module
from resolve_mcp.analysis import correlate, records
from resolve_mcp.analysis.beats import BeatGrid
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools import analysis as analysis_tools

from .conftest import Attach
from .fakes import FakeMediaPoolItem, FakeTimeline, FakeTimelineItem, FakeTrack, studio

FPS = "60"

BEAT_SECONDS = tuple(index * 0.5 for index in range(13))
"""Half-second beats from zero: 120bpm, four to the bar, six seconds of concert."""

ONSETS = (1.05, 2.48)
"""Two transients, one either side of the beat the cuts near them are measured against."""

BAD_BAR_SECONDS = tuple(round(index * 0.5, 6) for index in range(17))
BAD_BAR_DOWNBEATS = (0.0, 3.0, 5.0, 7.0)
"""A grid whose bars are mostly fours but whose first runs to six — the #112 self-refuting case.

Beats stay at 120bpm throughout, so the steadiness check is inert here and the bar-position
check is the only thing that can gate: the second measured cut lands on beat six of a bar
that a meter of four cannot hold, and the first lands on a position four can.
"""

SHOTS = (
    ("C0012.mp4", 100, 62, 1000),
    ("C0031.mp4", 162, 87, 4200),
    ("C0012.mp4", 249, 50, 1400),
)
"""name, record in, duration, source in — a cut at 1.033s and another at 2.483s."""


def a_cut(
    name: str = "sunset-set v3",
    shots: Sequence[tuple[str, int, int, int]] = SHOTS,
    audio_source: int | None = 0,
) -> FakeTimeline:
    """Shots over one continuous master mix — the shape build_timeline materializes."""
    audio = (
        [FakeTrack("Master", [_shot("master_mix.wav", 100, 200, audio_source)])]
        if audio_source is not None
        else []
    )
    return FakeTimeline(
        name,
        FPS,
        start_frame=100,
        video=[FakeTrack("Video 1", [_shot(*shot) for shot in shots])],
        audio=audio,
    )


def _shot(name: str, start: int, duration: int, source_start: int) -> FakeTimelineItem:
    return FakeTimelineItem(
        name,
        start,
        duration,
        source_start=source_start,
        media_item=FakeMediaPoolItem(name),
    )


def a_stack(*video: FakeTrack, name: str = "sunset-set v3") -> FakeTimeline:
    """A cut spread over more than one video track — the layout the visible edit resolves."""
    return FakeTimeline(
        name,
        FPS,
        start_frame=100,
        video=list(video),
        audio=[FakeTrack("Master", [_shot("master_mix.wav", 100, 200, 0)])],
    )


def _cut_track(shots: Sequence[tuple[str, int, int, int]] = SHOTS) -> FakeTrack:
    return FakeTrack("Video 1", [_shot(*shot) for shot in shots])


def _a_cut_with(extra: FakeTimelineItem) -> FakeTimeline:
    """The fixture cut with one more item on the cut track — a transition, or a generator."""
    return FakeTimeline(
        "sunset-set v3",
        FPS,
        start_frame=100,
        video=[FakeTrack("Video 1", [_shot(*shot) for shot in SHOTS] + [extra])],
        audio=[FakeTrack("Master", [_shot("master_mix.wav", 100, 200, 0)])],
    )


def _angle(name: str, start: int, duration: int, source_start: int, clip: Any) -> FakeTimelineItem:
    """One shot of a multicam: its own name is the angle, its pool item is shared."""
    return FakeTimelineItem(name, start, duration, source_start=source_start, media_item=clip)


def _dissolve(start: int, duration: int) -> FakeTimelineItem:
    """A transition as Resolve hands it back: no pool item, and it overlaps both neighbours."""
    return FakeTimelineItem("Cross Dissolve", start, duration)


def beats_file(
    tmp_path: Path,
    seconds: Sequence[float] = BEAT_SECONDS,
    downbeats: Sequence[float] | None = None,
    name: str = "concert-beats.json",
) -> Path:
    """A beats file in the shape analyze_music writes: header, then one record per line.

    Written once per test: the cache keys off the file's mtime, so a second identical write
    would look like new analysis and no rerun would ever be answered from cache.
    """
    target = tmp_path / name
    if target.exists():
        return target
    grid = BeatGrid(tuple(seconds), tuple(seconds[::4] if downbeats is None else downbeats))
    return records.write(
        target,
        {"kind": "beats", "audio": "concert.wav", "duration_seconds": 6.0},
        "beats",
        beats_module.numbered(grid),
    )


def tunes_file(tmp_path: Path) -> Path:
    """Two tunes with an applause gap between them, as the structure job writes them."""
    return records.write(
        tmp_path / "concert-tunes.json",
        {"kind": "tunes", "audio": "concert.wav", "count": 2},
        "tunes",
        [
            {"tune": 1, "t": 0.0, "end": 2.0, "seconds": 2.0},
            {"tune": 2, "t": 2.2, "end": 6.0, "seconds": 3.8},
        ],
    )


def solos_file(tmp_path: Path, first_from: str | None = None, first_t: float = 0.0) -> Path:
    return records.write(
        tmp_path / f"concert-solos-{first_from or 'nobody'}-{first_t}.json",
        {"kind": "solos", "audio": "concert.wav", "count": 2},
        "solos",
        [
            {"change": 1, "t": first_t, "to": "drums", "from": first_from, "signal": "prominence"},
            {"change": 2, "t": 2.0, "to": "other", "from": "drums", "signal": "timbre"},
        ],
    )




def _onsets(times: Sequence[float] = ONSETS) -> correlate.Onsets:
    def detect(path: Path) -> tuple[float, ...]:
        return tuple(times)

    return detect


def _audio(tmp_path: Path) -> Path:
    """A stand-in master mix: the injected detector never opens it, the cache only stats it."""
    target = tmp_path / "master_mix.wav"
    if not target.exists():
        target.write_bytes(b"RIFF----WAVE")
    return target


def _started(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    call: dict[str, Any] = {
        "beats": str(beats_file(tmp_path)),
        "audio": str(_audio(tmp_path)),
        "onsets": _onsets(),
    }
    call.update(overrides)
    return correlate.correlate_timeline(get_connection(), **call)


def _measured(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    """Start the job the way the tool does and hand back its completed result."""
    return _result(_started(tmp_path, **overrides))


def _result(started: dict[str, Any]) -> dict[str, Any]:
    record = wait_for(started["job_id"])
    assert record.state == "completed", record.error
    assert record.result is not None
    return record.result


def _rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    written = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    return list(written["cuts"])


def test_every_shot_lands_on_disk_with_its_offsets_bar_and_section(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, tunes=str(tunes_file(tmp_path)), solos=str(solos_file(tmp_path)))

    cuts = _rows(result)
    assert [one["clip"] for one in cuts] == ["C0012.mp4", "C0031.mp4", "C0012.mp4"]
    assert cuts[1] == {
        "cut": 2,
        "clip": "C0031.mp4",
        "track": 1,
        "role": None,
        "opening": False,
        "t": 1.033,
        "in": {"frames": 162, "seconds": 2.7, "timecode": "00:00:02:42", "fps": 60.0},
        "out": {"frames": 249, "seconds": 4.15, "timecode": "00:00:04:09", "fps": 60.0},
        "seconds": 1.45,
        "beat_offset": 0.033,
        "beat": 3,
        "bar": 1,
        "in_bar": 3,
        "in_grid": True,
        "transient_offset": -0.017,
        "tune": 1,
        "front": "drums",
    }
    assert cuts[2]["tune"] == 2
    assert cuts[2]["front"] == "other"


def test_the_offset_is_signed_from_the_cut_to_the_nearest_beat(
    attach: Attach, tmp_path: Path
) -> None:
    """Early and late are different edits, so the sign has to survive to the file."""
    attach(studio(timeline=a_cut()))

    cuts = _rows(_measured(tmp_path))

    late, early = cuts[1], cuts[2]
    assert late["beat_offset"] == 0.033  # 1.033s against a beat at 1.0: two frames late
    assert late["transient_offset"] == -0.017  # a transient at 1.05: one frame ahead of the hit
    assert early["beat_offset"] == -0.017  # 2.483s against a beat at 2.5: one frame early
    assert early["transient_offset"] == 0.003


def test_the_opening_shot_is_marked_and_left_out_of_the_offset_statistics(
    attach: Attach, tmp_path: Path
) -> None:
    """Where the timeline begins is not an edit decision; averaging it in would flatter it."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path)

    assert _rows(result)[0]["opening"] is True
    assert result["cuts"] == 3
    assert result["openings"] == 1
    assert result["beat_offsets"]["measured"] == 2


def test_the_gist_carries_statistics_inline_and_the_records_stay_on_disk(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path)

    assert result["beat_offsets"] == {
        "measured": 2,
        "mean_abs": 0.025,
        "median_abs": 0.025,
        "max_abs": 0.033,
        "early": 1,
        "late": 1,
        "on": 0,
    }
    assert result["transient_offsets"]["mean_abs"] == 0.01
    # Every stat is taken over the records as written, so the gist agrees to the last decimal
    # with anything the agent computes from the file itself.
    assert result["shot_seconds"] == {
        "mean": 1.105,
        "median": 1.033,
        "min": 0.833,
        "max": 1.45,
    }
    assert result["bars"] == {"2": 1, "3": 1}
    assert len(result["first_cuts"]) == 3


def test_roles_come_from_the_angle_sidecar_and_unlabelled_shots_say_so(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, angles={"C0031.mp4": {"role": "drums"}})

    assert result["roles"]["drums"] == {"cuts": 1, "seconds": 1.45, "share": 0.437}
    assert result["roles"]["unlabelled"]["cuts"] == 2
    assert [one["role"] for one in _rows(result)] == [None, "drums", None]


def test_a_role_may_be_written_as_a_bare_string(attach: Attach, tmp_path: Path) -> None:
    """The sidecar is the agent's document; both shapes of entry it holds read the same."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, angles={"C0012.mp4": "wide"})

    assert result["roles"]["wide"]["cuts"] == 2


def test_an_entry_with_no_role_in_it_is_dropped_rather_than_refused(
    attach: Attach, tmp_path: Path
) -> None:
    """Half a labelled corpus is still a measurement; a wrong call would cost the whole run."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, angles={"C0012.mp4": {"subject": "the room"}})

    assert result["roles"] is None


def test_a_dissolve_is_not_a_shot(attach: Attach, tmp_path: Path) -> None:
    """Resolve hands transitions back in the item list, sitting across the cut they soften.

    Live on the corpus anchor (#45) that was 159 of them in 525 items. Counted as shots
    they would add a cut the editor never made — half a transition early — and a run of
    identical short durations to the shot stats.
    """
    attach(studio(timeline=_a_cut_with(_dissolve(start=155, duration=14))))

    result = _measured(tmp_path)

    assert [one["clip"] for one in _rows(result)] == ["C0012.mp4", "C0031.mp4", "C0012.mp4"]
    assert result["cuts"] == 3


def test_a_dissolve_that_starts_on_the_cut_takes_no_shot_with_it(
    attach: Attach, tmp_path: Path
) -> None:
    """The other dissolve shape: aligned to the incoming shot rather than centred on the cut.

    Live on corpus entry 2 (#45) both shapes appear in one timeline. This one abuts the
    outgoing shot instead of overlapping it, so an adjacency rule keeps the dissolve — and
    then drops the real shot behind it, which does overlap. The count stays right and the
    cut is silently a different cut: a swap is worse than a miscount, because nothing about
    the totals looks wrong.
    """
    timeline = FakeTimeline(
        "sunset-set v3",
        FPS,
        start_frame=100,
        video=[
            FakeTrack(
                "Video 1",
                [
                    _shot(*SHOTS[0]),
                    _dissolve(start=162, duration=14),
                    _shot(*SHOTS[1]),
                    _shot(*SHOTS[2]),
                ],
            )
        ],
        audio=[FakeTrack("Master", [_shot("master_mix.wav", 100, 200, 0)])],
    )
    attach(studio(timeline=timeline))

    result = _measured(tmp_path)

    assert [one["clip"] for one in _rows(result)] == ["C0012.mp4", "C0031.mp4", "C0012.mp4"]
    assert result["cuts"] == 3


def test_a_generator_on_the_cut_track_is_still_a_shot(attach: Attach, tmp_path: Path) -> None:
    """The transition test is overlap, not the absence of a pool item — a generator has none."""
    slate = FakeTimelineItem("Solid Color", 299, 30, source_start=0)
    attach(studio(timeline=_a_cut_with(slate)))

    result = _measured(tmp_path)

    assert [one["clip"] for one in _rows(result)][-1] == "Solid Color"
    assert result["cuts"] == 4


def test_a_dissolve_into_a_generator_does_not_eat_the_generator(
    attach: Attach, tmp_path: Path
) -> None:
    """Neither item has a pool item, so 'overlaps a real shot' cannot separate them.

    What separates them is that a generator holds its stretch of track exclusively and a
    transition does not — so the test is overlap with anything else, not with media.
    """
    timeline = FakeTimeline(
        "sunset-set v3",
        FPS,
        start_frame=100,
        video=[
            FakeTrack(
                "Video 1",
                [
                    _shot(*SHOTS[0]),
                    _shot(*SHOTS[1]),
                    _dissolve(start=249, duration=14),
                    FakeTimelineItem("Solid Color", 249, 30, source_start=0),
                ],
            )
        ],
        audio=[FakeTrack("Master", [_shot("master_mix.wav", 100, 200, 0)])],
    )
    attach(studio(timeline=timeline))

    result = _measured(tmp_path)

    assert [one["clip"] for one in _rows(result)] == ["C0012.mp4", "C0031.mp4", "Solid Color"]


def test_an_overlay_is_the_shot_the_frame_shows_and_the_covered_clip_resumes(
    attach: Attach, tmp_path: Path
) -> None:
    """What the viewer sees is the topmost item, so a V2 overlay is a shot and V1 is not.

    #142: the #46 director recut put three shots on V2, and a V1-only measurement attributed
    those frames to the clip underneath — a report that described a cut nobody watched.
    """
    over = FakeTrack("Video 2", [_shot("B0001.mp4", 180, 40, 0)])
    attach(studio(timeline=a_stack(_cut_track(), over)))

    cuts = _rows(_measured(tmp_path))

    assert [(one["clip"], one["in"]["frames"]) for one in cuts] == [
        ("C0012.mp4", 100),
        ("C0031.mp4", 162),
        ("B0001.mp4", 180),
        ("C0031.mp4", 220),
        ("C0012.mp4", 249),
    ]
    assert [one["track"] for one in cuts] == [1, 1, 2, 1, 1]


def test_a_gap_is_a_black_shot_rather_than_nothing(attach: Attach, tmp_path: Path) -> None:
    """A director who cuts to black cut to something; a vanished gap is a cut left unmeasured."""
    gapped = (SHOTS[0], ("C0031.mp4", 200, 87, 4200))
    attach(studio(timeline=a_cut(shots=gapped)))

    result = _measured(tmp_path)

    cuts = _rows(result)
    assert [(one["clip"], one["track"]) for one in cuts] == [
        ("C0012.mp4", 1),
        (None, None),
        ("C0031.mp4", 1),
    ]
    assert cuts[1]["role"] is None
    assert cuts[1]["seconds"] == 0.633  # 162 to 200 at 60fps
    assert result["clips"]["black"] == {"cuts": 1, "seconds": 0.633, "share": 0.203}


def test_black_is_counted_apart_from_the_clips_nobody_labelled(
    attach: Attach, tmp_path: Path
) -> None:
    """A known absence is not a missing label — folding them together hides both."""
    gapped = (SHOTS[0], ("C0031.mp4", 200, 87, 4200))
    attach(studio(timeline=a_cut(shots=gapped)))

    result = _measured(tmp_path, angles={"C0012.mp4": "wide"})

    assert result["roles"]["black"]["cuts"] == 1
    assert result["roles"]["unlabelled"]["cuts"] == 1
    assert result["roles"]["wide"]["cuts"] == 1


def test_a_cut_out_of_black_is_a_cut_rather_than_an_opening(
    attach: Attach, tmp_path: Path
) -> None:
    """Black is an outgoing angle, so the frame it hands over on is a decision like any other."""
    gapped = (SHOTS[0], ("C0031.mp4", 200, 87, 4200))
    attach(studio(timeline=a_cut(shots=gapped)))

    result = _measured(tmp_path)

    assert [one["opening"] for one in _rows(result)] == [True, False, False]
    assert result["openings"] == 1


def test_an_overlay_filling_a_gap_leaves_no_black(attach: Attach, tmp_path: Path) -> None:
    """The #46 shape: V1 gaps with V2 shots over some of them, black only where none is."""
    gapped = (SHOTS[0], ("C0031.mp4", 260, 87, 4200))
    over = FakeTrack("Video 2", [_shot("B0001.mp4", 162, 38, 0)])
    attach(studio(timeline=a_stack(_cut_track(gapped), over)))

    result = _measured(tmp_path)

    cuts = _rows(result)
    assert [one["clip"] for one in cuts] == ["C0012.mp4", "B0001.mp4", None, "C0031.mp4"]
    assert result["visible"]["black"] == 1


def test_a_switched_off_track_is_not_in_the_visible_edit(attach: Attach, tmp_path: Path) -> None:
    """An overlay the director muted is not on screen, so measuring it would report a cut
    nobody can watch."""
    over = FakeTrack("Video 2", [_shot("B0001.mp4", 180, 40, 0)], enabled=False)
    attach(studio(timeline=a_stack(_cut_track(), over)))

    result = _measured(tmp_path)

    assert [one["clip"] for one in _rows(result)] == ["C0012.mp4", "C0031.mp4", "C0012.mp4"]
    assert result["visible"]["skipped"] == [2]


def test_a_disabled_item_is_not_in_the_visible_edit(attach: Attach, tmp_path: Path) -> None:
    """``GetClipEnabled`` is the item's own switch and reads true off the current timeline (#84)."""
    muted = FakeTimelineItem(
        "B0001.mp4",
        180,
        40,
        source_start=0,
        media_item=FakeMediaPoolItem("B0001.mp4"),
        enabled=False,
    )
    attach(studio(timeline=a_stack(_cut_track(), FakeTrack("Video 2", [muted]))))

    result = _measured(tmp_path)

    assert [one["clip"] for one in _rows(result)] == ["C0012.mp4", "C0031.mp4", "C0012.mp4"]


def test_a_track_whose_switch_cannot_be_read_is_measured_rather_than_dropped(
    attach: Attach, tmp_path: Path
) -> None:
    """#84: off the current timeline every track answers "off", and believing that would
    measure a whole concert as one black shot."""
    cut = a_stack(_cut_track(), name="recut v1")
    cut.getters_need_current = True
    open_one = FakeTimeline("something else v1", FPS, start_frame=100)
    attach(studio(timeline=open_one, timelines=[open_one, cut]))

    result = _measured(tmp_path, timeline="recut v1")

    assert [one["clip"] for one in _rows(result)] == ["C0012.mp4", "C0031.mp4", "C0012.mp4"]
    assert result["visible"]["enabled_known"] is False
    assert result["visible"]["skipped"] == []


def test_naming_a_track_measures_that_track_alone(attach: Attach, tmp_path: Path) -> None:
    """The old reading is still reachable: what one track holds, overlays and all ignored."""
    over = FakeTrack("Video 2", [_shot("B0001.mp4", 180, 40, 0)])
    attach(studio(timeline=a_stack(_cut_track(), over)))

    result = _measured(tmp_path, track=1)

    assert [one["clip"] for one in _rows(result)] == ["C0012.mp4", "C0031.mp4", "C0012.mp4"]
    assert result["visible"]["mode"] == "track"


def test_one_track_read_alone_still_lets_its_gaps_vanish(attach: Attach, tmp_path: Path) -> None:
    """Track mode measures the track as laid out, so a gap is absence and the next shot opens."""
    gapped = (SHOTS[0], ("C0031.mp4", 200, 87, 4200))
    attach(studio(timeline=a_cut(shots=gapped)))

    result = _measured(tmp_path, track=1)

    assert [one["opening"] for one in _rows(result)] == [True, True]
    assert result["openings"] == 2
    assert result["beat_offsets"] is None


def test_the_report_says_which_tracks_the_reading_came_from(
    attach: Attach, tmp_path: Path
) -> None:
    """A measurement of the wrong stack looks exactly like one of the right stack."""
    over = FakeTrack("Video 2", [_shot("B0001.mp4", 180, 40, 0)])
    attach(studio(timeline=a_stack(_cut_track(), over)))

    result = _measured(tmp_path)

    assert result["visible"] == {
        "mode": "visible",
        "video_tracks": 2,
        "measured": [1, 2],
        "skipped": [],
        "enabled_known": True,
        "black": 0,
    }


def test_each_multicam_angle_is_its_own_clip(attach: Attach, tmp_path: Path) -> None:
    """Every angle of a multicam shares one pool item, so the pool name is not the angle.

    Live on the corpus anchor (#45): both cameras answered "Zinc - Set 2 Multicam", which
    reads as an hour of concert with no angle switch in it — the one signal the corpus is
    measured for (#21).
    """
    multicam = FakeMediaPoolItem("Zinc - Set 2 Multicam", properties={"Type": "Multicam"})
    angles = FakeTimeline(
        "zinc v1",
        FPS,
        start_frame=100,
        video=[
            FakeTrack(
                "Video 1",
                [
                    _angle("Zinc - Set 2 Multicam - Video 1", 100, 62, 1000, multicam),
                    _angle("Zinc - Set 2 Multicam - Video 2", 162, 87, 4200, multicam),
                ],
            )
        ],
        audio=[FakeTrack("Master", [_shot("master_mix.wav", 100, 200, 0)])],
    )
    attach(studio(timeline=angles))

    result = _measured(tmp_path)

    assert set(result["clips"]) == {
        "Zinc - Set 2 Multicam - Video 1",
        "Zinc - Set 2 Multicam - Video 2",
    }


def test_a_multicam_angle_can_be_labelled_in_the_sidecar(attach: Attach, tmp_path: Path) -> None:
    """Which is the point of telling them apart: the sidecar keys on what the shot answers."""
    multicam = FakeMediaPoolItem("Zinc - Set 2 Multicam", properties={"Type": "Multicam"})
    angles = FakeTimeline(
        "zinc v1",
        FPS,
        start_frame=100,
        video=[
            FakeTrack(
                "Video 1",
                [
                    _angle("Zinc - Set 2 Multicam - Video 1", 100, 62, 1000, multicam),
                    _angle("Zinc - Set 2 Multicam - Video 2", 162, 87, 4200, multicam),
                ],
            )
        ],
        audio=[FakeTrack("Master", [_shot("master_mix.wav", 100, 200, 0)])],
    )
    attach(studio(timeline=angles))

    result = _measured(
        tmp_path, angles={"Zinc - Set 2 Multicam - Video 2": {"role": "drums-tight"}}
    )

    assert result["roles"]["drums-tight"]["cuts"] == 1
    assert result["roles"]["unlabelled"]["cuts"] == 1


def test_shots_are_counted_per_clip_even_without_a_sidecar(
    attach: Attach, tmp_path: Path
) -> None:
    """Angle labels are the director's to author; the measurement stands without them."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path)

    assert result["clips"]["C0012.mp4"]["cuts"] == 2
    assert result["clips"]["C0031.mp4"]["cuts"] == 1
    assert result["roles"] is None


def test_the_analysis_clock_is_read_off_the_master_audio_clip(
    attach: Attach, tmp_path: Path
) -> None:
    """A cut's time is its position in the *mix*, not on the timeline: the audio says where."""
    attach(studio(timeline=a_cut(audio_source=120)))  # the mix starts two seconds in

    result = _measured(tmp_path)

    assert result["alignment"] == {
        "mode": "audio_clip",
        "audio": "master_mix.wav",
        "matched": True,
        "zero_frame": -20,
    }
    assert _rows(result)[1]["t"] == 3.033


def test_a_timeline_without_audio_counts_from_its_own_first_frame(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(timeline=a_cut(audio_source=None)))

    result = _measured(tmp_path)

    assert result["alignment"] == {
        "mode": "timeline_start",
        "audio": None,
        "matched": False,
        "zero_frame": 100,
    }


def test_the_clip_holding_the_analysed_mix_wins_over_the_scratch_track(
    attach: Attach, tmp_path: Path
) -> None:
    """On a hand-edited cut A1 is routinely camera scratch; anchoring to it shifts everything."""
    attach(
        studio(
            timeline=FakeTimeline(
                "hand-edited",
                FPS,
                start_frame=100,
                video=[FakeTrack("Video 1", [_shot(*shot) for shot in SHOTS])],
                audio=[
                    FakeTrack("Scratch", [_shot("camera_scratch.wav", 100, 200, 0)]),
                    FakeTrack("Master", [_shot("master_mix.wav", 100, 200, 120)]),
                ],
            )
        )
    )

    result = _measured(tmp_path)

    assert result["alignment"]["audio"] == "master_mix.wav"
    assert result["alignment"]["matched"] is True
    assert _rows(result)[1]["t"] == 3.033


def test_an_unrecognised_mix_still_measures_but_says_the_clock_was_assumed(
    attach: Attach, tmp_path: Path
) -> None:
    """A reading taken off a clip nobody vouched for is the one to check before trusting it."""
    attach(studio(timeline=a_cut()))

    other = tmp_path / "some-other-mix.wav"
    other.write_bytes(b"RIFF----WAVE")
    result = _measured(tmp_path, audio=str(other))

    assert result["alignment"]["audio"] == "master_mix.wav"
    assert result["alignment"]["matched"] is False


def test_saying_where_the_mix_starts_beats_reading_it_off_a_clip_that_is_not_it(
    attach: Attach, tmp_path: Path
) -> None:
    """The analysed mix is not always on the timeline at all, and then no clip can be read.

    Corpus entry 2 (#45): the music reaches the cut through the multicam's own audio angle,
    so A1 carries the multicam, whose in point is in the multicam's timebase and says
    nothing about where the mastered mix begins. Every mode here would be guessing — and a
    guess 15 seconds out turns a cut on the beat into a cut nowhere near one, while the
    reading still looks perfectly ordinary. So the caller can name the frame instead, which
    a render of the timeline makes exactly knowable.
    """
    attach(
        studio(
            timeline=FakeTimeline(
                "judsons",
                FPS,
                start_frame=100,
                video=[FakeTrack("Video 1", [_shot(*shot) for shot in SHOTS])],
                audio=[FakeTrack("A1", [_shot("Sunshine Multicam - Angle 3", 100, 200, 500)])],
            )
        )
    )

    result = _measured(tmp_path, audio_at=100)

    assert result["alignment"] == {
        "mode": "given",
        "audio": "master_mix.wav",
        "matched": True,
        "zero_frame": 100,
    }
    assert _rows(result)[1]["t"] == 1.033


def test_a_mix_with_a_start_timecode_is_not_read_an_hour_late(
    attach: Attach, tmp_path: Path
) -> None:
    """Source frames are absolute: a WAV stamped 01:00:00:00 calls its first frame 216000."""
    stamped = FakeTimelineItem(
        "master_mix.wav",
        100,
        200,
        source_start=216000,
        media_item=FakeMediaPoolItem("master_mix.wav", properties={"Start": "216000"}),
    )
    attach(
        studio(
            timeline=FakeTimeline(
                "stamped",
                FPS,
                start_frame=100,
                video=[FakeTrack("Video 1", [_shot(*shot) for shot in SHOTS])],
                audio=[FakeTrack("Master", [stamped])],
            )
        )
    )

    result = _measured(tmp_path)

    assert result["alignment"]["zero_frame"] == 100
    assert _rows(result)[1]["t"] == 1.033


def test_cuts_past_the_end_of_the_grid_are_counted_rather_than_pinned_quietly(
    attach: Attach, tmp_path: Path
) -> None:
    """The nearest-beat lookup clamps, so a wrong clock otherwise produces well-formed nonsense."""
    far = (*SHOTS, ("C0031.mp4", 700, 60, 5000))  # 10s in, against six seconds of analysis
    attach(studio(timeline=a_cut(shots=far)))

    result = _measured(tmp_path)

    assert result["outside_grid"] == 1
    # Four shots and the black across the run-up to the far one, which is inside the grid.
    assert result["cuts"] == 5


def test_the_head_reads_as_whoever_the_first_change_took_over_from(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(timeline=a_cut()))

    result = _measured(
        tmp_path, solos=str(solos_file(tmp_path, first_from="piano", first_t=1.5))
    )

    assert _rows(result)[1]["front"] == "piano"


def test_a_named_analysis_file_that_says_nothing_is_refused_not_read_as_absent(
    attach: Attach, tmp_path: Path
) -> None:
    """Otherwise a tunes file whose records the reader cannot use looks like no tunes file."""
    empty = records.write(
        tmp_path / "empty-tunes.json",
        {"kind": "tunes", "audio": "concert.wav", "count": 0},
        "tunes",
        [{"note": "nothing found"}],
    )
    attach(studio(timeline=a_cut()))

    envelope = analysis_tools.correlate_timeline(
        beats=str(beats_file(tmp_path)), tunes=str(empty)
    )

    assert envelope["ok"] is False
    assert "tunes" in envelope["error"]["cause"]


def test_sections_are_absent_rather_than_guessed_when_no_structure_file_is_given(
    attach: Attach, tmp_path: Path
) -> None:
    """Tunes and solos are optional input; a cut still measures against the grid without them."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path)

    assert result["tunes"] is None
    assert result["solos"] is None
    assert all(one["tune"] is None and one["front"] is None for one in _rows(result))


def test_transients_are_not_measured_when_no_audio_is_named(
    attach: Attach, tmp_path: Path
) -> None:
    def refuse(path: Path) -> tuple[float, ...]:
        raise AssertionError("the detector must not run without audio")

    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, audio=None, onsets=refuse)

    assert result["transient_offsets"] is None
    assert all(one["transient_offset"] is None for one in _rows(result))


def test_an_empty_video_track_is_a_refusal_not_an_empty_measurement(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(timeline=FakeTimeline("empty v1", FPS, start_frame=100)))

    envelope = analysis_tools.correlate_timeline(beats=str(beats_file(tmp_path)))

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"
    assert "track" in envelope["error"]["fix"]
    assert "context" in envelope


def test_a_missing_analysis_file_is_refused_before_the_job_starts(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(timeline=a_cut()))

    envelope = analysis_tools.correlate_timeline(beats=str(tmp_path / "nothing-here.json"))

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"
    assert "analyze_music" in envelope["error"]["fix"]


def test_a_beats_file_that_is_not_a_beats_file_names_the_field_it_wanted(
    attach: Attach, tmp_path: Path
) -> None:
    wrong = tmp_path / "energy.json"
    wrong.write_text(json.dumps({"kind": "energy", "energy": []}), encoding="utf-8")
    attach(studio(timeline=a_cut()))

    envelope = analysis_tools.correlate_timeline(beats=str(wrong))

    assert envelope["ok"] is False
    assert "beats" in envelope["error"]["cause"]


def test_the_second_run_over_an_unchanged_cut_comes_back_from_cache(
    attach: Attach, tmp_path: Path
) -> None:
    attach(studio(timeline=a_cut()))
    first = _measured(tmp_path)

    started = _started(tmp_path)

    assert started["cached"] is True
    assert _result(started)["path"] == first["path"]


def test_a_report_the_previous_shape_wrote_is_not_answered_as_this_one(
    attach: Attach, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache watches what was measured, not the code — so the shape has to be watched too.

    A stale hit is the worst kind here: the file is well-formed and the reading is confident,
    and the only thing wrong with it is that it answers an older question (#142).
    """
    attach(studio(timeline=a_cut()))
    first = _measured(tmp_path)

    monkeypatch.setattr(correlate, "READING", correlate.READING + 1)
    started = _started(tmp_path)

    assert started["cached"] is False
    assert _result(started)["path"] != first["path"]


def test_a_recut_is_a_different_measurement(attach: Attach, tmp_path: Path) -> None:
    """The cache is keyed on the shots themselves, so a re-cut is never answered by an old file."""
    attach(studio(timeline=a_cut()))
    first = _measured(tmp_path)

    swapped = (SHOTS[0], ("C0044.mp4", 162, 87, 900), SHOTS[2])
    attach(studio(timeline=a_cut(shots=swapped)))
    second = _measured(tmp_path)

    assert second["path"] != first["path"]
    assert _rows(second)[1]["clip"] == "C0044.mp4"


@pytest.mark.parametrize("survives", range(1, 12))
def test_a_handle_that_dies_mid_read_never_measures_half_a_cut(
    attach: Attach, tmp_path: Path, survives: int
) -> None:
    """Every reading is taken before the job starts, so a death is a refusal, not a short file."""
    dying = studio(timeline=a_cut())
    dying.die_after(survives)
    attach(dying, studio(timeline=a_cut()))

    envelope = analysis_tools.correlate_timeline(beats=str(beats_file(tmp_path)))

    if envelope["ok"]:
        assert _result(envelope["job"])["cuts"] == 3
    else:
        assert envelope["error"]["code"] in {"resolve_unavailable", "internal_error"}


# --- gating the cuts the grid cannot describe (#112) -------------------------------------------


def _bad_grid(tmp_path: Path) -> str:
    return str(
        beats_file(
            tmp_path,
            seconds=BAD_BAR_SECONDS,
            downbeats=BAD_BAR_DOWNBEATS,
            name="bad-grid-beats.json",
        )
    )


def test_a_cut_on_a_bar_position_the_meter_cannot_hold_is_marked_and_counted(
    attach: Attach, tmp_path: Path
) -> None:
    """AC1/AC3: the marker is per cut and the count is inline, so nothing drops silently."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, beats=_bad_grid(tmp_path))

    cuts = _rows(result)
    assert cuts[1]["in_bar"] == 3
    assert cuts[1]["in_grid"] is True
    # Beat six of a bar in a grid whose meter is four: the grid contradicting itself.
    assert cuts[2]["in_bar"] == 6
    assert cuts[2]["in_grid"] is False
    assert result["gated"] == 1


def test_a_gated_cut_is_kept_out_of_the_bar_and_beat_statistics(
    attach: Attach, tmp_path: Path
) -> None:
    """AC3: an impossible position is evidence the grid is wrong, not a position to publish."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, beats=_bad_grid(tmp_path))

    assert result["bars"] == {"3": 1}
    assert "6" not in result["bars"]
    assert result["beat_offsets"]["measured"] == 1


def test_the_gate_leaves_the_transient_measurement_exactly_as_it_was(
    attach: Attach, tmp_path: Path
) -> None:
    """AC2: transients need no grid, so a broken grid must not shrink their n by one cut.

    The two runs are the gate off and on over identical cuts: the sound grid gates nothing,
    the broken one gates a cut. The beat block moves between them and the transient block
    may not.
    """
    attach(studio(timeline=a_cut()))
    ungated = _measured(tmp_path)

    attach(studio(timeline=a_cut()))
    gated = _measured(tmp_path, beats=_bad_grid(tmp_path))

    assert ungated["gated"] == 0
    assert gated["gated"] == 1
    assert gated["transient_offsets"] == ungated["transient_offsets"]
    assert gated["transient_offsets"]["measured"] == 2
    assert gated["beat_offsets"] != ungated["beat_offsets"]


def test_a_cut_in_an_out_of_time_stretch_is_gated_though_its_bar_position_is_legal(
    attach: Attach, tmp_path: Path
) -> None:
    """The check that matters at this seam: rubato carries positions 1..4 like anything else.

    The bar-position gate cannot see this one — every position here is legal — so a cut that
    survives the sound grid and falls out of this one has been gated on timing alone.
    """
    attach(studio(timeline=a_cut()))

    # Steady to 1.5s, then the head goes out of time across where the second cut lands.
    wandering = (0.0, 0.5, 1.0, 1.5, 2.4, 2.6, 3.6, 3.8, 4.9, 5.1, 6.1, 6.3)
    result = _measured(
        tmp_path,
        beats=str(beats_file(tmp_path, seconds=wandering, name="rubato-beats.json")),
    )

    cuts = _rows(result)
    assert cuts[2]["in_bar"] in {1, 2, 3, 4}  # nothing a bar-position check could object to
    assert cuts[2]["in_grid"] is False
    assert result["gated"] >= 1
    assert result["grid_refused"].get("tempo", 0) > 0
    assert result["grid_refused"].get("bar_position", 0) == 0


def test_every_cut_carries_the_marker_even_when_the_grid_is_sound(
    attach: Attach, tmp_path: Path
) -> None:
    """A reader should not have to guess whether an absent marker means trusted or unmeasured."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path)

    assert [one["in_grid"] for one in _rows(result)] == [True, True, True]
    assert result["gated"] == 0
