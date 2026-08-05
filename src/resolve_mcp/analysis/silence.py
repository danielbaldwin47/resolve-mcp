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

import contextlib
import math
import wave
from pathlib import Path

from ..errors import AudioExtractionError

DEFAULT_WINDOW_SECONDS = 0.05
DEFAULT_THRESHOLD_DB = -40.0
DEFAULT_MIN_SECONDS = 0.35

SILENT_FLOOR_DB = -120.0
MAX_SAMPLES_PER_WINDOW = 64
BITS_PER_BYTE = 8
PLACES = 3


def silence(
    path: Path | str,
    threshold_db: float = DEFAULT_THRESHOLD_DB,
    min_seconds: float = DEFAULT_MIN_SECONDS,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> list[dict[str, float]]:
    """The quiet stretches in a WAV, as ``{start, end, duration}`` in seconds."""
    measured = levels(path, window_seconds=window_seconds)
    return spans(
        measured,
        window_seconds=window_seconds,
        threshold_db=threshold_db,
        min_seconds=min_seconds,
        limit=duration(path),
    )


def levels(path: Path | str, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> list[float]:
    """RMS per window in dBFS, oldest first — the curve everything else here reads."""
    target = Path(path)
    try:
        with contextlib.closing(wave.open(str(target), "rb")) as handle:
            width = handle.getsampwidth()
            rate = handle.getframerate()
            channels = handle.getnchannels()
            _refuse_unsigned(target, width)
            per_window = max(1, round(window_seconds * rate))
            measured: list[float] = []
            while True:
                raw = handle.readframes(per_window)
                if not raw:
                    return measured
                measured.append(_window_db(raw, width))
                if len(raw) < per_window * width * channels:
                    return measured
    except (OSError, wave.Error) as exc:
        raise AudioExtractionError(
            cause=f"{target.name} is not a readable WAV file ({exc}).",
            fix="Delete it from the cache directory and run the job again.",
            detail={"path": str(target)},
        ) from exc


def duration(path: Path | str) -> float | None:
    """Seconds of audio, or ``None`` when the header does not say."""
    with contextlib.closing(wave.open(str(Path(path)), "rb")) as handle:
        rate = handle.getframerate()
        return handle.getnframes() / rate if rate else None


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


def _window_db(raw: bytes, width: int) -> float:
    """One window's RMS in dBFS, from a decimated read of its samples."""
    count = len(raw) // width
    if count == 0:
        return SILENT_FLOOR_DB
    stride = max(1, count // MAX_SAMPLES_PER_WINDOW)
    total = 0.0
    taken = 0
    for index in range(0, count, stride):
        offset = index * width
        sample = int.from_bytes(raw[offset : offset + width], "little", signed=True)
        total += float(sample) * float(sample)
        taken += 1
    full_scale = float(1 << (width * BITS_PER_BYTE - 1))
    rms = math.sqrt(total / taken) / full_scale
    return SILENT_FLOOR_DB if rms <= 0.0 else max(SILENT_FLOOR_DB, 20.0 * math.log10(rms))


def _refuse_unsigned(target: Path, width: int) -> None:
    """8-bit WAV samples are unsigned, and reading them as signed inverts loud and quiet.

    Nothing this server acquires is 8-bit — both acquisition routes write 16, 24 or 32 —
    so this is a file someone else put in the cache, and guessing at it would report a loud
    tone as breathing room.
    """
    if width == 1:
        raise AudioExtractionError(
            cause=f"{target.name} is 8-bit audio, which this reader does not measure.",
            fix="Re-acquire the audio (the acquisition jobs write 24-bit WAV) and retry.",
            detail={"path": str(target), "bit_depth": width * BITS_PER_BYTE},
        )
