"""The one thing every analysis file has in common: how it opens.

An agent reads these files by grepping the head of one — ``kind`` says what the measurement
is, ``audio`` says what it was taken off, ``duration_seconds`` says how much of it there was —
and that only works while every detector writes the same three keys in the same order. Nothing
enforced it: ``halves.written`` built the header for music and structure while bars, phrases
and fills hand-rolled their own copy, and a copy is a thing that drifts (#223).

So this file runs every half that writes through the writer, over the smallest fixtures each
one will accept, and compares the heads. It is deliberately the only test that knows about all
of them at once: the per-detector tests own what each file *says*, and this one owns what they
all say the same way. The fakes come from those tests rather than from copies here, so a
detector whose inputs change breaks this in one place with the rest of its own suite. The two
stem layouts below are the exception, and only because their originals are pytest fixtures
rather than callables: a fixture cannot be imported and called from another module's test.

Two files in the analysis directory are not halves and are absent on purpose: ``correlate``'s
report, a join over cut rows rather than a measurement of one audio file (no ``audio`` to
name), and the transcript, a schema-versioned document with three record lists rather than
one. Both are the exceptions ``halves.written`` documents, and both keep their own writers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from resolve_mcp.analysis import bars as bars_module
from resolve_mcp.analysis import fills as fills_module
from resolve_mcp.analysis import music as music_module
from resolve_mcp.analysis import phrases as phrases_module
from resolve_mcp.analysis import structure as structure_module
from resolve_mcp.audio.stems import DRUM_PASS, DRUM_STEMS, FOUR_STEMS, MIX_PASS
from resolve_mcp.jobs.runner import wait_for

from .fakes import write_clicks, write_hits, write_sections, write_wav
from .test_bar_map import _accent_of, _accented, _onset_scale
from .test_bar_map import _detector as _bar_detector
from .test_drum_fills import _detector_for as _fill_detector
from .test_drum_fills import _grid as _fill_grid
from .test_drum_fills import _played
from .test_music_analysis import _detector as _music_detector
from .test_music_analysis import _grid as _music_grid
from .test_music_structure import APPLAUSE_SECONDS, SAMPLE_RATE, TUNE_SECONDS, _heard
from .test_music_structure import _detector as _structure_detector
from .test_music_structure import _stems as _solo_stems
from .test_phrases import _detector_for as _phrase_detector
from .test_phrases import _grid as _phrase_grid
from .test_phrases import _read, _two_phrases

HEADER = ("kind", "audio", "duration_seconds")
"""What every analysis file opens with, in this order. The contract this file exists for."""

SOLO_SECONDS = 24.0


def _result(started: Mapping[str, Any]) -> dict[str, Any]:
    record = wait_for(str(started["job_id"]))
    assert record.state == "completed", record.error
    assert record.result is not None
    return record.result


def _mix_stems(tmp_path: Path) -> dict[str, Path]:
    """A four-stem separation, the shape the separation job reports it in."""
    directory = tmp_path / "stems" / "concert-abc123def456" / MIX_PASS
    return {
        label: write_hits(directory / f"concert_({label.title()})_model.wav", times=(1.0,))
        for label in FOUR_STEMS
    }


def _drum_stems(tmp_path: Path) -> dict[str, Path]:
    """The kit taken apart — what the fill job counts hits across."""
    directory = tmp_path / "kit" / "concert-abc123def456" / DRUM_PASS
    return {
        label: write_hits(directory / f"concert_({label.title()})_model.wav", times=())
        for label in DRUM_STEMS
    }


def _master(tmp_path: Path, seconds: float) -> Path:
    """The mix a bar map is read off — silent, since the accent reading is injected."""
    return write_hits(tmp_path / "media" / "master.wav", [], seconds=seconds)


def _every_report(tmp_path: Path) -> dict[str, dict[str, Any]]:
    """Run every detector that writes an analysis file, and read back what each one wrote."""
    written: dict[str, Path] = {}

    # music: both halves at once, off a click track.
    clicks = write_clicks(tmp_path / "media" / "clicks.wav", seconds=8.0)
    music = _result(
        music_module.analyze_music(clicks, detector=_music_detector(_music_grid(seconds=8.0)))
    )
    written[music_module.BEATS] = Path(music[music_module.BEATS]["path"])
    written[music_module.ENERGY] = Path(music[music_module.ENERGY]["path"])

    # structure: the tune half off a set with applause in it, the solo half off the stems.
    concert = write_sections(
        tmp_path / "media" / "concert.wav",
        (("tone", TUNE_SECONDS), ("noise", APPLAUSE_SECONDS), ("tone", TUNE_SECONDS)),
        sample_rate=SAMPLE_RATE,
    )
    tunes = _result(
        structure_module.analyze_structure(
            concert,
            burst_seconds=1.0,
            gap_seconds=1.0,
            tune_seconds=2.0,
            settle_seconds=1.0,
            tagger=_heard(),
            detector=_structure_detector(),
        )
    )
    written[structure_module.TUNES] = Path(tunes[structure_module.TUNES]["path"])

    set_audio = write_wav(
        tmp_path / "media" / "set.wav", seconds=SOLO_SECONDS, sample_rate=SAMPLE_RATE
    )
    solos = _result(
        structure_module.analyze_structure(
            set_audio,
            tunes=False,
            solos=True,
            stems=_solo_stems(tmp_path, seconds=SOLO_SECONDS),
            solo_seconds=SOLO_SECONDS / 4,
            detector=_structure_detector(),
        )
    )
    written[structure_module.SOLOS] = Path(solos[structure_module.SOLOS]["path"])

    # bars: the G2 grid and an accent reading that puts the bar line every eight beats.
    grid = _onset_scale()
    bars = _result(
        bars_module.detect_bars(
            _master(tmp_path, float(grid[-1]["t"]) + 1.0),
            detector=_bar_detector(grid),
            accent=_accent_of(_accented(grid, every=8)),
        )
    )
    written[bars_module.BARS] = Path(bars["path"])

    # phrases: a line with a rest in it, off the residual stem.
    phrase_grid = _phrase_grid()
    phrases = _result(
        phrases_module.detect_phrases(
            _mix_stems(tmp_path),
            write_clicks(tmp_path / "media" / "line.wav", seconds=16.0),
            detector=_phrase_detector(phrase_grid),
            reader=_read(_two_phrases()),
        )
    )
    written[phrases_module.PHRASES] = Path(phrases["path"])

    # fills: comping with a fill in it, off the kit.
    fill_grid = _fill_grid()
    fills = _result(
        fills_module.detect_drum_fills(
            _drum_stems(tmp_path),
            write_clicks(tmp_path / "media" / "kit.wav", seconds=16.0),
            detector=_fill_detector(fill_grid),
            transcriber=_played(fill_grid),
        )
    )
    written[fills_module.FILLS] = Path(fills["path"])

    return {
        kind: json.loads(path.read_text(encoding="utf-8")) for kind, path in written.items()
    }


def test_every_analysis_file_opens_with_the_same_header_keys(tmp_path: Path) -> None:
    """One header for every detector — the promise ``halves.written`` is there to keep."""
    reports = _every_report(tmp_path)

    assert set(reports) == {
        music_module.BEATS,
        music_module.ENERGY,
        structure_module.TUNES,
        structure_module.SOLOS,
        bars_module.BARS,
        phrases_module.PHRASES,
        fills_module.FILLS,
    }
    assert {kind: tuple(list(document)[: len(HEADER)]) for kind, document in reports.items()} == {
        kind: HEADER for kind in reports
    }
    # And the header is about the file it sits in: the kind names both the measurement and
    # the field the records are under.
    for kind, document in reports.items():
        assert document["kind"] == kind
        assert kind in document
