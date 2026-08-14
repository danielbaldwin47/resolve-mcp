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
from resolve_mcp.analysis import correlate, energy, records
from resolve_mcp.analysis.beats import BeatGrid
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.resolve.connection import get_connection
from resolve_mcp.tools import analysis as analysis_tools

from .conftest import Attach
from .fakes import (
    FakeMediaPoolItem,
    FakeTimeline,
    FakeTimelineItem,
    FakeTrack,
    studio,
    write_wav,
)

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


RISING = tuple((float(second), -60.0 + second * 0.05) for second in range(600))
"""Ten minutes of level rising steadily: the default curve, so every cut has a gearing read.

Steady rather than flat because a flat curve makes the tercile split arbitrary, and steady
rather than random because a fixture whose loudness nobody can predict cannot be asserted
against. Where a test is about the gearing it passes its own curve.
"""


def _levels(curve: Sequence[tuple[float, float]] = RISING) -> correlate.Levels:
    """The loudness seam, injected: no test in this file opens the stand-in WAV."""

    def read(path: Path) -> tuple[tuple[float, float], ...]:
        return tuple(curve)

    return read


def _steps(*levels: tuple[float, int]) -> tuple[tuple[float, float], ...]:
    """A curve given as (dBFS, how many seconds at it) — quiet stretch, then loud, in order."""
    curve: list[tuple[float, float]] = []
    for level, seconds in levels:
        start = len(curve)
        curve.extend((float(start + step), level) for step in range(seconds))
    return tuple(curve)


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
        "loudness": _levels(),
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
        "stranded": False,
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


def test_a_film_that_opens_on_black_opens_on_a_shot(attach: Attach, tmp_path: Path) -> None:
    """The run-up from the timeline's first frame is held black somebody chose the length of,
    and the cut out of it is a decision — measured, not written off as an opening."""
    late = FakeTimeline(
        "sunset-set v3",
        FPS,
        start_frame=100,
        video=[
            FakeTrack(
                "Video 1",
                [_shot(clip, start + 30, run, source) for clip, start, run, source in SHOTS],
            )
        ],
        audio=[FakeTrack("Master", [_shot("master_mix.wav", 100, 200, 0)])],
    )
    attach(studio(timeline=late))

    result = _measured(tmp_path)

    cuts = _rows(result)
    assert cuts[0]["clip"] is None
    assert cuts[0]["in"]["frames"] == 100  # the timeline's own first frame, not the picture's
    assert cuts[0]["seconds"] == 0.5  # 30 frames at 60fps
    assert cuts[1]["opening"] is False
    assert result["visible"]["black"] == 1


def test_the_black_after_the_last_shot_is_not_a_shot(attach: Attach, tmp_path: Path) -> None:
    """It has no end the edit decides — how far it runs is however long the audio under it is,
    which is a fact about the mix rather than about the cut."""
    attach(studio(timeline=a_cut()))  # the master mix runs one frame past the last shot

    result = _measured(tmp_path)

    assert [one["clip"] for one in _rows(result)] == ["C0012.mp4", "C0031.mp4", "C0012.mp4"]
    assert result["visible"]["black"] == 0


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


# --- the grid that does not reach the cut (#160) -----------------------------------------------


def _short_grid(tmp_path: Path, last: float = 1.5) -> str:
    """A grid that stops before the concert does, leaving the last cut with no beat near it.

    This is the observed shape, not an invented one: the Freefall pass keeps a cut 6.08 s
    from its nearest beat because the detector's grid ends at 670.72 s and the cut is at
    676.80 s. The nearest *surviving* beat is trusted and far away, which is exactly the row
    the gate was supposed to keep out of the trusted columns.
    """
    seconds = tuple(round(index * 0.5, 6) for index in range(int(last / 0.5) + 1))
    return str(beats_file(tmp_path, seconds=seconds, name=f"short-grid-{last}-beats.json"))


def test_a_cut_the_grid_does_not_reach_is_refused_rather_than_scored_against_a_distant_beat(
    attach: Attach, tmp_path: Path
) -> None:
    """#160: a beat a second away in a grid of half-second beats describes nothing at the cut."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, beats=_short_grid(tmp_path))

    cuts = _rows(result)
    assert cuts[1]["in_grid"] is True  # 33 ms from its beat, well inside the grid
    assert cuts[1]["stranded"] is False
    # Two beats past the end of a grid whose beats are half a second apart.
    assert cuts[2]["beat_offset"] == 0.983
    assert cuts[2]["stranded"] is True
    assert cuts[2]["in_grid"] is False


def test_a_stranded_cut_is_counted_apart_from_the_beats_the_gate_refused(
    attach: Attach, tmp_path: Path
) -> None:
    """A third refusal beside ``outside_grid`` and ``gated``, so neither count changes meaning.

    ``gated`` is beats the grid describes badly; this is a cut the grid does not describe at
    all. Folding the two together would make a hole in the detector's output read as rubato.
    """
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, beats=_short_grid(tmp_path))

    assert result["stranded"] == 1
    assert result["gated"] == 0
    assert result["grid_refused"] == {}


def test_a_stranded_cut_is_kept_out_of_the_bar_and_beat_statistics(
    attach: Attach, tmp_path: Path
) -> None:
    """The whole point of the refusal: the mean it would drag is the one style is read from."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, beats=_short_grid(tmp_path))

    assert result["beat_offsets"]["measured"] == 1
    assert result["beat_offsets"]["max_abs"] == 0.033
    assert result["bars"] == {"3": 1}


