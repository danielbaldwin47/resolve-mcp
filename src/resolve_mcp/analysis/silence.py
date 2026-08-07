"""Where nobody is playing or speaking, read off the waveform.

Silence is derived from RMS rather than from the gaps between transcribed words, because
the two are different questions. Whisper leaves a gap wherever it heard no *words* — which
includes a held chord, a drum fill and a room full of applause. Breathing room is where the
signal itself drops, and only the samples know that.

Two implementation constraints shape this module:

* **A concert is two hours of 48 kHz stereo.** Reading every sample in Python would take
  longer than the transcription it accompanies, so each window is decimated to at most
  ``MAX_SAMPLES_PER_WINDOW`` evenly spaced samples. RMS over 64 samples of a 2400-sample
  window is far more precision than a -40 dB threshold needs.

* **No numpy, no audioop.** numpy is not a dependency of the server, and ``audioop`` — the
  obvious C-speed answer — is deprecated in 3.12 and removed in 3.13, so anything built on
  it is scheduled to break on the next interpreter bump.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable
from pathlib import Path

from ..audio import riff, wav
from ..errors import AudioExtractionError

DEFAULT_WINDOW_SECONDS = 0.05
DEFAULT_THRESHOLD_DB = -40.0
DEFAULT_MIN_SECONDS = 0.35

SILENT_FLOOR_DB = -120.0
MAX_SAMPLES_PER_WINDOW = 64
PLACES = 3


def measure(
    path: Path | str,
    threshold_db: float = DEFAULT_THRESHOLD_DB,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> list[dict[str, float]]:
    """The quiet stretches in a WAV, as ``{start, end, duration}`` in seconds."""
    reading = wav.describe(path)
    return spans(
        levels(path, window_seconds=window_seconds),
        window_seconds=window_seconds,
        threshold_db=threshold_db,
        min_seconds=min_seconds,
        limit=reading["duration_seconds"],
    )


def levels(path: Path | str, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> list[float]:
    """RMS per window in dBFS, oldest first — the curve everything else here reads."""
    target = Path(path)
    with wav.opened(target) as handle:
        found = handle.format
        _refuse_unsigned(target, found)
        per_window = max(1, round(window_seconds * found.sample_rate))
        measured: list[float] = []
        while True:
            raw = handle.read_frames(per_window)
            if not raw:
                return measured
            measured.append(_window_db(raw, found))
            if len(raw) < per_window * found.block_align:
                return measured


def spans(
    measured: list[float],
    window_seconds: float,
    threshold_db: float = DEFAULT_THRESHOLD_DB,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    limit: float | None = None,
) -> list[dict[str, float]]:
    """Runs of windows under the threshold, kept only where they run long enough.

    ``limit`` clamps a run that reaches the end of the file: the last window is a whole
    window wide even when the audio stopped part way through it, and a span reported past
    the end of its own audio is a span an agent will try to cut on.
    """
    found: list[dict[str, float]] = []
    start: int | None = None
    for index, level in enumerate([*measured, threshold_db]):
        if level < threshold_db:
            start = index if start is None else start
            continue
        if start is not None:
            found.append(_span(start, index, window_seconds, limit))
            start = None
    return [one for one in found if one["duration"] >= min_seconds]


def _span(
    first: int, past: int, window_seconds: float, limit: float | None
) -> dict[str, float]:
    start = first * window_seconds
    end = past * window_seconds
    if limit is not None:
        end = min(end, limit)
    return {
        "start": round(start, PLACES),
        "end": round(end, PLACES),
        "duration": round(end - start, PLACES),
    }


def _window_db(raw: bytes, found: riff.Format) -> float:
    """One window's RMS in dBFS, from a decimated read of its samples.

    The stride walks *interleaved* samples, so it has to be coprime with the channel count
    or it visits a fixed subset of the channels forever — a stride of 64 on a stereo file
    reads only the left, and one of 150 on a four-channel file reads only channels 0 and 2.
    Either would call a file silent because the channels it happened to skip are the ones
    carrying the band. Stepping up to the next coprime stride costs at most a few samples
    of the 64 and visits every channel in turn.
    """
    count = len(raw) // found.sample_width
    if count == 0:
        return SILENT_FLOOR_DB
    stride = max(1, count // MAX_SAMPLES_PER_WINDOW)
    while found.channels > 1 and math.gcd(stride, found.channels) != 1:
        stride += 1
    read_one = _sample_reader(found)
    total = 0.0
    taken = 0
    for index in range(0, count, stride):
        sample = read_one(raw, index * found.sample_width)
        total += sample * sample
        taken += 1
    rms = math.sqrt(total / taken)
    return SILENT_FLOOR_DB if rms <= 0.0 else max(SILENT_FLOOR_DB, 20.0 * math.log10(rms))


def _sample_reader(found: riff.Format) -> Callable[[bytes, int], float]:
    """One sample as a fraction of full scale, however the file happens to store them.

    A float WAV's bytes read as a signed integer are not a quiet version of the signal, they
    are a different signal — the exponent bits land in the high bytes, so a steady tone reads
    as noise and digital silence reads as full scale. Picking the reader once per window
    keeps that branch out of the decimation loop, which runs per sample over a whole concert.
    """
    if found.is_float:
        code = "<f" if found.sample_width == riff.FLOAT32_BYTES else "<d"
        return lambda raw, offset: float(struct.unpack_from(code, raw, offset)[0])
    width = found.sample_width
    full_scale = float(1 << (found.bit_depth - 1))
    return lambda raw, offset: (
        int.from_bytes(raw[offset : offset + width], "little", signed=True) / full_scale
    )


def _refuse_unsigned(target: Path, found: riff.Format) -> None:
    """8-bit WAV samples are unsigned, and reading them as signed inverts loud and quiet.

    Nothing this server acquires is 8-bit — both acquisition routes write 16, 24 or 32 —
    so this is a file someone else put in the cache, and guessing at it would report a loud
    tone as breathing room. Float depths are never 8-bit, so this only ever catches PCM.
    """
    if found.sample_width == 1:
        raise AudioExtractionError(
            cause=f"{target.name} is 8-bit audio, which this reader does not measure.",
            fix="Re-acquire the audio (the acquisition jobs write 24-bit WAV) and retry.",
            detail={"path": str(target), "bit_depth": found.bit_depth},
        )
