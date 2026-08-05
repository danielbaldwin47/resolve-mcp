"""How loud, and how busy — the energy substrate every cutting decision reads.

Three numbers per window, because they answer three different questions:

* **LUFS** (ITU-R BS.1770-4) is perceived loudness — K-weighted, channel-summed, gated for
  the integrated figure. It is what "the band lifts here" means when a horn enters over a
  quiet vamp: RMS barely moves, LUFS does.
* **RMS in dBFS** is unweighted level, kept alongside because it is what a meter in Resolve
  shows and what a director means by "hot".
* **Onset density** is how many transients a second land in the window — a coarse activity
  measure, not a transcription. Drum-fill detection is its own model (#38); this is the
  cheap curve that says where the busy passages are.

The loudness maths is a published standard rather than a judgement call, so the filter is
derived from BS.1770's own filter parameters and checked against the coefficients the
standard tabulates for 48 kHz.
"""

from __future__ import annotations

import math
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import median_filter
from scipy.signal import sosfilt

from .decode import Audio

SILENCE_LUFS = -120.0
"""What silence reads as. JSON holds no ``-inf``, and a hole in a curve is worse than a floor."""

LOUDNESS_OFFSET = -0.691
ABSOLUTE_GATE = -70.0
RELATIVE_GATE = -10.0
BLOCK_SECONDS = 0.4
BLOCK_OVERLAP = 0.75

SHELF_GAIN_DB = 3.999843853973347
SHELF_Q = 0.7071752369554196
SHELF_HZ = 1681.974450955533
SHELF_BAND_EXPONENT = 0.499666774155
HIGHPASS_Q = 0.5003270373238773
HIGHPASS_HZ = 38.13547087602444

SURROUND_WEIGHT = 1.41
"""BS.1770 weights the surround channels up; front channels count once."""
FRONT_CHANNELS = 3

FLUX_WINDOW = 1024
FLUX_HOP = 512
FLUX_MEDIAN_SECONDS = 0.5
FLUX_EPSILON = 1e-6
MINIMUM_RISE = 0.2
"""How much of a spectrum has to be new before it counts as a transient rather than a wobble."""
MINIMUM_ONSET_GAP = 0.03


class EnergyPoint(NamedTuple):
    """One window of the curve. ``seconds`` is where the window *starts*."""

    seconds: float
    lufs: float
    rms_dbfs: float
    onsets_per_second: float


def k_weighting(sample_rate: int) -> NDArray[np.float64]:
    """The two BS.1770 stages as second-order sections, derived for this sample rate.

    The standard tabulates coefficients for 48 kHz only. A director's master mix is not
    always 48 kHz, so the filters are built from the parameters the standard's own design
    uses — which reproduces the tabulated numbers when the rate is 48 kHz.
    """
    return np.array(
        [
            _high_shelf(SHELF_HZ, SHELF_Q, SHELF_GAIN_DB, sample_rate),
            _high_pass(HIGHPASS_HZ, HIGHPASS_Q, sample_rate),
        ],
        dtype=np.float64,
    )


def _high_shelf(frequency: float, q: float, gain_db: float, sample_rate: int) -> list[float]:
    """The pre-filter, by the bilinear design the standard's own coefficients come from.

    Not the textbook RBJ shelf: that one normalises its numerator differently and lands a
    few tenths of a dB away from the tabulated numbers, which the coefficient test catches.
    """
    tangent = math.tan(math.pi * frequency / sample_rate)
    high = 10.0 ** (gain_db / 20.0)
    band = high**SHELF_BAND_EXPONENT
    scale = 1.0 + tangent / q + tangent * tangent
    return [
        (high + band * tangent / q + tangent * tangent) / scale,
        2.0 * (tangent * tangent - high) / scale,
        (high - band * tangent / q + tangent * tangent) / scale,
        1.0,
        2.0 * (tangent * tangent - 1.0) / scale,
        (1.0 - tangent / q + tangent * tangent) / scale,
    ]


def _high_pass(frequency: float, q: float, sample_rate: int) -> list[float]:
    """The RLB stage. Its numerator is [1, -2, 1] unnormalised, exactly as tabulated."""
    tangent = math.tan(math.pi * frequency / sample_rate)
    scale = 1.0 + tangent / q + tangent * tangent
    return [
        1.0,
        -2.0,
        1.0,
        1.0,
        2.0 * (tangent * tangent - 1.0) / scale,
        (1.0 - tangent / q + tangent * tangent) / scale,
    ]


