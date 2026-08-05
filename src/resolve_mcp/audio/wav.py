"""Reading back what we just wrote.

Everything this server acquires is a WAV, and the standard library reads WAV headers — so
duration, sample rate and channel count come from ``wave`` rather than from shelling out to
ffprobe. One less external binary on the critical path, and it works for the render-queue
route on a machine with no ffmpeg at all.
"""

from __future__ import annotations

import contextlib
import wave
from pathlib import Path
from typing import Any

from ..errors import AudioExtractionError

BITS_PER_BYTE = 8


def describe(path: Path | str) -> dict[str, Any]:
    """Duration, sample rate, channels and bit depth of a WAV on disk."""
    target = Path(path)
    try:
        with contextlib.closing(wave.open(str(target), "rb")) as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
            channels = handle.getnchannels()
            width = handle.getsampwidth()
    except (OSError, wave.Error) as exc:
        raise AudioExtractionError(
            cause=f"{target.name} is not a readable WAV file ({exc}).",
            fix="Delete it from the cache directory and run the job again.",
            detail={"path": str(target)},
        ) from exc
    return {
        "path": str(target),
        "duration_seconds": round(frames / rate, 3) if rate else None,
        "sample_rate": rate,
        "channels": channels,
        "bit_depth": width * BITS_PER_BYTE,
        "bytes": target.stat().st_size,
    }
