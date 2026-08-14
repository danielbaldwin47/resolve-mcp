"""The structure job: tune boundaries from applause, solo changes from the stems.

The tagger and the beat detector are both injectable (ADR 0002), so what is verified here is
every decision the worker makes around them — which boundaries land on disk, what comes back
inline, what a rerun costs, and which errors an agent gets — on a concert-shaped fixture of a
few seconds. Whether PANNs hears a room is the model's business.

The fixture is a concert in miniature: music, a burst of applause, music. The tagger is told
where the applause is; the file is what says where the last tune ends.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from resolve_mcp.analysis import applause as applause_module
from resolve_mcp.analysis import beats as beats_module
from resolve_mcp.analysis import decode, music, structure
from resolve_mcp.analysis import solos as solos_module
from resolve_mcp.analysis.beats import BeatGrid
from resolve_mcp.audio import stems as stems_module
from resolve_mcp.config import get_config
from resolve_mcp.jobs import store
from resolve_mcp.jobs.runner import wait_for
from resolve_mcp.tools import analysis as analysis_tools

from .fakes import write_sections, write_wav

SAMPLE_RATE = 8_000
TUNE_SECONDS = 4.0
APPLAUSE_SECONDS = 2.0
FRAME = 0.5

SOLO_SECONDS = 12.0
DOWNBEAT_OFFSET = 0.25


@pytest.fixture
def concert(tmp_path: Path) -> Path:
    """Four seconds of music, two of applause, four more — a set with two tunes in it."""
    return write_sections(
        tmp_path / "media" / "concert.wav",
        (("tone", TUNE_SECONDS), ("noise", APPLAUSE_SECONDS), ("tone", TUNE_SECONDS)),
        sample_rate=SAMPLE_RATE,
    )


def _tagger(*sections: tuple[float, float]) -> applause_module.Tagger:
    """A tagger that hears the room where the fixture puts it, frame by frame."""
    probability: list[float] = []
    for level, seconds in sections:
        probability.extend([level] * int(round(seconds / FRAME)))
    curve = applause_module.Curve(
        seconds=tuple(round(index * FRAME, 6) for index in range(len(probability))),
        probability=tuple(probability),
    )

    def tag(path: Path) -> applause_module.Curve:
        return curve

    return tag


def _heard() -> applause_module.Tagger:
    """The tagger for the fixture: quiet, a burst over the applause, quiet again."""
    return _tagger((0.05, TUNE_SECONDS), (0.9, APPLAUSE_SECONDS), (0.05, TUNE_SECONDS))


def _tagger_that_must_not_run() -> applause_module.Tagger:
    def tag(path: Path) -> applause_module.Curve:
        raise AssertionError("the tagger ran again for audio already analysed")

    return tag


def _grid(seconds: float = 24.0) -> BeatGrid:
    """A steady grid whose downbeats sit a quarter-second off the round numbers.

    Off the round numbers on purpose: a change point measured on a one-second hop lands on
    a whole second, so a snap that moved it is visible and one that did not is too.
    """
    beats = tuple(round(DOWNBEAT_OFFSET + index * 0.5, 6) for index in range(int(seconds / 0.5)))
    return BeatGrid(beats=beats, downbeats=beats[::4])


def _detector(grid: BeatGrid | None = None) -> beats_module.Detector:
    def detect(path: Path) -> BeatGrid:
        return grid if grid is not None else _grid()

    return detect


def _detector_that_must_not_run() -> beats_module.Detector:
    def detect(path: Path) -> BeatGrid:
        raise AssertionError("the beat model ran again for a grid already on disk")

    return detect


def _pulse_in_the_first_tune_only() -> beats_module.Detector:
    """A grid that stops when the first tune does — the second call has no pulse under it.

    Which is the shape the live pass found (#133): the tagger hears clapping on both sides
    of two minutes of talking and calls it a tune, and only the beat grid disagrees.
    """
    beats = tuple(
        round(DOWNBEAT_OFFSET + index * 0.5, 6) for index in range(int(TUNE_SECONDS / 0.5))
    )
    return _detector(BeatGrid(beats=beats, downbeats=beats[::4]))


def _stems(tmp_path: Path, seconds: float = 24.0) -> Path:
    """Three stems of a two-solo set: the vocal has the first half, the horns the second.

    The drums play throughout at one level, because a stem that never varies is the case
    the prominence measurement exists to rule out — it is the loudest stem in the room and
    it is not soloing.
    """
    directory = tmp_path / "stems" / "concert-abc123" / "mix"
    half = seconds / 2
    write_wav(
        directory / "concert_(Vocals)_model.wav",
        seconds=seconds,
        sample_rate=SAMPLE_RATE,
        silence=((half, seconds),),
    )
    write_wav(
        directory / "concert_(Other)_model.wav",
        seconds=seconds,
        sample_rate=SAMPLE_RATE,
        frequency=880.0,
        silence=((0.0, half),),
    )
    write_wav(
        directory / "concert_(Drums)_model.wav",
        seconds=seconds,
        sample_rate=SAMPLE_RATE,
        frequency=110.0,
        amplitude=0.5,
    )
    return directory.parent


def _third_pass(directory: Path, seconds: float = 24.0, half: bool = False) -> Path:
    """The opt-in third pass beside the mix pass: ``other`` taken apart into wind and comp.

    The two halves sum to the residual, which is the whole point of them — the horns take
    the first chorus of the second half of the set and the piano the next, so the handover
    that lived entirely inside ``other`` is now one stem falling as another lifts.

    ``half`` writes only the winds: a pass that died between its two files.
    """
    outer = directory / stems_module.OTHER_PASS
    middle = seconds / 2
    third = middle + seconds / 4
    write_wav(
        outer / "concert_(Woodwinds)_model.wav",
        seconds=seconds,
        sample_rate=SAMPLE_RATE,
        frequency=880.0,
        silence=((0.0, middle), (third, seconds)),
    )
    if not half:
        write_wav(
            outer / "concert_(No Woodwinds)_model.wav",
            seconds=seconds,
            sample_rate=SAMPLE_RATE,
            frequency=220.0,
            silence=((0.0, third),),
        )
    return directory


@pytest.fixture
def stems(tmp_path: Path) -> Path:
    return _stems(tmp_path)


@pytest.fixture
def split_stems(tmp_path: Path) -> Path:
    return _third_pass(_stems(tmp_path))


@pytest.fixture
def solo_audio(tmp_path: Path) -> Path:
    return write_wav(tmp_path / "media" / "set.wav", seconds=24.0, sample_rate=SAMPLE_RATE)


def _started(**kwargs: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "burst_seconds": 1.0,
        "gap_seconds": 1.0,
        "tune_seconds": 2.0,
        "settle_seconds": 1.0,
        "detector": _detector(),
    }
    settings.update(kwargs)
    return structure.analyze_structure(**settings)


def _finished(started: dict[str, Any]) -> store.JobRecord:
    return wait_for(started["job_id"])


def _result(started: dict[str, Any]) -> dict[str, Any]:
    record = _finished(started)
    assert record.state == "completed", record.error
    assert record.result is not None
    return record.result


def _solos(audio: Path, stems: Path, **kwargs: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "audio": audio,
        "tunes": False,
        "solos": True,
        "stems": stems,
        "solo_seconds": SOLO_SECONDS / 2,
        "detector": _detector(),
    }
    settings.update(kwargs)
    return _started(**settings)


# --- which stems the job reads ------------------------------------------------------------


def test_the_third_pass_halves_are_read_alongside_the_stems_from_the_first(
    split_stems: Path,
) -> None:
    """The wind pass writes its own directory, so nothing sees it unless this reaches for it."""
    found = structure._stems(split_stems, solos=True)

    assert sorted(found) == ["comp", "drums", "other", "vocals", "wind"]


def test_a_stems_directory_with_no_third_pass_reads_exactly_as_it_did(stems: Path) -> None:
    found = structure._stems(stems, solos=True)

    assert sorted(found) == ["drums", "other", "vocals"]


def test_the_mix_pass_directory_reaches_the_third_pass_beside_it(split_stems: Path) -> None:
    """An agent looking at the disk has the pass directory; the job record has its parent."""
    found = structure._stems(split_stems / stems_module.MIX_PASS, solos=True)

    assert sorted(found) == ["comp", "drums", "other", "vocals", "wind"]


def test_half_a_third_pass_is_no_third_pass(tmp_path: Path) -> None:
    """One half alone would join a voice set that still holds ``other`` — the residual twice."""
    found = structure._stems(_third_pass(_stems(tmp_path), half=True), solos=True)

    assert sorted(found) == ["drums", "other", "vocals"]


def test_a_split_stem_set_is_keyed_apart_from_an_unsplit_one() -> None:
    """The directory name is the same either way — the flag keys the job, not the stems (#153).

    Stems this server separated are keyed by that name, so the names of the stems in it are
    the only thing between a wind run and a residual run: without them the second run is
    served the first one's cached record.
    """
    inside = _stems(get_config().stems_dir.parent)

    plain = structure._stem_identity(structure._stems(inside, solos=True), get_config())
    split = structure._stem_identity(
        structure._stems(_third_pass(inside), solos=True), get_config()
    )

    assert "stems" in plain
    assert plain["stems"] == split["stems"]
    assert plain != split


def test_stems_from_outside_this_server_are_keyed_apart_too(tmp_path: Path) -> None:
    """The other branch of the same function: fingerprints, one per stem, not the name."""
    loose = structure._stems(_third_pass(_stems(tmp_path / "elsewhere")), solos=True)
    plain = {name: path for name, path in loose.items() if name not in solos_module.SPLIT}

    split_identity = structure._stem_identity(loose, get_config())

    assert "files" in split_identity
    assert split_identity != structure._stem_identity(plain, get_config())


# --- a board mix and a room mic ------------------------------------------------------------


BOARD_ANNOUNCEMENT_SECONDS = 6.0
BOARD_MUSIC_FROM = 16.0
"""Where the second tune actually starts in the board fixture: tone, room, silence, tone."""


@pytest.fixture
def board(tmp_path: Path) -> Path:
    """A board mix in miniature: music, a burst of room, a long announcement, music.

    The announcement is the part a room mic does not have and #179 is about — the applause
    ends six seconds before the band comes back in, so a boundary read off the clapping
    alone lands in the middle of somebody talking.
    """
    return write_sections(
        tmp_path / "media" / "board.wav",
        (
            ("tone", 8.0),
            ("noise", APPLAUSE_SECONDS),
            ("silence", BOARD_ANNOUNCEMENT_SECONDS),
            ("tone", 8.0),
        ),
        sample_rate=SAMPLE_RATE,
    )


def _barely_heard() -> applause_module.Tagger:
    """The board mix's tagger: a burst that never comes near the threshold, as measured."""
    return _tagger(
        (0.001, 8.0),
        (0.2, APPLAUSE_SECONDS),
        (0.001, BOARD_ANNOUNCEMENT_SECONDS + 8.0),
    )


@pytest.fixture
def room(tmp_path: Path) -> Path:
    """A room mic in miniature: the crowd is loud and goes on long enough to be a set's worth."""
    return write_sections(
        tmp_path / "media" / "room.wav",
        (("tone", 6.0), ("noise", 12.0), ("tone", 6.0)),
        sample_rate=SAMPLE_RATE,
    )


def _room_heard() -> applause_module.Tagger:
    return _tagger((0.05, 6.0), (0.9, 12.0), (0.05, 6.0))


def test_a_mix_the_threshold_finds_no_clapping_in_is_read_at_its_own_scale(board: Path) -> None:
    """The #179 case: a whole set under the threshold, so the threshold is not one here."""
    result = _result(_started(audio=board, tagger=_barely_heard()))

    assert result["tunes"]["read_at_own_scale"] is True
    assert result["tunes"]["threshold_used"] < applause_module.DEFAULT_THRESHOLD
    assert result["tunes"]["threshold"] == applause_module.DEFAULT_THRESHOLD
    assert result["tunes"]["applause_count"] == 1
    assert result["tunes"]["count"] == 2


def test_a_mix_with_an_audible_crowd_is_read_where_it_always_was(room: Path) -> None:
    """The regression that matters: the fallback stays off for a mix the tagger is sure of."""
    result = _result(_started(audio=room, tagger=_room_heard()))

    assert result["tunes"]["read_at_own_scale"] is False
    assert result["tunes"]["threshold_used"] == applause_module.DEFAULT_THRESHOLD
    assert result["tunes"]["burst_seconds_used"] == 1.0


def test_an_audible_crowd_gets_the_boundaries_the_applause_alone_would_have_given(
    room: Path,
) -> None:
    """The other half of the same regression: the tunes, not just the numbers they were read at.

    The band plays either side of the clapping with no announcement between, which is what a
    room mic sounds like — so the settle step has nothing to move and every boundary has to
    come back exactly where the applause put it.
    """
    settled = _result(_started(audio=room, tagger=_room_heard()))
    unsettled = _result(_started(audio=room, tagger=_room_heard(), settle_seconds=0.0))

    written = json.loads(Path(settled["tunes"]["path"]).read_text(encoding="utf-8"))
    before = json.loads(Path(unsettled["tunes"]["path"]).read_text(encoding="utf-8"))

    assert [(one["t"], one["end"]) for one in written["tunes"]] == [(0.0, 6.0), (18.0, 24.0)]
    assert [one["t"] for one in written["tunes"]] == [one["t"] for one in before["tunes"]]
    assert settled["tunes"]["count"] == unsettled["tunes"]["count"] == 2
    assert settled["tunes"]["settled_seconds"] == 0.0


def test_a_boundary_lands_where_the_band_comes_in_not_where_the_clapping_stopped(
    board: Path,
) -> None:
    result = _result(_started(audio=board, tagger=_barely_heard()))

    written = json.loads(Path(result["tunes"]["path"]).read_text(encoding="utf-8"))
    second = written["tunes"][1]

    assert second["t"] == pytest.approx(BOARD_MUSIC_FROM, abs=2.5)
    assert second["talk_seconds"] > 3.0
    assert result["tunes"]["settled"] == 1


def test_turning_the_settle_step_off_puts_the_boundary_back_on_the_applause(
    board: Path,
) -> None:
    """The way to run the tune half with no loudness curve, and what it costs."""
    result = _result(_started(audio=board, tagger=_barely_heard(), settle_seconds=0.0))

    written = json.loads(Path(result["tunes"]["path"]).read_text(encoding="utf-8"))

    assert written["tunes"][1]["t"] == 10.0
    assert written["tunes"][1]["talk_seconds"] is None
    assert result["tunes"]["settled"] == 0


def test_a_call_the_band_never_comes_in_on_is_recorded_with_its_reason(board: Path) -> None:
    """A tagger that hears the room where the announcement is calls the silence a tune."""
    heard_the_wrong_way = _tagger(
        (0.001, 8.0),
        (0.2, APPLAUSE_SECONDS),
        (0.001, 1.0),
        (0.2, APPLAUSE_SECONDS),
        (0.001, BOARD_ANNOUNCEMENT_SECONDS + 3.0),
    )
    result = _result(
        _started(audio=board, tagger=heard_the_wrong_way, tune_seconds=1.0, gap_seconds=0.5)
    )

    written = json.loads(Path(result["tunes"]["path"]).read_text(encoding="utf-8"))

    assert result["tunes"]["no_music"] >= 1
    assert any("music" in one["reason"] for one in written["quiet_calls"])


# --- what lands on disk ----------------------------------------------------------------


def test_tune_boundaries_land_on_disk_with_the_applause_between_them(concert: Path) -> None:
    result = _result(_started(audio=concert, tagger=_heard()))

    written = json.loads(Path(result["tunes"]["path"]).read_text(encoding="utf-8"))

    assert [(one["t"], one["end"]) for one in written["tunes"]] == [(0.0, 4.0), (6.0, 10.0)]
    assert written["tunes"][0]["applause_after"] == 2.0
    assert written["tunes"][1]["applause_before"] == 2.0
    assert written["tunes"][1]["applause_after"] is None


def test_the_last_tune_ends_where_the_file_does(concert: Path) -> None:
    """The tagger says where the room is; only the audio says where the set stops."""
    result = _result(_started(audio=concert, tagger=_heard()))

    written = json.loads(Path(result["tunes"]["path"]).read_text(encoding="utf-8"))

    assert written["tunes"][-1]["end"] == pytest.approx(written["duration_seconds"], abs=0.01)


def test_the_tunes_file_is_readable_in_slices(concert: Path) -> None:
    result = _result(_started(audio=concert, tagger=_heard()))

    lines = Path(result["tunes"]["path"]).read_text(encoding="utf-8").splitlines()
    rows = [line for line in lines if line.lstrip().startswith('{"tune":')]

    assert len(rows) == result["tunes"]["count"] == 2


def test_a_call_with_no_pulse_under_it_never_reaches_the_tunes_file(concert: Path) -> None:
    """Clapping on both sides of talking is not a tune, and the beat grid is what says so."""
    result = _result(
        _started(audio=concert, tagger=_heard(), detector=_pulse_in_the_first_tune_only())
    )

    written = json.loads(Path(result["tunes"]["path"]).read_text(encoding="utf-8"))

    assert [(one["t"], one["end"]) for one in written["tunes"]] == [(0.0, 4.0)]
    assert result["tunes"]["count"] == 1
    assert result["tunes"]["dropped"] == 1


def test_the_dropped_call_is_on_disk_with_the_reason_it_was_dropped(concert: Path) -> None:
    """Out of the tune set but not out of the record — the filter has to be auditable."""
    result = _result(
        _started(audio=concert, tagger=_heard(), detector=_pulse_in_the_first_tune_only())
    )

    written = json.loads(Path(result["tunes"]["path"]).read_text(encoding="utf-8"))

    assert [(one["t"], one["end"]) for one in written["dropped_calls"]] == [(6.0, 10.0)]
    assert written["dropped_calls"][0]["beats_per_second"] == 0.0
    assert "no pulse" in written["dropped_calls"][0]["reason"]


def test_the_dropped_calls_stay_out_of_what_comes_back_inline(concert: Path) -> None:
    """The gist is stats; the rejects are boundaries, so they live on disk with the rest."""
    result = _result(
        _started(audio=concert, tagger=_heard(), detector=_pulse_in_the_first_tune_only())
    )

    assert "dropped_calls" not in result["tunes"]
    assert not any(isinstance(value, list) for value in result["tunes"].values())


def test_a_kept_tune_carries_the_density_it_was_kept_on(concert: Path) -> None:
    result = _result(
        _started(audio=concert, tagger=_heard(), detector=_pulse_in_the_first_tune_only())
    )

    written = json.loads(Path(result["tunes"]["path"]).read_text(encoding="utf-8"))

    assert written["tunes"][0]["beats"] == 8
    assert written["tunes"][0]["beats_per_second"] == 2.0


def test_a_floor_of_zero_keeps_every_call_and_never_reads_a_grid(concert: Path) -> None:
    """The escape hatch is also the way to run this half with no beat model installed."""
    result = _result(
        _started(
            audio=concert,
            tagger=_heard(),
            detector=_detector_that_must_not_run(),
            density_per_second=0.0,
        )
    )

    written = json.loads(Path(result["tunes"]["path"]).read_text(encoding="utf-8"))

    assert [(one["t"], one["end"]) for one in written["tunes"]] == [(0.0, 4.0), (6.0, 10.0)]
    assert result["tunes"]["dropped"] == 0
    assert written["tunes"][1]["beats_per_second"] is None


def test_solo_changes_land_on_disk_snapped_to_a_downbeat(solo_audio: Path, stems: Path) -> None:
    result = _result(_solos(solo_audio, stems))

    written = json.loads(Path(result["solos"]["path"]).read_text(encoding="utf-8"))
    change = written["solos"][0]

    assert change["measured_t"] == pytest.approx(SOLO_SECONDS, abs=2.0)
    assert change["t"] in _grid().downbeats
    assert abs(change["t"] - change["measured_t"]) <= solos_module.DEFAULT_SNAP_SECONDS
    assert change["downbeat"] is True
    assert change["from"] == "vocals"
    assert change["to"] == "other"
    assert change["signal"] == "lead"


def test_the_steady_stem_never_takes_the_front(solo_audio: Path, stems: Path) -> None:
    """The drums are the loudest stem in the mix and are not soloing in this fixture."""
    result = _result(_solos(solo_audio, stems))

    written = json.loads(Path(result["solos"]["path"]).read_text(encoding="utf-8"))

    assert "drums" not in {one["to"] for one in written["solos"]}
    assert "drums" not in written["seconds_in_front"]


def test_the_two_halves_are_separate_files(solo_audio: Path, stems: Path) -> None:
    result = _result(
        _solos(solo_audio, stems, tunes=True, tagger=_tagger((0.05, 24.0)))
    )

    assert result["tunes"]["path"] != result["solos"]["path"]
    assert Path(result["tunes"]["path"]).parent == get_config().analysis_dir


# --- what comes back inline -------------------------------------------------------------


def test_gist_stats_come_back_inline_and_the_boundaries_do_not(concert: Path) -> None:
    result = _result(_started(audio=concert, tagger=_heard()))

    assert result["tunes"]["count"] == 2
    assert result["tunes"]["applause_count"] == 1
    assert result["tunes"]["applause_seconds"] == 2.0
    assert result["tunes"]["longest"]["seconds"] == 4.0
    assert result["audio"]["duration_seconds"] == pytest.approx(10.0, abs=0.01)
    assert not any(isinstance(value, list) for value in result["tunes"].values())


def test_the_solo_gist_says_who_held_the_front_and_how_much_snapped(
    solo_audio: Path,
    stems: Path,
) -> None:
    result = _result(_solos(solo_audio, stems))

    assert result["solos"]["count"] == 1
    assert result["solos"]["snapped"] == 1
    assert result["solos"]["longest_lead"]["stem"] in {"vocals", "other"}
    assert result["solos"]["stem_count"] == 3
    assert result["solos"]["timbre_stem"] == "other"
    assert result["solos"]["voices"] == "drums, other, vocals"


def test_the_solo_gist_says_what_it_measured_once_the_winds_are_their_own_stem(
    solo_audio: Path,
    split_stems: Path,
) -> None:
    """Which stems were voices and which one the brightness came off — a wind run from a not.

    Without this the record cannot be read back: the same directory, the same job settings
    and two different measurements, with nothing in the gist saying which one ran.
    """
    result = _result(_solos(solo_audio, split_stems))

    assert result["solos"]["timbre_stem"] == "wind"
    assert result["solos"]["voices"] == "comp, drums, vocals, wind"
    assert result["solos"]["stem_count"] == 4
    assert not any(isinstance(value, list) for value in result["solos"].values())


def test_the_winds_handing_over_to_the_comp_is_a_lead_change_on_disk(
    solo_audio: Path,
    split_stems: Path,
) -> None:
    """The handover timbre used to be the only witness of, named at both ends and in dB."""
    result = _result(_solos(solo_audio, split_stems, solo_seconds=4.0))

    written = json.loads(Path(result["solos"]["path"]).read_text(encoding="utf-8"))
    handover = [one for one in written["solos"] if {one["from"], one["to"]} == {"wind", "comp"}]

    assert handover
    assert handover[0]["signal"] == "lead"
    assert handover[0]["detail"] > 0.0
    assert "other" not in {one["to"] for one in written["solos"]}


# --- what a rerun costs -----------------------------------------------------------------


def test_a_rerun_does_not_tag_the_room_again(concert: Path) -> None:
    _result(_started(audio=concert, tagger=_heard()))

    result = _result(_started(audio=concert, tagger=_tagger_that_must_not_run()))

    assert result["tunes"]["count"] == 2


def test_refresh_tags_the_room_again(concert: Path) -> None:
    _result(_started(audio=concert, tagger=_heard()))

    started = _started(audio=concert, tagger=_tagger_that_must_not_run(), refresh=True)

    record = _finished(started)
    assert record.state == "failed"


def test_a_rerun_does_not_read_the_stems_again(
    solo_audio: Path,
    stems: Path,
    monkeypatch: Any,
) -> None:
    """Separated stems are gigabytes of WAV; a second answer comes off the cache entry."""
    first = _result(_solos(solo_audio, stems))

    def must_not_run(path: Path | str) -> Any:
        raise AssertionError("the stems were decoded again for a set already analysed")

    monkeypatch.setattr(decode, "read", must_not_run)
    second = _result(_solos(solo_audio, stems))

    assert second["solos"] == first["solos"]


def test_solo_changes_reuse_the_beat_grid_music_analysis_already_wrote(
    solo_audio: Path,
    stems: Path,
) -> None:
    """One grid per piece of audio: the second job to want downbeats reads the first's."""
    _result(music.analyze_music(solo_audio, energy=False, detector=_detector()))

    result = _result(_solos(solo_audio, stems, detector=_detector_that_must_not_run()))

    assert result["solos"]["count"] == 1


def test_music_analysis_reuses_the_beat_grid_this_job_wrote(
    solo_audio: Path,
    stems: Path,
) -> None:
    _result(_solos(solo_audio, stems))

    beats = _result(
        music.analyze_music(solo_audio, energy=False, detector=_detector_that_must_not_run())
    )

    assert beats["beats"]["count"] > 0


def test_the_tune_half_reuses_the_beat_grid_music_analysis_already_wrote(concert: Path) -> None:
    """The density check pays for no second detection either — same entry, same rule."""
    _result(music.analyze_music(concert, energy=False, detector=_pulse_in_the_first_tune_only()))

    result = _result(
        _started(audio=concert, tagger=_heard(), detector=_detector_that_must_not_run())
    )

    assert result["tunes"]["count"] == 1


def test_music_analysis_reuses_the_beat_grid_the_tune_half_wrote(concert: Path) -> None:
    _result(_started(audio=concert, tagger=_heard()))

    beats = _result(
        music.analyze_music(concert, energy=False, detector=_detector_that_must_not_run())
    )

    assert beats["beats"]["count"] > 0


def test_moving_the_density_floor_gets_its_own_answer(concert: Path) -> None:
    """Two floors are two results, so they are keyed apart rather than sharing an entry."""
    loose = _result(
        _started(
            audio=concert,
            tagger=_heard(),
            detector=_pulse_in_the_first_tune_only(),
            density_per_second=0.0,
        )
    )

    strict = _result(
        _started(
            audio=concert,
            tagger=_heard(),
            detector=_pulse_in_the_first_tune_only(),
            density_per_second=0.5,
        )
    )

    assert loose["tunes"]["count"] == 2
    assert strict["tunes"]["count"] == 1
    assert loose["tunes"]["path"] != strict["tunes"]["path"]


def test_a_finer_solo_window_does_not_re_tag_the_room(concert: Path, stems: Path) -> None:
    """The halves are keyed apart, so changing one does not pay for the other again."""
    _result(_solos(concert, stems, tunes=True, tagger=_heard()))

    result = _result(
        _solos(
            concert,
            stems,
            tunes=True,
            tagger=_tagger_that_must_not_run(),
            hop_seconds=0.5,
        )
    )

    assert result["tunes"]["count"] == 2


# --- what an agent gets when it is wrong ------------------------------------------------


def test_asking_for_solos_without_stems_is_refused_before_any_job_starts(
    concert: Path,
) -> None:
    with pytest.raises(Exception, match="stems"):
        _started(audio=concert, solos=True, tunes=False)

    assert store.load_all() == []


def test_asking_for_neither_half_is_refused(concert: Path) -> None:
    with pytest.raises(Exception, match="[Nn]either"):
        _started(audio=concert, tunes=False, solos=False)

    assert store.load_all() == []


def test_a_stems_directory_with_no_stems_in_it_is_refused(concert: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(Exception, match="separated stems"):
        _started(audio=concert, solos=True, tunes=False, stems=empty)


def test_an_impossible_threshold_is_refused(concert: Path) -> None:
    with pytest.raises(Exception, match="probability"):
        _started(audio=concert, threshold=1.5)


def test_a_negative_density_floor_is_refused(concert: Path) -> None:
    with pytest.raises(Exception, match="density"):
        _started(audio=concert, density_per_second=-1.0)

    assert store.load_all() == []


def test_a_negative_settle_margin_is_refused(concert: Path) -> None:
    """It would put playing level above the file's median and call every tune silent."""
    with pytest.raises(Exception, match="settle"):
        _started(audio=concert, settle_db=-6.0)

    assert store.load_all() == []


def test_a_scale_that_is_not_a_fraction_is_refused(concert: Path) -> None:
    with pytest.raises(Exception, match="scale"):
        _started(audio=concert, scale=1.5)

    assert store.load_all() == []


def test_a_missing_tagger_names_the_install(concert: Path, monkeypatch: Any) -> None:
    real = importlib.import_module

    def missing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == applause_module.MODULE:
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", missing)

    record = _finished(_started(audio=concert))

    assert record.error is not None
    assert record.error["code"] == "analysis_dependency_missing"
    assert "panns" in record.error["fix"]


def test_a_missing_beat_model_names_the_way_to_run_without_one(
    concert: Path,
    monkeypatch: Any,
) -> None:
    """beats' own advice is beats=false, which is no help to a caller asking for tunes."""
    real = importlib.import_module

    def missing(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == beats_module.MODULE:
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", missing)

    record = _finished(_started(audio=concert, tagger=_heard(), detector=None))

    assert record.error is not None
    assert record.error["code"] == "analysis_dependency_missing"
    assert "density_per_second=0" in record.error["fix"]
    assert "beat_this" in record.error["fix"]


def test_a_tagger_that_falls_over_is_an_analysis_failure(concert: Path) -> None:
    def broken(path: Path) -> applause_module.Curve:
        raise RuntimeError("out of memory")

    record = _finished(_started(audio=concert, tagger=broken))

    assert record.error is not None
    assert record.error["code"] == "analysis_failed"
    assert "out of memory" in record.error["cause"]


# --- the tool seam ------------------------------------------------------------------------


def test_the_tool_returns_a_job_and_never_the_boundaries(concert: Path) -> None:
    envelope = analysis_tools.analyze_structure(str(concert))

    assert envelope["ok"] is True
    assert envelope["job"]["kind"] == structure.KIND
    assert "tunes" not in envelope


def test_the_tool_hands_the_density_floor_to_the_job(concert: Path) -> None:
    """Refused rather than started, which is the cheap proof the value reached the starter."""
    envelope = analysis_tools.analyze_structure(str(concert), density_per_second=-1.0)

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "invalid_request"
    assert "density_per_second" in envelope["error"]["fix"]
    assert store.load_all() == []


def test_the_tool_is_registered() -> None:
    assert analysis_tools.analyze_structure in analysis_tools.TOOLS