def k_weight(samples: NDArray[np.floating], sample_rate: int) -> NDArray[np.float64]:
    """One channel, K-weighted, widened to float64 for the filter's sake."""
    return np.asarray(
        sosfilt(k_weighting(sample_rate), np.asarray(samples, dtype=np.float64)),
        dtype=np.float64,
    )


def loudness(mean_squares: NDArray[np.float64], channels: int) -> NDArray[np.float64]:
    """BS.1770's channel-weighted sum of mean squares, in LUFS, floored at silence."""
    weighted = (_weights(channels)[:, None] * mean_squares).sum(axis=0)
    with np.errstate(divide="ignore"):
        values = LOUDNESS_OFFSET + 10.0 * np.log10(weighted)
    return np.asarray(np.where(weighted > 0.0, values, SILENCE_LUFS), dtype=np.float64)


def _weights(channels: int) -> NDArray[np.float64]:
    weights = np.ones(channels, dtype=np.float64)
    if channels > FRONT_CHANNELS:
        weights[FRONT_CHANNELS:] = SURROUND_WEIGHT
    return weights


def integrated_lufs(audio: Audio) -> float:
    """The whole file as one number, gated the way the standard gates it.

    Two gates: anything below -70 LUFS is not programme, and once the loud material is
    known, anything more than 10 LU below *its* average is background rather than the thing
    being measured. Without them a concert's silences drag the figure down.
    """
    block = max(int(BLOCK_SECONDS * audio.sample_rate), 1)
    hop = max(int(block * (1.0 - BLOCK_OVERLAP)), 1)
    starts = _starts(audio.frames, block, hop, full_blocks_only=True)
    squares = _mean_squares(audio, block, starts)
    blocks = loudness(squares, audio.channels)

    above_absolute = blocks > ABSOLUTE_GATE
    if not above_absolute.any():
        return SILENCE_LUFS
    threshold = _gate_threshold(squares[:, above_absolute], audio.channels)
    kept = above_absolute & (blocks > threshold)
    if not kept.any():
        kept = above_absolute
    return float(loudness(squares[:, kept].mean(axis=1, keepdims=True), audio.channels)[0])


def _gate_threshold(squares: NDArray[np.float64], channels: int) -> float:
    average = loudness(squares.mean(axis=1, keepdims=True), channels)[0]
    return float(average + RELATIVE_GATE)


def curve(
    audio: Audio,
    window_seconds: float = 3.0,
    hop_seconds: float = 0.5,
) -> tuple[EnergyPoint, ...]:
    """Walk the file in windows, reporting loudness, level and activity for each one."""
    window = max(int(window_seconds * audio.sample_rate), 1)
    hop = max(int(hop_seconds * audio.sample_rate), 1)
    starts = _starts(audio.frames, window, hop, full_blocks_only=False)

    weighted = _mean_squares(audio, window, starts)
    plain = _mean_squares(audio, window, starts, weighted=False)
    lufs = loudness(weighted, audio.channels)
    rms = _dbfs(plain.mean(axis=0))
    density = onset_density(onsets(audio), starts / audio.sample_rate, window_seconds)

    return tuple(
        EnergyPoint(
            seconds=round(float(start) / audio.sample_rate, 3),
            lufs=round(float(one), 2),
            rms_dbfs=round(float(level), 2),
            onsets_per_second=round(float(rate), 3),
        )
        for start, one, level, rate in zip(starts, lufs, rms, density, strict=True)
    )


def _dbfs(mean_squares: NDArray[np.float64]) -> NDArray[np.float64]:
    with np.errstate(divide="ignore"):
        values = 10.0 * np.log10(mean_squares)
    return np.asarray(np.where(mean_squares > 0.0, values, SILENCE_LUFS), dtype=np.float64)


def _starts(frames: int, window: int, hop: int, full_blocks_only: bool) -> NDArray[np.int64]:
    """Window start frames. A file shorter than one window still gets one window."""
    last = frames - window if full_blocks_only else frames - 1
    if last < 0:
        return np.zeros(1, dtype=np.int64)
    return np.arange(0, last + 1, hop, dtype=np.int64)