def test_the_reach_refusal_leaves_the_transient_measurement_exactly_as_it_was(
    attach: Attach, tmp_path: Path
) -> None:
    """Transients need no grid, so a grid that stops early may not shrink their n by a cut."""
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, beats=_short_grid(tmp_path))

    assert result["transient_offsets"]["measured"] == 2


def test_a_cut_in_a_hole_inside_the_grid_is_refused_by_the_beat_gate_rather_than_the_reach_rule(
    attach: Attach, tmp_path: Path
) -> None:
    """Why #160 needed no "a refused beat lies between" rule: an interior hole gates itself.

    ``nearest`` looks over every beat, so no beat can sit between a cut and the beat it was
    scored against — a nearer one would be the one it was scored against. And the steadiness
    check refuses *both* beats of an interval that does not belong, so the beat on the near
    side of a hole is refused already. The reach rule is therefore about the ends of the grid
    and the two counts stay apart here, which is the claim this pins.
    """
    attach(studio(timeline=a_cut()))

    # Half-second beats with the third cut's second missing from the middle of them.
    holed = (0.0, 0.5, 1.0, 1.5, 4.0, 4.5, 5.0, 5.5, 6.0)
    result = _measured(
        tmp_path, beats=str(beats_file(tmp_path, seconds=holed, name="holed-beats.json"))
    )

    cuts = _rows(result)
    assert cuts[2]["in_grid"] is False
    assert cuts[2]["stranded"] is False
    assert result["gated"] == 1
    assert result["stranded"] == 0


def test_a_grid_that_calls_the_whole_span_one_beat_reaches_the_cuts_inside_it(
    attach: Attach, tmp_path: Path
) -> None:
    """The rule is a beat wide, not a number of seconds: a slow grid reaches further.

    Two beats six seconds apart is a grid saying the music runs at 10bpm. That is a bad grid,
    but it is bad in the way #112 judges — nothing about a cut three seconds in contradicts
    it, and refusing that cut for distance would be this rule inventing a tempo of its own.
    """
    attach(studio(timeline=a_cut()))

    result = _measured(
        tmp_path,
        beats=str(beats_file(tmp_path, seconds=(0.0, 6.0), name="coarse-beats.json")),
    )

    assert [one["stranded"] for one in _rows(result)] == [False, False, False]
    assert result["stranded"] == 0


def _paced(*shots: tuple[str, int], name: str = "sunset-set v3") -> FakeTimeline:
    """A cut of back-to-back shots given as (angle, frames) — the shape rhythm is read off.

    Contiguous by construction, so nothing here becomes black by accident and every shot in
    the reading is one this list asked for.
    """
    laid: list[tuple[str, int, int, int]] = []
    start = 100
    for clip, frames in shots:
        laid.append((clip, start, frames, 1000))
        start += frames
    return a_cut(name=name, shots=laid)


def _rhythm(result: dict[str, Any]) -> dict[str, Any]:
    block = result["shot_rhythm"]
    assert isinstance(block, dict)
    return block


def test_the_rhythm_block_bins_the_shot_lengths_and_says_how_far_they_spread(
    attach: Attach, tmp_path: Path
) -> None:
    """One shot per corpus bin: the histogram, the spread and the averages, all arithmetic."""
    attach(
        studio(
            timeline=_paced(
                ("A.mp4", 60),  # 1.0s
                ("B.mp4", 180),  # 3.0s
                ("C.mp4", 360),  # 6.0s
                ("D.mp4", 600),  # 10.0s
                ("E.mp4", 1200),  # 20.0s
                ("F.mp4", 2400),  # 40.0s
            )
        )
    )

    lengths = _rhythm(_measured(tmp_path))["lengths"]

    assert lengths["histogram"] == {"<2": 1, "2-4": 1, "4-8": 1, "8-15": 1, "15-30": 1, ">30": 1}
    assert lengths["spread_ratio"] == 40.0  # 40s over 1s
    assert lengths["mean"] == 13.333
    assert lengths["median"] == 8.0  # between the 6s and the 10s shot


def test_a_shot_on_a_bin_edge_falls_in_the_bin_that_opens_there(
    attach: Attach, tmp_path: Path
) -> None:
    """The bins are half-open on the upper edge: exactly 4s is a 4-8 shot, never a 2-4 one.

    Three shots sitting exactly on three boundaries — 2s, 4s and 30s — so every one of them
    is a shot the neighbouring bin could have claimed.
    """
    attach(studio(timeline=_paced(("A.mp4", 120), ("B.mp4", 240), ("A.mp4", 1800))))

    histogram = _rhythm(_measured(tmp_path))["lengths"]["histogram"]

    assert histogram == {"<2": 0, "2-4": 1, "4-8": 1, "8-15": 0, "15-30": 0, ">30": 1}


def test_two_cameras_traded_on_one_length_read_metronomic(
    attach: Attach, tmp_path: Path
) -> None:
    """The failure the check exists for: strict A/B, every shot in one bin.

    The lengths here vary by nearly four to one, so the coefficient of variation is well
    clear of its floor — this arm is the bin's alone.
    """
    traded = [("A.mp4", 30) if turn % 2 == 0 else ("B.mp4", 114) for turn in range(8)]
    attach(studio(timeline=_paced(*traded)))

    rhythm = _rhythm(_measured(tmp_path))

    assert rhythm["alternation"] == {"cuts": 7, "longest_run": 7, "fraction": 1.0}
    assert rhythm["uniformity"]["bin"] == "<2"
    assert rhythm["uniformity"]["one_bin"] == 1.0
    assert rhythm["uniformity"]["cv"] == 0.583  # varied lengths, all inside the one bin
    assert rhythm["reads_metronomic"] is True


