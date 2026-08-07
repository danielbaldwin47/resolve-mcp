"""Reading back what we just wrote — and what the director handed over.

Everything this server acquires is a WAV, so duration, sample rate and channel count come
off the header rather than out of ffprobe: one less external binary on the critical path,
and it works for the render-queue route on a machine with no ffmpeg at all. The header is
parsed by ``riff`` rather than by the standard library, which opens PCM only (#110).

The reading is left to the caller; what lives here is the one failure every reader of a WAV
has to report, phrased for a file this server did not necessarily write.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..errors import AudioExtractionError
from . import riff


@contextlib.contextmanager
def opened(path: Path | str) -> Iterator[riff.Reader]:
    """An open WAV, or the one failure every reader of one should report.

    The advice deliberately does not say to delete the file. This path reads two kinds of
    WAV — one the server cached and can make again, and one the caller passed in with
    ``audio_at``, which is their own master sitting on a media drive — and it cannot tell
    them apart from here. Telling an agent to delete the second is worse than saying nothing
    at all, so the fix names the check that is safe either way (#110).
    """
    target = Path(path)
    try:
        with riff.opened(target) as handle:
            yield handle
    except (OSError, riff.RiffError) as exc:
        raise AudioExtractionError(
            cause=f"{target.name} is not a readable WAV file ({exc}).",
            fix=(
                "Check the path points at a WAV this reader decodes — PCM or IEEE float. "
                "If the server acquired this file, run the acquisition again to rewrite it; "
                "if it is your own master, convert a copy to PCM WAV and analyse that."
            ),
            detail={"path": str(target)},
        ) from exc


def describe(path: Path | str) -> dict[str, Any]:
    """Duration, sample rate, channels, bit depth and encoding of a WAV on disk."""
    target = Path(path)
    with opened(target) as handle:
        found = handle.format
    return {
        "path": str(target),
        "duration_seconds": round(found.duration_seconds, 3) if found.sample_rate else None,
        "sample_rate": found.sample_rate,
        "channels": found.channels,
        "bit_depth": found.bit_depth,
        "encoding": found.encoding,
        "bytes": target.stat().st_size,
    }
