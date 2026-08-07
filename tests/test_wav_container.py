"""The WAV container itself: the headers the standard library will not open.

``wave`` reads RIFF but refuses every format tag except PCM, so a 32-bit float master — the
default output of a mastering chain, and one of the four concert masters in the #21 corpus —
came back as "unknown format: 3" and was reported as a damaged file (#110). The tests here
pin both halves of that: the float and extensible headers now decode, and a file that really
cannot be read says so without telling the caller to delete their own media.

The fixtures build these headers by hand because the standard library cannot write them
either; the PCM fixtures still go through ``wave``, which makes them an independent control —
where a test compares a float file against a PCM one, the PCM side was written by a decoder
this repo does not own.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from resolve_mcp.analysis import decode, energy, music, silence
from resolve_mcp.audio import riff, wav
from resolve_mcp.errors import AudioExtractionError

from .fakes import (
    write_extensible_pcm_wav,
    write_float_wav,
    write_tagged_wav,
    write_wav,
)

ADPCM_TAG = 0x0011


@pytest.mark.parametrize("bit_depth", [32, 64])
def test_decode_reads_ieee_float(tmp_path: Path, bit_depth: int) -> None:
    path = write_float_wav(tmp_path / f"float{bit_depth}.wav", seconds=0.2, bit_depth=bit_depth)

    audio = decode.read(path)

    assert audio.sample_rate == 48_000
    assert audio.channels == 2
    assert audio.frames == 9_600
    assert float(np.max(np.abs(audio.samples))) == pytest.approx(0.3, abs=0.01)


def test_decode_reads_an_extensible_header(tmp_path: Path) -> None:
    """Anything above two channels, and plenty of stereo besides, is tagged extensible."""
    path = write_extensible_pcm_wav(tmp_path / "extensible.wav", seconds=0.2)

    audio = decode.read(path)

    assert audio.sample_rate == 48_000
    assert audio.channels == 2
    assert float(np.max(np.abs(audio.samples))) == pytest.approx(0.3, abs=0.01)


def test_float_and_pcm_of_one_tone_decode_alike(tmp_path: Path) -> None:
    """The whole point of the fix: the same music measures the same whichever way it is stored.

    The PCM side is written by the standard library, so this compares the new reader against
    a decoder the repo does not own rather than against itself.
    """
    fixed = decode.read(write_wav(tmp_path / "fixed.wav", seconds=0.2, bit_depth=24))
    floating = decode.read(write_float_wav(tmp_path / "floating.wav", seconds=0.2))

    assert floating.samples.shape == fixed.samples.shape
    assert floating.sample_rate == fixed.sample_rate
    # 24-bit quantisation is the only difference left, and it lands well under a thousandth.
    assert float(np.max(np.abs(floating.mono() - fixed.mono()))) < 1e-3


def test_decode_keeps_peaks_above_full_scale(tmp_path: Path) -> None:
    """Float WAVs carry peaks over 1.0, and clamping them would under-report loudness.

    A fixed-point file cannot express this, so nothing before #110 had to decide; measuring
    a clipped copy of a master would report a peak the master does not have.
    """
    path = write_float_wav(tmp_path / "hot.wav", seconds=0.05, amplitude=1.4)

    audio = decode.read(path)

    assert float(np.max(np.abs(audio.samples))) == pytest.approx(1.4, abs=0.01)


def test_describe_reports_a_float_wav(tmp_path: Path) -> None:
    path = write_float_wav(tmp_path / "master.wav", seconds=0.5, sample_rate=44_100)

    described = wav.describe(path)

    # Exact, not approximate: #45 leaned on a float master and its transcode agreeing to the
    # sample (6130.888708 both ways), so a frame lost in the header read would matter.
    assert described["duration_seconds"] == 0.5
    assert decode.read(path).frames == 22_050
    assert described["sample_rate"] == 44_100
    assert described["channels"] == 2
    assert described["bit_depth"] == 32
    assert described["encoding"] == "float"


def test_describe_still_reports_pcm_as_pcm(tmp_path: Path) -> None:
    described = wav.describe(write_wav(tmp_path / "tone.wav", seconds=0.5, bit_depth=24))

    assert described["duration_seconds"] == 0.5
    assert described["bit_depth"] == 24
    assert described["encoding"] == "pcm"


def test_silence_is_measured_in_a_float_wav(tmp_path: Path) -> None:
    """The streaming reader reads float samples too, or it calls a loud file silent.

    Making ``describe`` accept a float file without teaching this path the same thing would
    turn a clean refusal into a wrong answer — the one outcome worse than the bug in #110.
    """
    path = write_float_wav(tmp_path / "gap.wav", seconds=2.0, silence=[(0.6, 1.4)])

    found = silence.measure(path, min_seconds=0.3)

    assert len(found) == 1
    assert found[0]["start"] == pytest.approx(0.6, abs=0.1)
    assert found[0]["end"] == pytest.approx(1.4, abs=0.1)


def test_a_float_wav_is_not_called_silent(tmp_path: Path) -> None:
    """Float bytes read as signed integers measure as noise or as nothing, never as a tone."""
    path = write_float_wav(tmp_path / "tone.wav", seconds=0.5)

    levels = silence.levels(path)

    assert levels
    assert max(levels) > -20.0


def test_an_unreadable_file_is_not_answered_by_deleting_the_callers_media(
    tmp_path: Path,
) -> None:
    """#110's second half: the ``fix`` line pointed at the wrong file and the wrong action.

    ``describe`` reads the caller's own source when it is passed through ``audio_at``, not
    only the server's cache, and these errors are read by agents that act on them.
    """
    path = tmp_path / "notes.txt"
    path.write_text("not audio", encoding="utf-8")

    with pytest.raises(AudioExtractionError) as raised:
        wav.describe(path)

    assert "delete" not in raised.value.fix.lower()
    assert "notes.txt" in raised.value.cause


@pytest.mark.parametrize("reader", [wav.describe, decode.read])
def test_a_compressed_wav_is_refused_by_its_name(
    tmp_path: Path, reader: Callable[[Path], object]
) -> None:
    """A tag neither reader can decode is a different fault from a damaged file, and says so."""
    path = write_tagged_wav(tmp_path / "adpcm.wav", tag=ADPCM_TAG)

    with pytest.raises(AudioExtractionError) as raised:
        reader(path)

    assert str(ADPCM_TAG) in raised.value.cause
    assert "delete" not in raised.value.fix.lower()


def test_rf64_is_named_rather_than_called_damaged(tmp_path: Path) -> None:
    """A concert master past 4 GB is RF64, which is a real format and not a broken RIFF."""
    path = write_tagged_wav(tmp_path / "long.wav", tag=1, riff_id=b"RF64")

    with pytest.raises(AudioExtractionError) as raised:
        wav.describe(path)

    assert "RF64" in raised.value.cause


def test_a_data_chunk_that_runs_past_the_file_is_read_to_its_end(tmp_path: Path) -> None:
    """A WAV piped from ffmpeg, or one still being written, declares a size it does not have.

    ``wave`` reports the declared frame count and then hands back short reads; trusting the
    header here would report a duration the file does not hold.
    """
    path = write_float_wav(tmp_path / "cut.wav", seconds=0.4)
    whole = path.read_bytes()
    path.write_bytes(whole[: len(whole) // 2])

    described = wav.describe(path)

    assert 0.0 < described["duration_seconds"] < 0.4
    decoded = decode.read(path).duration_seconds
    assert decoded == pytest.approx(described["duration_seconds"], abs=0.01)


def test_a_truncated_header_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "stub.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", 4) + b"WAVE")

    with pytest.raises(AudioExtractionError):
        wav.describe(path)


def test_the_reader_walks_past_chunks_it_does_not_know(tmp_path: Path) -> None:
    """The fixtures write a ``fact`` chunk between ``fmt `` and ``data``, as a real export does."""
    path = write_float_wav(tmp_path / "master.wav", seconds=0.1)

    assert b"fact" in path.read_bytes()
    with riff.opened(path) as handle:
        assert handle.format.frames == 4_800


def test_analyze_music_measures_a_float_master(tmp_path: Path) -> None:
    """#110's own headline, at the tool the ticket names rather than a layer below it.

    Beats are switched off because the detector is a model this tier does not install
    (ADR 0002); the energy half reads the samples, which is the half the bug was in.
    """
    path = write_float_wav(tmp_path / "master.wav", seconds=2.0, sample_rate=8_000)
    settings = {"beats": False, "energy": True, "window_seconds": 0.5, "hop_seconds": 0.25}

    output = music.analyze(path, settings, lambda fraction, step: None)

    assert output.result["audio"]["duration_seconds"] == 2.0
    assert output.result["audio"]["sample_rate"] == 8_000
    assert output.result["energy"]["count"] > 1


def test_loudness_survives_a_master_that_peaks_above_full_scale(tmp_path: Path) -> None:
    """The unclamped samples reach the loudness maths, which has to stay finite on them.

    Passing peaks through is only right if what reads them can take it: BS.1770 is a log of
    a mean square, so an above-full-scale file must measure *louder*, not overflow or clip.
    """
    quiet = decode.read(write_float_wav(tmp_path / "quiet.wav", seconds=0.5, amplitude=0.3))
    hot = decode.read(write_float_wav(tmp_path / "hot.wav", seconds=0.5, amplitude=1.4))

    measured = energy.integrated_lufs(hot)

    assert math.isfinite(measured)
    assert measured > energy.integrated_lufs(quiet)
