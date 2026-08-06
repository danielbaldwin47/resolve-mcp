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


@pytest.fixture
def stems(tmp_path: Path) -> Path:
    return _stems(tmp_path)


@pytest.fixture
def solo_audio(tmp_path: Path) -> Path:
    return write_wav(tmp_path / "media" / "set.wav", seconds=24.0, sample_rate=SAMPLE_RATE)


def _started(**kwargs: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "burst_seconds": 1.0,
        "gap_seconds": 1.0,
        "tune_seconds": 2.0,
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
    assert result["solos"]["residual"] is True


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


def test_the_tool_is_registered() -> None:
    assert analysis_tools.analyze_structure in analysis_tools.TOOLS
