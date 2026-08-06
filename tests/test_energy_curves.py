"""The energy substrate: K-weighting, LUFS, RMS and onset density on fixture audio.

Model quality is not under test here (#22) — but the loudness maths is not a model, it is a
published standard, so it is pinned against the coefficients BS.1770-4 tabulates and against
properties that hold whatever the filter does: halving amplitude drops loudness by 6 LU, and
a K-weighted meter reads a high tone louder than a low one at equal RMS.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from resolve_mcp.analysis import decode, energy
from resolve_mcp.errors import AudioExtractionError

from .fakes import write_clicks, write_wav

# ITU-R BS.1770-4, Tables 1 and 2: the two stages at 48 kHz.
SHELF_B = (1.53512485958697, -2.69169618940638, 1.19839281085285)
SHELF_A = (1.0, -1.69065929318241, 0.73248077421585)
HIGHPASS_B = (1.0, -2.0, 1.0)
HIGHPASS_A = (1.0, -1.99004745483398, 0.99007225036621)


def test_k_weighting_matches_the_tabulated_coefficients_at_48k() -> None:
    sections = energy.k_weighting(48_000)

    shelf, highpass = sections
    assert shelf[:3] == pytest.approx(SHELF_B, abs=1e-4)
    assert shelf[3:] == pytest.approx(SHELF_A, abs=1e-4)
    assert highpass[:3] == pytest.approx(HIGHPASS_B, abs=1e-4)
    assert highpass[3:] == pytest.approx(HIGHPASS_A, abs=1e-4)


def test_k_weighting_is_derived_for_other_sample_rates() -> None:
    """A director's mix is not always 48k, and the filter has to move with the rate."""
    sections = energy.k_weighting(44_100)

    assert sections.shape == (2, 6)
    assert sections[0][3] == 1.0
    assert sections[0][:3] != pytest.approx(SHELF_B, abs=1e-4)


def test_loudness_of_a_reference_tone_lands_near_its_level(tmp_path: Path) -> None:
    """A 0.5-amplitude 440 Hz sine in two channels: -6.7 by hand, and K-weighting is flat there.

    Mean square per channel is 0.125, BS.1770 sums the two channels rather than averaging
    them, so -0.691 + 10*log10(0.25).
    """
    audio = decode.read(write_wav(tmp_path / "tone.wav", seconds=1.0, amplitude=0.5))

    assert energy.integrated_lufs(audio) == pytest.approx(-6.71, abs=0.5)


def test_halving_amplitude_drops_loudness_by_six(tmp_path: Path) -> None:
    loud = decode.read(write_wav(tmp_path / "loud.wav", seconds=1.0, amplitude=0.5))
    quiet = decode.read(write_wav(tmp_path / "quiet.wav", seconds=1.0, amplitude=0.25))

    dropped = energy.integrated_lufs(loud) - energy.integrated_lufs(quiet)

    assert dropped == pytest.approx(6.02, abs=0.2)


def test_the_meter_is_weighted_towards_the_top_end(tmp_path: Path) -> None:
    """K-weighting is the point of LUFS over RMS: equal energy, unequal perceived loudness."""
    low = decode.read(write_wav(tmp_path / "low.wav", seconds=1.0, frequency=100.0))
    high = decode.read(write_wav(tmp_path / "high.wav", seconds=1.0, frequency=6_000.0))

    assert energy.integrated_lufs(high) > energy.integrated_lufs(low) + 3.0


def test_silence_reads_as_the_floor_not_as_negative_infinity(tmp_path: Path) -> None:
    """JSON cannot hold -inf, and a curve with a hole in it is worse than a floored one."""
    audio = decode.read(write_wav(tmp_path / "silent.wav", seconds=1.0, amplitude=0.0))

    assert energy.integrated_lufs(audio) == energy.SILENCE_LUFS
    assert math.isfinite(energy.integrated_lufs(audio))


