"""WAV on disk to samples in memory.

Everything analysed here is a WAV this server acquired or the director handed over, so the
container is parsed in-repo by ``audio.riff`` and numpy does the arithmetic — no soundfile,
no librosa, no third decoder to disagree with the two the repo already depends on. ``riff``
rather than the standard library's ``wave`` because ``wave`` opens PCM and nothing else, and
a 32-bit float master is what a mastering chain hands you (#110).

Samples are ``float32``, shaped ``(channels, frames)``. float32 rather than float64 because
an hour of 48k stereo is 173 million frames: 1.4 GB of the former is already a lot to hold,
and 2.8 GB of the latter is a lot to hold twice. The loudness filter widens one channel at a
time to float64 where the arithmetic wants the headroom.

Fixed-point sources land in [-1, 1] because that is the whole range they have. Float sources
are passed through unscaled and may exceed it: a float master is allowed peaks above full
scale, and clamping them here would report a true peak, and a loudness, that the master does
not have.
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray

from ..audio import riff
from ..errors import AudioExtractionError

SUPPORTED_WIDTHS = (2, 3, 4)


class Audio(NamedTuple):
    """Decoded samples and the rate they were sampled at."""

    samples: NDArray[np.float32]
    sample_rate: int

    @property
    def channels(self) -> int:
        return int(self.samples.shape[0])

    @property
    def frames(self) -> int:
        return int(self.samples.shape[1])

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0

    def mono(self) -> NDArray[np.float32]:
        """The channel average — what onset detection listens to.

        Loudness does not use this: BS.1770 weights and sums *per channel*, and averaging
        first would let two channels in antiphase read as silence.
        """
        return np.asarray(self.samples.mean(axis=0), dtype=np.float32)


def read(path: Path | str) -> Audio:
    """Decode a WAV, or say which file could not be decoded and why."""
    target = Path(path)
    try:
        found, raw = riff.read(target)
    except (OSError, riff.RiffError) as exc:
        raise AudioExtractionError(
            cause=f"{target.name} is not a readable WAV file ({exc}).",
            fix=(
                "Analysis reads PCM and IEEE float WAV. Acquire the audio with "
                "acquire_timeline_audio, or convert a copy of the master mix to PCM WAV "
                "and analyse that."
            ),
            detail={"path": str(target)},
        ) from exc

    if not found.is_float and found.sample_width not in SUPPORTED_WIDTHS:
        raise AudioExtractionError(
            cause=f"{target.name} is {found.bit_depth}-bit PCM, which is not supported.",
            fix="Convert it to 16-, 24- or 32-bit PCM WAV and analyse that.",
            detail={"path": str(target), "bit_depth": found.bit_depth},
        )

    samples = _samples(raw, found.sample_width, found.is_float)
    usable = samples.size - samples.size % found.channels
    reshaped = samples[:usable].reshape(-1, found.channels).T
    return Audio(samples=reshaped.copy(), sample_rate=found.sample_rate)


def _samples(raw: bytes, width: int, is_float: bool) -> NDArray[np.float32]:
    """Interleaved sample bytes to interleaved floats.

    Float sources are already in full-scale units, so they are only narrowed to float32 —
    dividing by anything would be scaling a signal that is already scaled.
    """
    if is_float:
        precision = np.dtype("<f4") if width == riff.FLOAT32_BYTES else np.dtype("<f8")
        return np.asarray(np.frombuffer(raw, dtype=precision), dtype=np.float32)
    if width == 3:
        ints = _from_24_bit(raw)
    else:
        signed = np.dtype("<i2") if width == 2 else np.dtype("<i4")
        ints = np.frombuffer(raw, dtype=signed).astype(np.int32)
    full_scale = float(2 ** (width * riff.BITS_PER_BYTE - 1))
    return np.asarray(ints.astype(np.float32) / full_scale, dtype=np.float32)


def _from_24_bit(raw: bytes) -> NDArray[np.int32]:
    """24-bit has no numpy dtype: rebuild each sample from its three little-endian bytes."""
    usable = len(raw) - len(raw) % 3
    triples = np.frombuffer(raw[:usable], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
    high = np.where(triples[:, 2] >= 128, triples[:, 2] - 256, triples[:, 2])
    return np.asarray(triples[:, 0] | (triples[:, 1] << 8) | (high << 16), dtype=np.int32)