def test_a_metronome_that_straddles_a_bin_boundary_still_reads_metronomic(
    attach: Attach, tmp_path: Path
) -> None:
    """Half the shots in one bin and half in the next, and nothing about the cut varying.

    This is the arm the bin count cannot see: 3.5s and 4.5s land either side of a boundary,
    so no bin holds more than half — but the spread around the mean is tiny and the cut is
    the same metronome it would be if both lengths sat in one bin.
    """
    attach(
        studio(
            timeline=_paced(
                *[("A.mp4", 210) if turn % 2 == 0 else ("B.mp4", 270) for turn in range(8)]
            )
        )
    )

    rhythm = _rhythm(_measured(tmp_path))

    assert rhythm["uniformity"]["one_bin"] == 0.5
    assert rhythm["uniformity"]["cv"] == 0.125
    assert rhythm["reads_metronomic"] is True


def test_a_cut_that_varies_its_lengths_does_not_read_metronomic(
    attach: Attach, tmp_path: Path
) -> None:
    """Two cameras traded strictly is not the finding — trading them on one length is."""
    attach(
        studio(
            timeline=_paced(
                ("A.mp4", 30),  # 0.5s
                ("B.mp4", 300),  # 5.0s
                ("A.mp4", 120),  # 2.0s
                ("B.mp4", 900),  # 15.0s
                ("A.mp4", 60),  # 1.0s
                ("B.mp4", 450),  # 7.5s
            )
        )
    )

    rhythm = _rhythm(_measured(tmp_path))

    assert rhythm["alternation"]["fraction"] == 1.0
    assert rhythm["uniformity"]["one_bin"] == 0.333
    assert rhythm["uniformity"]["cv"] == 0.972
    assert rhythm["reads_metronomic"] is False


def test_a_cut_that_breaks_the_alternation_does_not_read_metronomic(
    attach: Attach, tmp_path: Path
) -> None:
    """Every shot the same length, but the angles do not simply trade — so the run is short."""
    attach(
        studio(
            timeline=_paced(
                ("A.mp4", 90),
                ("B.mp4", 90),
                ("A.mp4", 90),
                ("A.mp4", 90),
                ("B.mp4", 90),
                ("A.mp4", 90),
            )
        )
    )

    rhythm = _rhythm(_measured(tmp_path))

    assert rhythm["alternation"] == {"cuts": 5, "longest_run": 2, "fraction": 0.4}
    assert rhythm["uniformity"]["one_bin"] == 1.0
    assert rhythm["uniformity"]["cv"] == 0.0
    assert rhythm["reads_metronomic"] is False


def test_a_tightening_ladder_reads_metronomic_though_no_two_shots_share_a_length(
    attach: Attach, tmp_path: Path
) -> None:
    """The shape the panel named and this reading used to miss (P3·R3, 9.9s down to 2.9s).

    Two framings traded strictly while every shot is a second shorter than the one before it.
    No length repeats, so the fullest bin holds half the cut and the spread sits just over its
    floor — both length arms read it as varied — and three critics called it a mechanical
    metronome, because a ladder is as countable as a fixed length. The ramp is what sees it.
    """
    ladder = [(f"{'AB'[turn % 2]}.mp4", 594 - 60 * turn) for turn in range(8)]  # 9.9s .. 2.9s
    attach(studio(timeline=_paced(*ladder)))

    rhythm = _rhythm(_measured(tmp_path))

    assert rhythm["alternation"]["fraction"] == 1.0
    assert rhythm["uniformity"]["one_bin"] == 0.5  # four of the eight in 4-8
    assert rhythm["uniformity"]["cv"] == 0.358  # over the floor: the lengths do vary
    assert rhythm["ramp"] == {"cuts": 7, "longest_run": 7, "fraction": 1.0}
    assert rhythm["reads_metronomic"] is True


def test_a_short_ladder_inside_a_varied_cut_is_not_the_finding(
    attach: Attach, tmp_path: Path
) -> None:
    """Four one-way cuts and then the cut stops laddering: a run, not a rule.

    Some stretch of any cut long enough shortens for a few shots — the reading is about a
    shape held over the cut, so the run has to cover it, and here it covers four cuts of nine.
    """
    laddered = [594, 534, 474, 414, 354]  # 9.9s down to 5.9s, four cuts of it
    varied = [720, 60, 840, 90, 780]  # then 12s, 1s, 14s, 1.5s, 13s
    shots = [(f"{'AB'[turn % 2]}.mp4", frames) for turn, frames in enumerate(laddered + varied)]
    attach(studio(timeline=_paced(*shots)))

    rhythm = _rhythm(_measured(tmp_path))

    assert rhythm["alternation"]["fraction"] == 1.0
    assert rhythm["ramp"] == {"cuts": 9, "longest_run": 4, "fraction": 0.444}
    assert rhythm["reads_metronomic"] is False