def test_the_curve_walks_the_file_in_hops(tmp_path: Path) -> None:
    audio = decode.read(write_wav(tmp_path / "tone.wav", seconds=2.0))

    points = energy.curve(audio, window_seconds=0.5, hop_seconds=0.25)

    assert [round(point.seconds, 3) for point in points[:4]] == [0.0, 0.25, 0.5, 0.75]
    assert points[-1].seconds <= audio.duration_seconds
    assert all(point.lufs > energy.SILENCE_LUFS for point in points)
    assert all(point.rms_dbfs < 0.0 for point in points)


def test_the_curve_follows_a_level_change(tmp_path: Path) -> None:
    """The whole point of a curve: where the tune gets loud, the numbers get bigger."""
    path = write_wav(tmp_path / "ramp.wav", seconds=2.0, amplitude=0.05)
    louder = write_wav(tmp_path / "louder.wav", seconds=2.0, amplitude=0.5)
    joined = decode.read(_concatenated(tmp_path / "joined.wav", path, louder))

    points = energy.curve(joined, window_seconds=0.5, hop_seconds=0.5)
    first, last = points[0], points[-1]

    assert last.lufs > first.lufs + 15.0
    assert last.rms_dbfs > first.rms_dbfs + 15.0


def test_onsets_land_on_the_clicks(tmp_path: Path) -> None:
    audio = decode.read(write_clicks(tmp_path / "clicks.wav", beats_per_minute=120.0, seconds=4.0))

    found = energy.onsets(audio)

    assert len(found) == pytest.approx(8, abs=2)
    assert min(abs(found - 1.0)) < 0.06


def test_a_steady_tone_has_almost_no_onsets(tmp_path: Path) -> None:
    audio = decode.read(write_wav(tmp_path / "tone.wav", seconds=2.0))

    assert len(energy.onsets(audio)) <= 1


def test_onset_density_counts_per_second_inside_the_window(tmp_path: Path) -> None:
    audio = decode.read(write_clicks(tmp_path / "clicks.wav", beats_per_minute=120.0, seconds=4.0))

    points = energy.curve(audio, window_seconds=2.0, hop_seconds=1.0)

    assert points[0].onsets_per_second == pytest.approx(2.0, abs=0.6)


def test_a_window_longer_than_the_file_still_yields_one_point(tmp_path: Path) -> None:
    audio = decode.read(write_wav(tmp_path / "short.wav", seconds=0.5))

    points = energy.curve(audio, window_seconds=3.0, hop_seconds=1.0)

    assert len(points) == 1
    assert points[0].seconds == 0.0


def test_decode_averages_channels_and_keeps_the_rate(tmp_path: Path) -> None:
    audio = decode.read(write_wav(tmp_path / "tone.wav", seconds=1.0, channels=2))

    assert audio.sample_rate == 48_000
    assert audio.channels == 2
    assert audio.frames == 48_000
    assert audio.duration_seconds == pytest.approx(1.0)
    assert audio.mono().shape == (48_000,)
    assert float(np.max(np.abs(audio.samples))) <= 1.0


@pytest.mark.parametrize("bit_depth", [16, 24, 32])
def test_decode_reads_every_depth_the_server_writes(tmp_path: Path, bit_depth: int) -> None:
    path = write_wav(tmp_path / f"tone{bit_depth}.wav", seconds=0.2, bit_depth=bit_depth)

    audio = decode.read(path)

    assert float(np.max(np.abs(audio.samples))) == pytest.approx(0.3, abs=0.01)


def test_decode_refuses_a_file_that_is_not_a_wav(tmp_path: Path) -> None:
    path = tmp_path / "notes.txt"
    path.write_text("not audio", encoding="utf-8")

    with pytest.raises(AudioExtractionError):
        decode.read(path)


def _concatenated(target: Path, first: Path, second: Path) -> Path:
    """Glue two WAVs together — a level change the curve has to follow."""
    import wave

    with wave.open(str(first), "rb") as head, wave.open(str(second), "rb") as tail:
        params = head.getparams()
        frames = head.readframes(head.getnframes()) + tail.readframes(tail.getnframes())
    with wave.open(str(target), "wb") as handle:
        handle.setparams(params)
        handle.writeframes(frames)
    return target