def _mean_squares(
    audio: Audio,
    window: int,
    starts: NDArray[np.int64],
    weighted: bool = True,
) -> NDArray[np.float64]:
    """Mean square per channel per window, one channel in memory at a time.

    A running sum rather than a window-per-window loop: an hour of audio at a half-second
    hop is 7200 windows over 173 million frames, and the cumulative sum reads each frame
    once whatever the window length is.
    """
    ends = np.minimum(starts + window, audio.frames)
    lengths = np.maximum(ends - starts, 1)
    squares = np.empty((audio.channels, starts.size), dtype=np.float64)
    for channel in range(audio.channels):
        one = audio.samples[channel]
        # Both branches own their array: the square below is done in place.
        signal = (
            k_weight(one, audio.sample_rate)
            if weighted
            else np.array(one, dtype=np.float64, copy=True)
        )
        np.square(signal, out=signal)
        running = np.concatenate(([0.0], np.cumsum(signal)))
        squares[channel] = (running[ends] - running[starts]) / lengths
    return squares


def onsets(audio: Audio) -> NDArray[np.float64]:
    """Times, in seconds, where a transient starts — spectral flux with a moving threshold.

    Deliberately coarse. What it is for is "how busy is this passage", and a threshold that
    follows the local median keeps a quiet passage from reading as empty and a loud one from
    reading as one continuous onset.
    """
    flux, times = _spectral_flux(audio)
    if flux.size == 0:
        return np.zeros(0, dtype=np.float64)

    span = max(int(FLUX_MEDIAN_SECONDS * audio.sample_rate / FLUX_HOP), 3)
    local = np.asarray(median_filter(flux, size=span, mode="nearest"), dtype=np.float64) * 1.5
    threshold = np.maximum(local, MINIMUM_RISE)
    peaks = (flux > threshold) & (flux >= np.roll(flux, 1)) & (flux > np.roll(flux, -1))
    peaks[0] = peaks[-1] = False

    return _spaced(times[peaks], MINIMUM_ONSET_GAP)


def _spectral_flux(audio: Audio) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """How much of each short-time spectrum is new, as a fraction of the spectrum itself.

    The fraction is what makes a fixed threshold mean anything. A raw rise is measured in
    whatever units the mix happens to be in, so a rule about it either misses every onset in
    a quiet passage or fires on the leakage of a held note. Dividing by the magnitude of the
    frame the rise landed in gives a number between 0 and 1: a drum hit after near-silence
    is most of a new spectrum, a sustained tone is a fraction of a percent of one.
    """
    mono = audio.mono()
    if mono.size < FLUX_WINDOW * 2:
        return np.zeros(0, dtype=np.float64), np.zeros(0, dtype=np.float64)

    window = np.hanning(FLUX_WINDOW).astype(np.float32)
    count = 1 + (mono.size - FLUX_WINDOW) // FLUX_HOP
    frames = np.lib.stride_tricks.as_strided(
        mono,
        shape=(count, FLUX_WINDOW),
        strides=(mono.strides[0] * FLUX_HOP, mono.strides[0]),
    )

    rise = np.empty(count - 1, dtype=np.float64)
    totals = np.empty(count, dtype=np.float64)
    previous: NDArray[np.float64] | None = None
    for first in range(0, count, _FLUX_CHUNK):
        last = min(first + _FLUX_CHUNK, count)
        magnitudes = np.abs(np.fft.rfft(frames[first:last] * window, axis=1)).astype(np.float64)
        totals[first:last] = magnitudes.sum(axis=1)
        if previous is not None:
            rise[first - 1] = np.maximum(magnitudes[0] - previous, 0.0).sum()
        rising = np.maximum(np.diff(magnitudes, axis=0), 0.0).sum(axis=1)
        rise[first : first + rising.size] = rising
        previous = magnitudes[-1]

    floor = FLUX_EPSILON * float(totals.max()) + np.finfo(np.float64).tiny
    flux = rise / (totals[1:] + floor)
    times = (np.arange(flux.size, dtype=np.float64) + 1.0) * FLUX_HOP / audio.sample_rate
    return flux, times


_FLUX_CHUNK = 4096
"""Spectra per pass. A concert is 300k frames; all of them at once is gigabytes of complex."""


def _spaced(times: NDArray[np.float64], gap: float) -> NDArray[np.float64]:
    """Drop onsets that follow their predecessor too closely to be a separate hit."""
    kept: list[float] = []
    for one in times:
        if not kept or one - kept[-1] >= gap:
            kept.append(float(one))
    return np.array(kept, dtype=np.float64)


def onset_density(
    times: NDArray[np.float64],
    starts: NDArray[np.float64],
    window_seconds: float,
) -> NDArray[np.float64]:
    """Onsets per second inside each window."""
    if window_seconds <= 0:
        return np.zeros(starts.size, dtype=np.float64)
    opened = np.searchsorted(times, starts, side="left")
    closed = np.searchsorted(times, starts + window_seconds, side="left")
    return np.asarray((closed - opened) / window_seconds, dtype=np.float64)