def test_the_longest_run_is_the_longest_one_not_the_whole_sequence(
    attach: Attach, tmp_path: Path
) -> None:
    """A held pair opens it and a third angle ends it; the run in between is what is counted."""
    attach(
        studio(
            timeline=_paced(
                ("A.mp4", 90),
                ("A.mp4", 90),
                ("B.mp4", 90),
                ("A.mp4", 90),
                ("B.mp4", 90),
                ("A.mp4", 90),
                ("B.mp4", 90),
                ("C.mp4", 90),
            )
        )
    )

    assert _rhythm(_measured(tmp_path))["alternation"] == {
        "cuts": 7,
        "longest_run": 5,
        "fraction": 0.714,
    }


def test_two_shots_are_a_cut_rather_than_an_alternation(attach: Attach, tmp_path: Path) -> None:
    """Alternation needs the return: without it every two-shot timeline would read metronomic."""
    attach(studio(timeline=_paced(("A.mp4", 90), ("B.mp4", 90))))

    rhythm = _rhythm(_measured(tmp_path))

    assert rhythm["alternation"] == {"cuts": 1, "longest_run": 0, "fraction": 0.0}
    assert rhythm["uniformity"]["one_bin"] == 1.0  # uniform, and still not the finding
    assert rhythm["reads_metronomic"] is False


def test_black_is_one_of_the_angles_the_cut_alternates_with(
    attach: Attach, tmp_path: Path
) -> None:
    """Cutting a camera against black is a pattern, not a hole in one."""
    gapped = (
        ("C0012.mp4", 100, 90, 1000),
        ("C0012.mp4", 280, 90, 1400),
        ("C0012.mp4", 460, 90, 1800),
    )
    attach(studio(timeline=a_cut(shots=gapped)))

    rhythm = _rhythm(_measured(tmp_path))

    assert rhythm["shots"] == 5  # three shots and the two black stretches between them
    assert rhythm["alternation"] == {"cuts": 4, "longest_run": 4, "fraction": 1.0}


def test_the_rhythm_reading_carries_the_rule_it_applied(attach: Attach, tmp_path: Path) -> None:
    """A warning-shaped fact: the numbers, the verdict, and the rule that joined them.

    Nothing refuses on it — the job completes and the report is written whatever it says —
    so the agent reading it can weigh the rule against the passage rather than obey it.
    """
    attach(studio(timeline=_paced(("A.mp4", 90), ("B.mp4", 90), ("A.mp4", 90))))

    rhythm = _rhythm(_measured(tmp_path))

    assert rhythm["heuristic"] == correlate.HEURISTIC
    assert "warning" in rhythm["heuristic"]
    assert str(correlate.ALTERNATION_FLOOR) in rhythm["heuristic"]
    assert set(rhythm) == {
        "shots",
        "lengths",
        "alternation",
        "uniformity",
        "ramp",
        "reads_metronomic",
        "gears",
        "heuristic",
    }


def test_a_cut_that_misses_the_last_beat_by_less_than_a_beat_is_still_measured(
    attach: Attach, tmp_path: Path
) -> None:
    """The refusal is about reach, not about the ends: a near miss at the edge is a real cut.

    Refusing everything past the last beat would throw away the cut that lands 20 ms before
    the grid starts or after it stops — an honest measurement — so the line is drawn at the
    width of a beat rather than at the edge of the grid.
    """
    attach(studio(timeline=a_cut()))

    result = _measured(tmp_path, beats=_short_grid(tmp_path, last=2.0))

    cuts = _rows(result)
    assert cuts[2]["beat_offset"] == 0.483  # inside the half-second beat it is measured against
    assert cuts[2]["stranded"] is False
    assert cuts[2]["in_grid"] is True
    assert result["stranded"] == 0


# --- the gearing ------------------------------------------------------------------------------
#
# One curve shape throughout: 36 one-second windows, twelve at each of three levels, over a
# 36-second cut. Thirds of the windows are thirds of the cut, so every cuts-per-minute below
# divides by a fifth of a minute and can be checked by hand.

THIRDS = ((-50.0, 12), (-35.0, 12), (-20.0, 12))
"""Quiet twelve seconds, middling twelve, loud twelve — the fixture the gearing is read on."""

SECOND = 60
"""Frames in a second at the fixture's 60fps, so a shot list reads in seconds."""


def _gears(result: dict[str, Any]) -> dict[str, Any]:
    block = _rhythm(result)["gears"]
    assert isinstance(block, dict), "the curve was injected, so a gearing read was possible"
    return block


def test_the_terciles_are_thirds_of_the_loudness_not_thirds_of_the_clock(
    attach: Attach, tmp_path: Path
) -> None:
    """A cut that opens loud and stays quiet after: the loud third is the opening.

    Thirds by time would call the first twelve seconds the quiet gear because that is where
    the cut starts, and the whole reading would then describe the running order rather than
    the music. Here the loud stretch is first, and the fast cutting in it has to come back as
    the loud tercile's rate.
    """
    opening = [(f"{'AB'[turn % 2]}.mp4", SECOND) for turn in range(12)]
    later = [(f"{'AB'[turn % 2]}.mp4", 4 * SECOND) for turn in range(6)]
    attach(studio(timeline=_paced(*opening, *later)))

    gears = _gears(_measured(tmp_path, loudness=_levels(_steps((-20.0, 12), (-50.0, 24)))))

    assert gears["terciles"]["loud"]["level_dbfs"] == -20.0
    assert gears["terciles"]["loud"]["shots"] == 12  # the opening twelve, first in time
    assert gears["terciles"]["loud"]["median_seconds"] == 1.0
    assert gears["terciles"]["quiet"]["level_dbfs"] == -50.0
    assert gears["terciles"]["quiet"]["median_seconds"] == 4.0
    assert gears["rate_ratio"] == 4.0


