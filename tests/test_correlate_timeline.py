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


def beats_file(tmp_path: Path, seconds: Sequence[float] = BEAT_SECONDS) -> Path:
    """A beats file in the shape analyze_music writes: header, then one record per line.

    Written once per test: the cache keys off the file's mtime, so a second identical write
    would look like new analysis and no rerun would ever be answered from cache.
    """
    target = tmp_path / "concert-beats.json"
    if target.exists():
        return target
    grid = BeatGrid(tuple(seconds), tuple(seconds[::4]))
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


def test_a_shot_that_starts_after_a_gap_opens_rather_than_cuts(
    attach: Attach, tmp_path: Path
) -> None:
    """There is no outgoing angle at that frame, so its distance from the beat says nothing."""
    gapped = (SHOTS[0], ("C0031.mp4", 200, 87, 4200))
    attach(studio(timeline=a_cut(shots=gapped)))

    result = _measured(tmp_path)

    assert [one["opening"] for one in _rows(result)] == [True, True]
    assert result["openings"] == 2
    assert result["beat_offsets"] is None


def test_cuts_past_the_end_of_the_grid_are_counted_rather_than_pinned_quietly(
    attach: Attach, tmp_path: Path
) -> None:
    """The nearest-beat lookup clamps, so a wrong clock otherwise produces well-formed nonsense."""
    far = (*SHOTS, ("C0031.mp4", 700, 60, 5000))  # 10s in, against six seconds of analysis
    attach(studio(timeline=a_cut(shots=far)))

    result = _measured(tmp_path)

    assert result["outside_grid"] == 1
    assert result["cuts"] == 4


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
