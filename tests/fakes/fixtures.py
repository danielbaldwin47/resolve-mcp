"""Real media files the worker tier reads back, and the ffmpeg runners routes shell out to."""

from __future__ import annotations

import contextlib
import math
import random
import wave
from collections.abc import Sequence
from pathlib import Path

from resolve_mcp.ffmpeg import Completed, Runner


def write_wav(
    path: Path,
    seconds: float = 2.0,
    sample_rate: int = 48_000,
    bit_depth: int = 24,
    channels: int = 2,
    frequency: float = 440.0,
    amplitude: float = 0.3,
    silence: Sequence[tuple[float, float]] = (),
) -> Path:
    """A real WAV of a sine tone — the fixture audio the worker tier is tested on.

    Real audio rather than a stub file, because the workers read the header back: a fake
    that wrote ``b"RIFF"`` would pass a duration assertion that means nothing. ``amplitude``
    is a fraction of full scale, which is what the loudness tests vary.

    ``silence`` zeroes the given ``(start, end)`` second-ranges, which is what makes this
    fixture usable by the analysis tier: a tone that never stops has no breathing room to
    find, so a silence detector run over it can only ever be asserted to find nothing.
    """
    peak = 2 ** (bit_depth - 1) * amplitude
    quiet = [(start * sample_rate, end * sample_rate) for start, end in silence]
    samples = [
        0
        if any(start <= index < end for start, end in quiet)
        else int(peak * math.sin(2 * math.pi * frequency * index / sample_rate))
        for index in range(int(seconds * sample_rate))
    ]
    return _write_samples(path, samples, sample_rate, bit_depth, channels)


def write_clicks(
    path: Path,
    beats_per_minute: float = 120.0,
    seconds: float = 4.0,
    sample_rate: int = 48_000,
    bit_depth: int = 24,
    channels: int = 2,
    decay_seconds: float = 0.03,
    amplitude: float = 0.5,
) -> Path:
    """A click track — the fixture the onset side is tested on.

    A steady tone has no transients at all, so it cannot show that onset detection finds
    anything; a decaying click on every beat can, and its beat times are known.
    """
    peak = 2 ** (bit_depth - 1) * amplitude
    step = 60.0 / beats_per_minute
    total = int(seconds * sample_rate)
    decay = max(int(decay_seconds * sample_rate), 1)
    samples = [0] * total
    for beat in range(int(seconds / step)):
        start = int(beat * step * sample_rate)
        for offset in range(min(decay, total - start)):
            envelope = 1.0 - offset / decay
            wobble = math.sin(2 * math.pi * 2_000.0 * offset / sample_rate)
            samples[start + offset] = int(peak * envelope * envelope * wobble)
    return _write_samples(path, samples, sample_rate, bit_depth, channels)


def write_hits(
    path: Path,
    times: Sequence[float],
    seconds: float = 2.0,
    sample_rate: int = 48_000,
    bit_depth: int = 24,
    channels: int = 2,
    decay_seconds: float = 0.03,
    amplitude: float = 0.5,
    frequency: float = 2_000.0,
) -> Path:
    """A drum stem: decaying hits at exactly the times asked for, silence in between.

    ``write_clicks`` puts a click on every beat, which is what a beat fixture wants and the
    opposite of what a stem is — a kick stem is mostly nothing, and where its hits fall is
    the thing under test. Passing no times writes the silence a stem the band did not play
    would hold.
    """
    peak = 2 ** (bit_depth - 1) * amplitude
    total = int(seconds * sample_rate)
    decay = max(int(decay_seconds * sample_rate), 1)
    samples = [0] * total
    for when in times:
        start = int(when * sample_rate)
        for offset in range(min(decay, max(total - start, 0))):
            envelope = 1.0 - offset / decay
            wobble = math.sin(2 * math.pi * frequency * offset / sample_rate)
            samples[start + offset] = int(peak * envelope * envelope * wobble)
    return _write_samples(path, samples, sample_rate, bit_depth, channels)


def write_sections(
    path: Path,
    sections: Sequence[tuple[str, float]],
    sample_rate: int = 8_000,
    bit_depth: int = 24,
    channels: int = 2,
    frequency: float = 440.0,
    amplitude: float = 0.3,
    seed: int = 7,
) -> Path:
    """A concert-shaped WAV: ``("tone" | "noise" | "silence", seconds)`` laid end to end.

    The applause half of structure analysis reads a tagger, not the waveform, so what this
    fixture has to be right about is its *shape* — where the music stops, how long the room
    goes on for, and how long the whole file is, since the last tune ends where the file
    does. The noise is seeded so a boundary assertion means the same thing on every run.
    """
    noise = random.Random(seed)
    peak = 2 ** (bit_depth - 1) * amplitude
    samples: list[int] = []
    for kind, seconds in sections:
        for index in range(int(seconds * sample_rate)):
            if kind == "silence":
                samples.append(0)
            elif kind == "noise":
                samples.append(int(peak * noise.uniform(-1.0, 1.0)))
            else:
                samples.append(int(peak * math.sin(2 * math.pi * frequency * index / sample_rate)))
    return _write_samples(path, samples, sample_rate, bit_depth, channels)


def _write_samples(
    path: Path,
    samples: list[int],
    sample_rate: int,
    bit_depth: int,
    channels: int,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = bit_depth // 8
    frames = bytearray()
    for sample in samples:
        frames.extend(sample.to_bytes(width, "little", signed=True) * channels)
    with contextlib.closing(wave.open(str(path), "wb")) as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
    return path


def write_jpeg(path: Path, width: int = 1568, height: int = 882) -> Path:
    """A JPEG header carrying real dimensions, standing in for a frame ffmpeg wrote.

    The grab route reads the width and height back off the file rather than repeating what
    it asked for, so the fixture has to carry a truthful SOF0 segment. It carries nothing
    else: no scan data, because no test decodes a pixel, and a file full of them would only
    make the fixture slower to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    jfif = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    components = b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"  # three components, 8-bit, one table
    frame = (
        b"\xff\xc0"
        + (8 + len(components)).to_bytes(2, "big")
        + b"\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x03"
        + components
    )
    path.write_bytes(
        b"\xff\xd8" + b"\xff\xe0" + (2 + len(jfif)).to_bytes(2, "big") + jfif + frame + b"\xff\xd9"
    )
    return path


def ffmpeg_absent(argv: Sequence[str]) -> Completed:
    """A machine with no ffmpeg on it: the runner raises what ``subprocess`` would raise."""
    raise FileNotFoundError(argv[0])


def ffmpeg_refusing(stderr: str) -> Runner:
    """An ffmpeg that ran and would not have the file, complaining the way it does."""

    def runner(argv: Sequence[str]) -> Completed:
        return Completed(1, stderr)

    return runner