def test_each_tercile_reports_its_cutting_rate_over_the_music_it_holds(
    attach: Attach, tmp_path: Path
) -> None:
    """The arithmetic in full: three shots in twelve quiet seconds is fifteen cuts a minute.

    The denominator is the music the tercile holds, not the screen time the shots in it run
    for — the two differ the moment a shot starts in one gear and ends in the next, and only
    the first is a rate the director can compare against another passage.
    """
    quiet = [(f"{'AB'[turn % 2]}.mp4", 4 * SECOND) for turn in range(3)]
    middling = [(f"{'CD'[turn % 2]}.mp4", 3 * SECOND) for turn in range(4)]
    loud = [(f"{'EF'[turn % 2]}.mp4", SECOND) for turn in range(12)]
    attach(studio(timeline=_paced(*quiet, *middling, *loud)))

    gears = _gears(_measured(tmp_path, loudness=_levels(_steps(*THIRDS))))

    assert gears["window_seconds"] == 1.0
    assert gears["terciles"] == {
        "quiet": {
            "seconds": 12.0,
            "shots": 3,
            "cuts_per_minute": 15.0,
            "median_seconds": 4.0,
            "level_dbfs": -50.0,
        },
        "mid": {
            "seconds": 12.0,
            "shots": 4,
            "cuts_per_minute": 20.0,
            "median_seconds": 3.0,
            "level_dbfs": -35.0,
        },
        "loud": {
            "seconds": 12.0,
            "shots": 12,
            "cuts_per_minute": 60.0,
            "median_seconds": 1.0,
            "level_dbfs": -20.0,
        },
    }
    assert gears["rate_ratio"] == 4.0  # sixty cuts a minute against fifteen


def test_a_quiet_third_nobody_cut_in_reports_no_ratio_rather_than_a_number(
    attach: Attach, tmp_path: Path
) -> None:
    """One long shot over the loud opening and everything else after it: the quiet gear is empty.

    A rate of zero in the denominator is not a slow cut, it is no reading — and a ratio
    invented there would read as the strongest gearing in the report.
    """
    held = [("A.mp4", 24 * SECOND)]
    after = [(f"{'BC'[turn % 2]}.mp4", SECOND) for turn in range(12)]
    attach(studio(timeline=_paced(*held, *after)))

    gears = _gears(_measured(tmp_path, loudness=_levels(_steps((-20.0, 12), (-50.0, 24)))))

    assert gears["terciles"]["quiet"]["shots"] == 0
    assert gears["terciles"]["quiet"]["cuts_per_minute"] == 0.0
    assert gears["terciles"]["quiet"]["median_seconds"] is None
    assert gears["rate_ratio"] is None
    assert gears["one_speed"] is False  # a reading it cannot take is not a finding


def test_the_short_shots_are_counted_and_told_which_gear_they_sit_in(
    attach: Attach, tmp_path: Path
) -> None:
    """Six shots under two seconds, four of them in the loud third — the bluntest gearing read.

    Where the short shots sit says what the averages can blur: a build that saves its fast
    cutting for the loud passages has changed gear whatever the rate ratio rounds to.
    """
    quiet = [("A.mp4", SECOND), ("B.mp4", SECOND), ("A.mp4", 10 * SECOND)]
    middling = [(f"{'CD'[turn % 2]}.mp4", 3 * SECOND) for turn in range(4)]
    loud = [(f"{'EF'[turn % 2]}.mp4", SECOND) for turn in range(4)] + [("G.mp4", 8 * SECOND)]
    attach(studio(timeline=_paced(*quiet, *middling, *loud)))

    gears = _gears(_measured(tmp_path, loudness=_levels(_steps(*THIRDS))))

    assert gears["sub2s_count"] == 6
    assert gears["sub2s_in_loud"] == 4
    assert gears["sub2s_loud_fraction"] == 0.667


def test_one_length_through_loud_and_quiet_alike_reads_as_one_speed(
    attach: Attach, tmp_path: Path
) -> None:
    """The finding the check exists for: the intro cut at the pace of the last chorus."""
    attach(studio(timeline=_paced(*[(f"{'AB'[turn % 2]}.mp4", 3 * SECOND) for turn in range(12)])))

    gears = _gears(_measured(tmp_path, loudness=_levels(_steps((-50.0, 18), (-20.0, 18)))))

    assert gears["terciles"]["quiet"]["cuts_per_minute"] == 20.0
    assert gears["terciles"]["loud"]["cuts_per_minute"] == 20.0
    assert gears["rate_ratio"] == 1.0
    assert gears["one_speed"] is True


def test_a_cut_that_changes_gear_is_not_one_speed_though_its_lengths_barely_vary(
    attach: Attach, tmp_path: Path
) -> None:
    """The rate arm alone: three-second shots in the quiet, two-second in the loud.

    The spread is well under its floor — this is nearly a metronome by length — and the
    reading still comes back false, because the cutting rate went up with the music.
    """
    quiet = [(f"{'AB'[turn % 2]}.mp4", 3 * SECOND) for turn in range(4)]
    middling = [(f"{'CD'[turn % 2]}.mp4", 3 * SECOND) for turn in range(4)]
    loud = [(f"{'EF'[turn % 2]}.mp4", 2 * SECOND) for turn in range(6)]
    attach(studio(timeline=_paced(*quiet, *middling, *loud)))

    result = _measured(tmp_path, loudness=_levels(_steps(*THIRDS)))
    rhythm, gears = _rhythm(result), _gears(result)

    assert gears["rate_ratio"] == 1.5  # thirty cuts a minute against twenty
    assert rhythm["uniformity"]["cv"] < correlate.GEAR_CV_FLOOR
    assert gears["one_speed"] is False


