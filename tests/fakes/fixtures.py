"""Real media files the worker tier reads back, and the ffmpeg runners routes shell out to."""

from __future__ import annotations

import contextlib
import math
import random
import struct
import wave
from collections.abc import Sequence
from pathlib import Path

from resolve_mcp.ffmpeg import Completed, Runner

# The format tags a WAV header can carry, spelled out again rather than imported from
# resolve_mcp.audio.riff: a fixture that took its constants from the parser under test would
# agree with that parser about a wrong number, and prove nothing.
PCM_TAG = 0x0001
FLOAT_TAG = 0x0003
EXTENSIBLE_TAG = 0xFFFE
FLOAT32_BITS = 32

# The tail of KSDATAFORMAT_SUBTYPE_*, the GUID an extensible header carries in place of a tag.
SUBFORMAT_TAIL = b"\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"


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
    accents: Sequence[float] | None = None,
) -> Path:
    """A drum stem: decaying hits at exactly the times asked for, silence in between.

    ``write_clicks`` puts a click on every beat, which is what a beat fixture wants and the
    opposite of what a stem is — a kick stem is mostly nothing, and where its hits fall is
    the thing under test. Passing no times writes the silence a stem the band did not play
    would hold.

    ``accents`` scales each hit in turn — the fixture a bar map needs (#180), where *which*
    hits are loud is the whole reading and hits of one size would say nothing.
    """
    peak = 2 ** (bit_depth - 1) * amplitude
    total = int(seconds * sample_rate)
    decay = max(int(decay_seconds * sample_rate), 1)
    scales = tuple(accents) if accents is not None else tuple(1.0 for _ in times)
    samples = [0] * total
    for when, scale in zip(times, scales, strict=True):
        start = int(when * sample_rate)
        for offset in range(min(decay, max(total - start, 0))):
            envelope = 1.0 - offset / decay
            wobble = math.sin(2 * math.pi * frequency * offset / sample_rate)
            samples[start + offset] = int(peak * scale * envelope * envelope * wobble)
    return _write_samples(path, samples, sample_rate, bit_depth, channels)


def write_tones(
    path: Path,
    notes: Sequence[tuple[float, float, float]],
    seconds: float | None = None,
    sample_rate: int = 48_000,
    bit_depth: int = 24,
    channels: int = 2,
    amplitude: float = 0.5,
    attack_seconds: float = 0.01,
    release_seconds: float = 0.02,
) -> Path:
    """A melodic stem: ``(start, duration, hz)`` notes laid down, silence between them.

    ``write_hits`` is the percussion stem — transients with no pitch to read — and the phrase
    side needs its opposite: notes that hold a pitch for a knowable length and then stop, so
    "how long was that note" and "how far did the line jump" are facts about the fixture
    rather than about the detector that reads it.

    The attack and release live *inside* the note's own duration, so a note really has
    stopped by ``start + duration`` and a planted gap is the length it says it is.
    """
    peak = 2 ** (bit_depth - 1) * amplitude
    ends = [start + held for start, held, _ in notes]
    total = int((seconds if seconds is not None else max(ends, default=0.0) + 0.5) * sample_rate)
    attack = max(int(attack_seconds * sample_rate), 1)
    release = max(int(release_seconds * sample_rate), 1)
    samples = [0] * total
    for start, held, hertz in notes:
        first = max(int(start * sample_rate), 0)
        length = min(int(held * sample_rate), max(total - first, 0))
        for offset in range(length):
            envelope = min(offset / attack, (length - offset) / release, 1.0)
            wobble = math.sin(2 * math.pi * hertz * offset / sample_rate)
            samples[first + offset] = int(peak * envelope * wobble)
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


