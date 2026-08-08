"""Notes off the melodic stem: the pitch estimate, the gating, and the reading seam (#143).

Pitch is checked on arrays built here rather than on a file, because the thing under test is
arithmetic and a WAV in the middle only adds a decoder to the list of things that could be
wrong. The note layer is checked on fixture audio, because *that* is where the decisions
live — where a note is judged to have started and stopped is the whole input to phrase
detection, and a fixture whose note times are known is the only way to check it.

Tolerances are stated per assertion and are frame-scale (tens of milliseconds), not
phrase-scale: the reader is allowed to be half a window out, and the phrase tier that
consumes it is allowed a few hundred milliseconds on top (#143).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from resolve_mcp.analysis import melody
from resolve_mcp.analysis.melody import Note
from resolve_mcp.errors import AnalysisFailedError

from .fakes import write_sections, write_tones

SAMPLE_RATE = 16_000
"""Fast fixtures. The reader sizes its window in seconds, so nothing here depends on 48k."""


def _tone(
    hertz: float,
    seconds: float = 0.1,
    sample_rate: int = SAMPLE_RATE,
) -> NDArray[np.float64]:
    """One frame's worth of a sine, the shape ``pitch`` is handed."""
    steps = np.arange(int(seconds * sample_rate), dtype=np.float64)
    return np.sin(2.0 * math.pi * hertz * steps / sample_rate)


# --- the pitch estimate -------------------------------------------------------------


@pytest.mark.parametrize("hertz", [110.0, 220.0, 440.0, 880.0, 1_320.0])
def test_a_sine_reads_back_as_its_own_frequency(hertz: float) -> None:
    """Within 1%: below that the semitone arithmetic downstream stops meaning anything."""
    assert melody.pitch(_tone(hertz), SAMPLE_RATE) == pytest.approx(hertz, rel=0.01)


def test_a_note_with_a_strong_second_harmonic_reads_as_its_fundamental() -> None:
    """A horn is not a sine. The octave above is the loudest partial and is not the note."""
    both = _tone(440.0) + _tone(880.0)

    assert melody.pitch(both, SAMPLE_RATE) == pytest.approx(440.0, rel=0.01)


def test_silence_is_not_pitched() -> None:
    assert melody.pitch(np.zeros(1_600), SAMPLE_RATE) == 0.0


def test_noise_is_not_pitched() -> None:
    """A cymbal wash in the residual stem must not be reported as a note with a pitch."""
    noise = np.random.default_rng(7).normal(size=1_600)

    assert melody.pitch(noise, SAMPLE_RATE) == 0.0


def test_a_frame_with_nothing_in_it_is_not_pitched() -> None:
    assert melody.pitch(np.zeros(1), SAMPLE_RATE) == 0.0


# --- notes off a stem ---------------------------------------------------------------


def test_three_separated_notes_read_back_as_three_notes(tmp_path: Path) -> None:
    stem = write_tones(
        tmp_path / "other.wav",
        notes=[(0.2, 0.5, 440.0), (1.0, 0.5, 494.0), (1.8, 0.5, 523.0)],
        seconds=2.6,
        sample_rate=SAMPLE_RATE,
    )

    notes = melody.read(stem)

    assert len(notes) == 3
    assert [one.hz for one in notes] == pytest.approx([440.0, 494.0, 523.0], rel=0.01)


def test_a_note_starts_and_stops_where_it_was_written(tmp_path: Path) -> None:
    """Within 30ms — half the reader's window, which is what dating a frame at its centre costs."""
    stem = write_tones(
        tmp_path / "other.wav",
        notes=[(0.4, 0.8, 440.0)],
        seconds=1.6,
        sample_rate=SAMPLE_RATE,
    )

    (note,) = melody.read(stem)

    assert note.seconds == pytest.approx(0.4, abs=0.03)
    assert note.end == pytest.approx(1.2, abs=0.03)
    assert note.held == pytest.approx(0.8, abs=0.05)


def test_two_legato_notes_with_no_gap_are_two_notes(tmp_path: Path) -> None:
    """The pitch step is the only evidence there are two, and phrase length depends on it."""
    stem = write_tones(
        tmp_path / "other.wav",
        notes=[(0.2, 0.6, 440.0), (0.8, 0.6, 587.0)],
        seconds=1.8,
        sample_rate=SAMPLE_RATE,
    )

    notes = melody.read(stem)

    assert len(notes) == 2
    assert [one.hz for one in notes] == pytest.approx([440.0, 587.0], rel=0.01)


def test_one_note_held_across_a_vibrato_sized_wobble_stays_one_note(tmp_path: Path) -> None:
    """A tenor does not play in tune to the cent, and every wobble must not become a note."""
    stem = write_tones(
        tmp_path / "other.wav",
        notes=[(0.2, 0.4, 440.0), (0.6, 0.4, 447.0), (1.0, 0.4, 440.0)],
        seconds=1.8,
        sample_rate=SAMPLE_RATE,
    )

    assert len(melody.read(stem)) == 1


def test_a_stem_the_band_did_not_play_holds_no_notes(tmp_path: Path) -> None:
    stem = write_tones(tmp_path / "other.wav", notes=[], seconds=1.0, sample_rate=SAMPLE_RATE)

    assert melody.read(stem) == ()


def test_an_unpitched_event_still_reads_as_a_note_but_names_no_pitch(tmp_path: Path) -> None:
    """A cymbal wash in the residual has a start, an end and no fundamental — say all three."""
    stem = write_sections(
        tmp_path / "other.wav",
        sections=[("silence", 0.3), ("noise", 0.4), ("silence", 0.3)],
    )

    (note,) = melody.read(stem)

    assert note.hz == 0.0
    assert note.seconds == pytest.approx(0.3, abs=0.05)
    assert note.end == pytest.approx(0.7, abs=0.05)


def test_notes_come_back_earliest_first(tmp_path: Path) -> None:
    def out_of_order(path: Path) -> tuple[Note, ...]:
        return (
            Note(seconds=2.0, end=2.5, hz=440.0, strength=0.5),
            Note(seconds=0.5, end=1.0, hz=330.0, strength=0.5),
        )

    notes = melody.read(tmp_path / "unread.wav", out_of_order)

    assert [one.seconds for one in notes] == [0.5, 2.0]


def test_held_is_the_length_of_the_note() -> None:
    assert Note(seconds=1.0, end=2.5, hz=440.0, strength=0.5).held == 1.5


# --- the reading seam ---------------------------------------------------------------


def test_an_injected_reader_is_used_instead_of_the_default(tmp_path: Path) -> None:
    """ADR 0002: a better pitch tracker is an upgrade the rule layer never notices."""
    planted = (Note(seconds=0.0, end=1.0, hz=220.0, strength=0.9),)

    assert melody.read(tmp_path / "never-opened.wav", lambda path: planted) == planted


def test_a_reader_that_falls_over_is_an_analysis_failure_not_an_internal_error(
    tmp_path: Path,
) -> None:
    """Same rule as drum transcription: the agent can act on it, and it is not a server bug."""

    def broken(path: Path) -> tuple[Note, ...]:
        raise RuntimeError("no model")

    with pytest.raises(AnalysisFailedError) as raised:
        melody.read(tmp_path / "other.wav", broken)

    assert "no model" in raised.value.cause