def test_a_cut_whose_lengths_vary_is_not_one_speed_though_its_rate_holds(
    attach: Attach, tmp_path: Path
) -> None:
    """The spread arm alone: the same three cuts in every gear, of wildly different lengths.

    The rate ratio is exactly one here, so the rate arm is inert — and a cut that runs a ten
    second shot against a one second shot is not cutting at one speed by any reading.
    """
    quiet = [("A.mp4", 10 * SECOND), ("B.mp4", SECOND), ("A.mp4", SECOND)]
    middling = [("C.mp4", 4 * SECOND), ("D.mp4", 4 * SECOND), ("C.mp4", 4 * SECOND)]
    loud = [("E.mp4", SECOND), ("F.mp4", SECOND), ("E.mp4", 10 * SECOND)]
    attach(studio(timeline=_paced(*quiet, *middling, *loud)))

    result = _measured(tmp_path, loudness=_levels(_steps(*THIRDS)))
    rhythm, gears = _rhythm(result), _gears(result)

    assert gears["rate_ratio"] == 1.0
    assert rhythm["uniformity"]["cv"] > correlate.GEAR_CV_FLOOR
    assert gears["one_speed"] is False


def test_a_cut_with_no_mix_named_has_no_gearing_rather_than_flat_gears(
    attach: Attach, tmp_path: Path
) -> None:
    """Nobody looked at the loudness, so the report says nothing about it.

    Flat terciles would read as a concert whose dynamics never moved, which is a finding
    about the music — and this measurement has not heard the music at all.
    """
    attach(studio(timeline=_paced(("A.mp4", SECOND), ("B.mp4", SECOND))))

    rhythm = _rhythm(_measured(tmp_path, audio=None))

    assert rhythm["gears"] is None
    assert rhythm["reads_metronomic"] is False  # the rest of the block is unaffected


def test_the_gearing_reading_carries_the_rule_it_applied(attach: Attach, tmp_path: Path) -> None:
    """A warning-shaped fact, like the metronome one: numbers, verdict, and the rule between."""
    attach(studio(timeline=_paced(*[(f"{'AB'[turn % 2]}.mp4", 3 * SECOND) for turn in range(12)])))

    gears = _gears(_measured(tmp_path))

    assert gears["heuristic"] == correlate.GEAR_HEURISTIC
    assert "warning" in gears["heuristic"]
    assert str(correlate.RATE_RATIO_FLOOR) in gears["heuristic"]
    assert str(correlate.GEAR_CV_FLOOR) in gears["heuristic"]
    assert set(gears) == {
        "window_seconds",
        "terciles",
        "outside_shots",
        "rate_ratio",
        "sub2s_count",
        "sub2s_in_loud",
        "sub2s_loud_fraction",
        "one_speed",
        "quiet_floor",
        "heuristic",
    }