def write_float_wav(
    path: Path,
    seconds: float = 2.0,
    sample_rate: int = 48_000,
    bit_depth: int = 32,
    channels: int = 2,
    frequency: float = 440.0,
    amplitude: float = 0.3,
    silence: Sequence[tuple[float, float]] = (),
    extensible: bool = False,
) -> Path:
    """``write_wav``'s tone, written as IEEE float — what a mastering chain hands you.

    The standard library cannot write one of these any more than it can read one, so the
    header is built by hand. ``amplitude`` is in full-scale units and is *not* clamped:
    passing more than 1.0 writes the above-full-scale peaks a float master is allowed to
    carry, which is the thing a fixed-point fixture cannot express.
    """
    quiet = [(start * sample_rate, end * sample_rate) for start, end in silence]
    samples = [
        0.0
        if any(start <= index < end for start, end in quiet)
        else amplitude * math.sin(2 * math.pi * frequency * index / sample_rate)
        for index in range(int(seconds * sample_rate))
    ]
    frames = _interleaved_floats(samples, bit_depth, channels)
    return _write_riff(path, frames, sample_rate, bit_depth, channels, FLOAT_TAG, extensible)


def write_extensible_pcm_wav(
    path: Path,
    seconds: float = 2.0,
    sample_rate: int = 48_000,
    bit_depth: int = 24,
    channels: int = 2,
    frequency: float = 440.0,
    amplitude: float = 0.3,
) -> Path:
    """PCM samples behind an extensible header — ordinary audio the standard library refuses.

    Anything with more than two channels, and plenty of two-channel exports besides, comes
    out of a professional tool tagged ``WAVE_FORMAT_EXTENSIBLE`` with PCM named in a GUID.
    The samples are the same ones ``write_wav`` writes; only the header differs.
    """
    peak = 2 ** (bit_depth - 1) * amplitude
    samples = [
        int(peak * math.sin(2 * math.pi * frequency * index / sample_rate))
        for index in range(int(seconds * sample_rate))
    ]
    frames = _interleaved_ints(samples, bit_depth, channels)
    return _write_riff(path, frames, sample_rate, bit_depth, channels, PCM_TAG, extensible=True)


def write_tagged_wav(
    path: Path,
    tag: int,
    bit_depth: int = 16,
    sample_rate: int = 48_000,
    channels: int = 2,
    frames: bytes = b"\x00" * 64,
    riff_id: bytes = b"RIFF",
) -> Path:
    """A structurally sound WAV carrying whatever format tag is asked for.

    For the refusals: a compressed tag the readers cannot decode, and an ``RF64`` file,
    both of which have to be told apart from a damaged file rather than lumped in with one.
    """
    return _write_riff(
        path, frames, sample_rate, bit_depth, channels, tag, extensible=False, riff_id=riff_id
    )


def _interleaved_ints(samples: list[int], bit_depth: int, channels: int) -> bytes:
    """One mono signal laid out as interleaved fixed-point frames: every channel the same."""
    width = bit_depth // 8
    frames = bytearray()
    for sample in samples:
        frames.extend(sample.to_bytes(width, "little", signed=True) * channels)
    return bytes(frames)


def _interleaved_floats(samples: list[float], bit_depth: int, channels: int) -> bytes:
    """The same, in single or double precision — the layouts ``wave`` will not write."""
    code = "<f" if bit_depth == FLOAT32_BITS else "<d"
    frames = bytearray()
    for sample in samples:
        frames.extend(struct.pack(code, sample) * channels)
    return bytes(frames)