def test_shots_past_the_end_of_the_analysed_mix_are_counted_apart_from_the_gears(
    attach: Attach, tmp_path: Path
) -> None:
    """A tail the curve never heard: thirty-six seconds of mix, forty-one seconds of cut.

    The tail is the trap. Pinned into the last window it would land whole in the loud third —
    ten fast cuts added to a tercile whose twelve seconds of music did not grow — and the
    report would read as a build that saved every short shot for the loudest passage and
    changed gear into it. The cut is the same two seconds a shot from end to end.
    """
    body = [(f"{'AB'[turn % 2]}.mp4", 2 * SECOND) for turn in range(18)]  # 0s .. 36s
    tail = [(f"{'CD'[turn % 2]}.mp4", SECOND // 2) for turn in range(10)]  # 36s .. 41s
    attach(studio(timeline=_paced(*body, *tail)))

    gears = _gears(_measured(tmp_path, loudness=_levels(_steps(*THIRDS))))

    assert gears["outside_shots"] == 10
    assert gears["terciles"]["loud"] == {
        "seconds": 12.0,
        "shots": 6,
        "cuts_per_minute": 30.0,
        "median_seconds": 2.0,
        "level_dbfs": -20.0,
    }
    assert gears["rate_ratio"] == 1.0
    assert gears["sub2s_count"] == 0  # every short shot sits where no window does
    assert gears["sub2s_in_loud"] == 0
    assert gears["sub2s_loud_fraction"] is None
    assert gears["one_speed"] is True


def test_a_cold_open_ahead_of_the_curve_is_counted_apart_from_the_gears(
    attach: Attach, tmp_path: Path
) -> None:
    """The other end of the same rule: the cut starts twelve seconds before the mix does.

    Clamped into the first window those opening shots would double the quiet third's cutting
    rate against unchanged seconds of music, and the report would hand back a gearing drawn
    from the part of the cut nothing measured.
    """
    curve = tuple((start + 12.0, level) for start, level in _steps(*THIRDS))
    opening = [(f"{'AB'[turn % 2]}.mp4", 2 * SECOND) for turn in range(6)]  # 0s .. 12s
    body = [(f"{'CD'[turn % 2]}.mp4", 2 * SECOND) for turn in range(18)]  # 12s .. 48s
    attach(studio(timeline=_paced(*opening, *body)))

    gears = _gears(_measured(tmp_path, loudness=_levels(curve)))

    assert gears["outside_shots"] == 6
    assert gears["terciles"]["quiet"]["shots"] == 6
    assert gears["terciles"]["quiet"]["cuts_per_minute"] == 30.0
    assert gears["rate_ratio"] == 1.0


def test_the_default_curve_is_read_off_the_mix_itself(attach: Attach, tmp_path: Path) -> None:
    """No injected curve: the levels come from the WAV, and the silent half is the quiet gear.

    The one test here that opens real audio, because the seam under it is the one nothing
    else can check — a curve fixture agrees with itself whatever ``measured_levels`` does.
    """
    mix = write_wav(
        tmp_path / "halves.wav", seconds=12.0, sample_rate=8_000, silence=[(0.0, 6.0)]
    )
    attach(studio(timeline=_paced(*[(f"{'AB'[turn % 2]}.mp4", 2 * SECOND) for turn in range(6)])))

    gears = _gears(_measured(tmp_path, audio=str(mix), loudness=None))

    assert gears["terciles"]["quiet"]["level_dbfs"] == energy.SILENCE_LUFS
    assert gears["terciles"]["quiet"]["shots"] == 2  # the first four seconds, both silent
    assert gears["terciles"]["loud"]["level_dbfs"] > -30.0
    assert gears["rate_ratio"] == 1.0  # one cut every two seconds throughout


# --- the quiet floor (#190) -------------------------------------------------------------
#
# A song in three even blocks — loud, quiet, mid, thirty seconds each — so the quiet third of
# the smoothed curve is exactly the middle block and every passage below runs 30.0 to 60.0.
# The shots either side of it are filler at a rate nothing here asserts; the reading under
# test is what happens between the thirtieth and the sixtieth second.

BLOCKS = ((-20.0, 30), (-50.0, 30), (-35.0, 30))
LOUD_FILLER = [("A.mp4", SECOND)] * 30
MID_FILLER = [("B.mp4", 2 * SECOND)] * 15


def _floor(result: dict[str, Any]) -> dict[str, Any]:
    block = _gears(result)["quiet_floor"]
    assert isinstance(block, dict)
    return block


def _passage(result: dict[str, Any]) -> dict[str, Any]:
    runs = _floor(result)["runs"]
    assert len(runs) == 1, f"one quiet block, so one passage: {runs}"
    passage: dict[str, Any] = runs[0]
    return passage


def _through(*quiet: float) -> FakeTimeline:
    """A cut whose middle stretch holds the given shot lengths, in seconds."""
    held = [(f"Q{turn}.mp4", round(length * SECOND)) for turn, length in enumerate(quiet)]
    return _paced(*LOUD_FILLER, *held, *MID_FILLER)


def test_a_quiet_passage_is_the_stretch_of_music_it_covers(
    attach: Attach, tmp_path: Path
) -> None:
    """Where the passage sits and how it was cut: the frame every other reading hangs on."""
    attach(studio(timeline=_through(8, 7, 8, 7)))

    passage = _passage(_measured(tmp_path, loudness=_levels(_steps(*BLOCKS))))

    assert passage["from"] == 30.0
    assert passage["to"] == 60.0
    assert passage["seconds"] == 30.0
    assert passage["shots"] == 4
    assert passage["cuts_per_minute"] == 8.0
    assert passage["median_seconds"] == 7.5


def test_a_floor_of_holds_all_much_one_length_reads_locked(
    attach: Attach, tmp_path: Path
) -> None:
    """Four holds inside a second of each other: the slow gear, driven at one speed.

    This is the failure the block exists for. Nothing above it can see this cut: the rate is
    the one the quiet gear asks for, the ratio against the loud third is right, and the whole
    cut's spread is carried by the fast material either side of this passage.
    """
    attach(studio(timeline=_through(8, 7, 8, 7)))

    floor = _floor(_measured(tmp_path, loudness=_levels(_steps(*BLOCKS))))

    assert floor["runs"][0]["cv"] == 0.067
    assert floor["runs"][0]["reads_locked"] is True
    assert floor["reads_locked"] is True


def test_a_floor_whose_lengths_move_does_not_read_locked(
    attach: Attach, tmp_path: Path
) -> None:
    """The same passage, the same rate, lengths that travel — one second out to eighteen."""
    attach(studio(timeline=_through(1, 3, 8, 18)))

    passage = _passage(_measured(tmp_path, loudness=_levels(_steps(*BLOCKS))))

    assert passage["cuts_per_minute"] == 8.0  # the rate the locked passage ran at
    assert passage["cv"] == 0.877
    assert passage["reads_locked"] is False


def test_the_spread_a_lone_flash_holds_up_is_not_the_passages_spread(
    attach: Attach, tmp_path: Path
) -> None:
    """Two long holds, one flash between them: the reading is the spread without the flash.

    The whole reason the orphans are dropped and the question asked again. Raw, this passage
    scores a spread over the floor — a single one-second shot beside a twelve and a thirteen
    moves a coefficient of variation a long way. What a viewer sits through is the holds.
    """
    attach(studio(timeline=_through(12, 1, 13, 4)))

    passage = _passage(_measured(tmp_path, loudness=_levels(_steps(*BLOCKS))))

    assert passage["cv"] == 0.683  # over the floor on its own
    assert passage["orphans"] == 1
    assert passage["orphan_seconds"] == [1.0]
    assert passage["cv_less_orphans"] == 0.417
    assert passage["reads_locked"] is True


def test_two_short_shots_side_by_side_are_a_burst_rather_than_orphans(
    attach: Attach, tmp_path: Path
) -> None:
    """A burst is a gesture the quiet floor is allowed to make, so it stays in the spread."""
    attach(studio(timeline=_through(12, 1, 1, 16)))

    passage = _passage(_measured(tmp_path, loudness=_levels(_steps(*BLOCKS))))

    assert passage["orphans"] == 0
    assert passage["cv_less_orphans"] == passage["cv"] == 0.887
    assert passage["reads_locked"] is False


def test_a_single_hold_across_the_passage_is_the_stillest_reading_there_is(
    attach: Attach, tmp_path: Path
) -> None:
    """One shot is a spread of zero, not an unreadable one — the most locked a floor can be."""
    attach(studio(timeline=_paced(*[("A.mp4", 2 * SECOND)] * 15, ("H.mp4", 30 * SECOND),
                                  *MID_FILLER)))

    passage = _passage(_measured(tmp_path, loudness=_levels(_steps(*BLOCKS))))

    assert passage["shots"] == 1
    assert passage["median_seconds"] == 30.0
    assert passage["cv"] == 0.0
    assert passage["reads_locked"] is True


def test_a_hold_that_runs_the_whole_passage_through_is_locked_by_that_alone(
    attach: Attach, tmp_path: Path
) -> None:
    """No shot starts inside, because one that started before it covers the lot.

    The stillest floor there is, and the one a spread cannot see: with no cut inside the
    passage there is no length to take a coefficient of variation over, and reading that as
    "no finding" would wave through the only case where nothing whatsoever happens. The shot's
    own length is the reading instead.
    """
    attach(studio(timeline=_paced(*[("A.mp4", 2 * SECOND)] * 14, ("H.mp4", 34 * SECOND),
                                  *[("B.mp4", 2 * SECOND)] * 14)))

    passage = _passage(_measured(tmp_path, loudness=_levels(_steps(*BLOCKS))))

    assert passage["shots"] == 0
    assert passage["cv"] is None  # nothing inside to take a spread over
    assert passage["held_through_seconds"] == 34.0
    assert passage["reads_locked"] is True


def test_a_quiet_pocket_too_short_to_sit_in_is_not_a_passage(
    attach: Attach, tmp_path: Path
) -> None:
    """Three ten-second dips: the quiet third of the music, and not one passage among them.

    A pocket holds two or three shots, and a spread over three shots is a number the report
    cannot mean anything by. The runs list is empty, which is not the same as passing — the
    heuristic says so in as many words.
    """
    pockets = [level for _ in range(3) for level in ((-50.0, 10), (-20.0, 20))]
    attach(studio(timeline=_paced(*[(f"{'AB'[turn % 2]}.mp4", 3 * SECOND) for turn in range(30)])))

    floor = _floor(_measured(tmp_path, loudness=_levels(_steps(*pockets))))

    assert floor["runs"] == []
    assert floor["reads_locked"] is False


def test_a_room_that_crosses_the_quiet_edge_and_back_is_one_passage_not_six(
    attach: Attach, tmp_path: Path
) -> None:
    """The reason the passages are found on a smoothed curve rather than on the gear labels.

    A live room does not go quiet and stay there: a crash, a shout, a chord puts one window
    back over the edge every few seconds. Read window by window this is six quiet pockets,
    none of them long enough to be anything — and the passage a viewer sat through for half a
    minute vanishes between them.
    """
    spiked = [level for _ in range(6) for level in ((-50.0, 4), (-20.0, 1))]
    attach(studio(timeline=_through(8, 7, 8, 7)))

    floor = _floor(_measured(tmp_path, loudness=_levels(_steps((-20.0, 30), *spiked, (-35.0, 30)))))

    assert [(run["from"], run["to"]) for run in floor["runs"]] == [(31.0, 61.0)]


def test_the_quiet_floor_reading_carries_the_rule_it_applied(
    attach: Attach, tmp_path: Path
) -> None:
    """Numbers, verdict, and the rule between them — the shape every warning here takes."""
    attach(studio(timeline=_through(8, 7, 8, 7)))

    floor = _floor(_measured(tmp_path, loudness=_levels(_steps(*BLOCKS))))

    assert floor["heuristic"] == correlate.FLOOR_HEURISTIC
    assert "warning" in floor["heuristic"]
    assert str(correlate.FLOOR_CV_FLOOR) in floor["heuristic"]
    assert str(correlate.ORPHAN_FRACTION) in floor["heuristic"]
    assert str(correlate.QUIET_FLOOR_SECONDS) in floor["heuristic"]
    assert floor["smoothing_windows"] == correlate.QUIET_SMOOTHING_WINDOWS
    assert set(floor) == {"smoothing_windows", "runs", "reads_locked", "heuristic"}
    assert set(floor["runs"][0]) == {
        "from",
        "to",
        "seconds",
        "shots",
        "cuts_per_minute",
        "median_seconds",
        "cv",
        "orphans",
        "orphan_seconds",
        "cv_less_orphans",
        "held_through_seconds",
        "reads_locked",
    }