def _write_riff(
    path: Path,
    frames: bytes,
    sample_rate: int,
    bit_depth: int,
    channels: int,
    tag: int,
    extensible: bool,
    riff_id: bytes = b"RIFF",
) -> Path:
    """A WAV assembled chunk by chunk, including the ``fact`` chunk a non-PCM file carries.

    ``fact`` is here because it sits *between* ``fmt `` and ``data`` in a real float export:
    a reader that assumed the samples follow the header immediately would pass every test
    written without it and fail on the first file off a mastering desk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    block_align = bit_depth // 8 * channels
    header = struct.pack(
        "<HHIIHH",
        EXTENSIBLE_TAG if extensible else tag,
        channels,
        sample_rate,
        sample_rate * block_align,
        block_align,
        bit_depth,
    )
    if extensible:
        header += struct.pack("<HHI", 22, bit_depth, 0) + struct.pack("<I", tag) + SUBFORMAT_TAIL
    body = (
        _chunk(b"fmt ", header)
        + _chunk(b"fact", struct.pack("<I", len(frames) // max(block_align, 1)))
        + _chunk(b"data", frames)
    )
    path.write_bytes(riff_id + struct.pack("<I", 4 + len(body)) + b"WAVE" + body)
    return path


def _chunk(name: bytes, body: bytes) -> bytes:
    """One RIFF chunk, padded to an even length the way the container requires."""
    return name + struct.pack("<I", len(body)) + body + (b"\x00" if len(body) % 2 else b"")


def _write_samples(
    path: Path,
    samples: list[int],
    sample_rate: int,
    bit_depth: int,
    channels: int,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(wave.open(str(path), "wb")) as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(bit_depth // 8)
        handle.setframerate(sample_rate)
        handle.writeframes(_interleaved_ints(samples, bit_depth, channels))
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


GRAY_LIGHT = 180
GRAY_DARK = 100
"""The two values the fixture stage alternates between: bright enough that neither is 'dark'
against the frame's own median, and alternating per pixel so the background is full of
texture. A near-field blocker has to be distinguishable from a busy dark stage, not just
from a blank one, so the fixture background is busy and dark-ish on purpose."""

BLOCKER_VALUE = 8
"""A silhouette: near black and, being one value, without a scrap of texture."""


def gray_frame(
    fraction: float = 0.0,
    anchor: str = "bottom",
    value: int = BLOCKER_VALUE,
    width: int = 128,
    height: int = 72,
) -> bytes:
    """One raw 8-bit grey frame: a textured stage with ``fraction`` of it behind a flat blob.

    ``anchor`` is where the blob sits — ``bottom`` and ``side`` are what a body in the near
    field looks like, ``top`` is the club's ceiling and ``float`` is something in the picture
    rather than in front of it. The last two exist so the tests can prove the measurement
    tells them apart.

    The frame is composed here from plain bytes rather than by the module under test: a
    fixture that borrowed the scorer's own idea of a blocker could only ever agree with it.
    """
    pixels = bytearray(
        GRAY_LIGHT if (x + y) % 2 else GRAY_DARK for y in range(height) for x in range(width)
    )
    if fraction > 0:
        side = math.sqrt(fraction)
        box_width = max(1, round(width * side))
        box_height = max(1, round(height * side))
        left = (width - box_width) // 2
        top = {
            "bottom": height - box_height,
            "top": 0,
            "side": height - box_height,
            "float": (height - box_height) // 2,
        }[anchor]
        if anchor == "side":
            left = 0
        elif anchor == "float":
            left = (width - box_width) // 2
        for y in range(top, top + box_height):
            for x in range(left, left + box_width):
                pixels[y * width + x] = value
    return bytes(pixels)


def ffmpeg_sampling(calls: list[Sequence[str]], frames: Sequence[bytes]) -> Runner:
    """An ffmpeg that writes the raw grey a sampling command asked for, and records the call.

    The raw file is the seam the occlusion worker reads: substituting the subprocess means
    the scoring, the spans and the cache are all testable on a machine with no decoder.
    """

    def runner(argv: Sequence[str]) -> Completed:
        calls.append(list(argv))
        target = Path(argv[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"".join(frames))
        return Completed(0, "")

    return runner


def ffmpeg_writing_nothing(calls: list[Sequence[str]]) -> Runner:
    """An ffmpeg that exits zero and writes no frames — what a seek past the end does."""

    def runner(argv: Sequence[str]) -> Completed:
        calls.append(list(argv))
        return Completed(0, "")

    return runner


def ffmpeg_absent(argv: Sequence[str]) -> Completed:
    """A machine with no ffmpeg on it: the runner raises what ``subprocess`` would raise."""
    raise FileNotFoundError(argv[0])


def ffmpeg_refusing(stderr: str) -> Runner:
    """An ffmpeg that ran and would not have the file, complaining the way it does."""

    def runner(argv: Sequence[str]) -> Completed:
        return Completed(1, stderr)

    return runner
