"""The WAV container, read here because the standard library only opens half of them.

``wave`` parses RIFF perfectly well and then refuses any format tag that is not PCM, so a
32-bit float master — what a mastering chain hands you by default — raises "unknown format:
3" and reaches the caller looking like a damaged file (#110). The two tags it leaves out are
the two that turn up most: ``WAVE_FORMAT_IEEE_FLOAT``, and ``WAVE_FORMAT_EXTENSIBLE``, which
carries its real tag in a GUID and is what a professional tool writes for anything above two
channels.

This module reads the container for *every* WAV the server touches rather than falling back
to it for the ones ``wave`` rejects. One reader, because two readers on one format is two
sets of edge cases that will eventually disagree about the same file — and the disagreement
would show up as a duration that changes depending on which analysis job asked.

It stays standard-library-only, for the reason ``decode`` gives: soundfile and librosa each
bring a second decoder along, and neither is installed on the machine that runs the server.

Nothing here raises a server error. A parse failure is a ``RiffError``, and the caller — the
one that knows whether this file came out of the cache or off the director's drive — turns
it into an ``AudioExtractionError`` with advice that fits where the file came from.
"""

from __future__ import annotations

import contextlib
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, NamedTuple

PCM = 0x0001
IEEE_FLOAT = 0x0003
EXTENSIBLE = 0xFFFE

BITS_PER_BYTE = 8
HEADER_BYTES = 12
CHUNK_HEADER_BYTES = 8
FMT_BYTES = 16
EXTENSIBLE_FMT_BYTES = 40
FLOAT32_BYTES = 4
FLOAT64_BYTES = 8
FLOAT_WIDTHS = (FLOAT32_BYTES, FLOAT64_BYTES)
STREAMED_SIZE = 0xFFFFFFFF


class RiffError(ValueError):
    """This file is not a WAV this module can read, and the message says which part failed."""


class Format(NamedTuple):
    """What the ``fmt `` chunk says, once the extensible indirection is resolved."""

    sample_rate: int
    channels: int
    sample_width: int
    is_float: bool
    frames: int

    @property
    def bit_depth(self) -> int:
        return self.sample_width * BITS_PER_BYTE

    @property
    def block_align(self) -> int:
        """Bytes per frame — one sample for each channel."""
        return self.sample_width * self.channels

    @property
    def encoding(self) -> str:
        """``pcm`` or ``float``, as the caller reports it and the sample maths branches on it."""
        return "float" if self.is_float else "pcm"

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate else 0.0


class Reader:
    """An open WAV positioned on its first sample.

    ``read_frames`` is the whole surface, because the one caller that does not want the file
    in memory at once — silence, which walks a two-hour concert a window at a time — wants
    exactly that and nothing else.
    """

    def __init__(self, stream: BinaryIO, layout: Format, start: int) -> None:
        self.format = layout
        self._stream = stream
        self._remaining = layout.frames * layout.block_align
        stream.seek(start)

    def read_frames(self, count: int) -> bytes:
        """Up to ``count`` whole frames, or ``b""`` once the data chunk is spent."""
        wanted = min(count * self.format.block_align, self._remaining)
        if wanted <= 0:
            return b""
        raw = self._stream.read(wanted)
        self._remaining -= len(raw)
        return raw

    def read_all(self) -> bytes:
        """Every frame left, which for every caller but silence means the whole file."""
        return self.read_frames(self.format.frames)


@contextlib.contextmanager
def opened(path: Path | str) -> Iterator[Reader]:
    """A WAV open on its samples, its header already parsed."""
    with Path(path).open("rb") as stream:
        layout, start = _parse(stream)
        yield Reader(stream, layout, start)


def header(path: Path | str) -> Format:
    """The header alone — no samples read, which matters when the file is gigabytes."""
    with Path(path).open("rb") as stream:
        return _parse(stream)[0]


def read(path: Path | str) -> tuple[Format, bytes]:
    """The header and every frame behind it."""
    with opened(path) as handle:
        return handle.format, handle.read_all()


def _parse(stream: BinaryIO) -> tuple[Format, int]:
    """Walk the chunks to the ``fmt `` and ``data`` pair, and report where the samples start."""
    prologue = stream.read(HEADER_BYTES)
    if len(prologue) < HEADER_BYTES or prologue[8:12] != b"WAVE":
        raise RiffError("not a RIFF/WAVE file")
    if prologue[0:4] == b"RF64":
        raise RiffError(
            "this is an RF64 file, the 64-bit variant of WAV written past the 4 GB "
            "limit, which this reader does not decode"
        )
    if prologue[0:4] != b"RIFF":
        raise RiffError(f"unexpected container {prologue[0:4]!r}, expected RIFF")

    fmt: bytes | None = None
    while True:
        head = stream.read(CHUNK_HEADER_BYTES)
        if len(head) < CHUNK_HEADER_BYTES:
            raise RiffError("no data chunk" if fmt else "no fmt chunk")
        name, declared = head[0:4], struct.unpack("<I", head[4:8])[0]
        if name == b"data":
            return _format(fmt, _data_bytes(stream, declared)), stream.tell()
        if name == b"fmt ":
            fmt = stream.read(declared)
        else:
            stream.seek(declared, 1)
        stream.seek(declared % 2, 1)  # every chunk is padded to an even length


def _data_bytes(stream: BinaryIO, declared: int) -> int:
    """The data chunk's real length, which is not always the one in its header.

    A WAV ffmpeg piped, or one whose writer died mid-render, declares a size it never wrote —
    sometimes ``0xFFFFFFFF``, sometimes the size the finished file would have had. Believing
    it would report a duration the file does not hold, so the rest of the file wins.
    """
    start = stream.tell()
    available = stream.seek(0, 2) - start
    stream.seek(start)
    return available if declared in (0, STREAMED_SIZE) else min(declared, available)


def _format(fmt: bytes | None, data_bytes: int) -> Format:
    if fmt is None:
        raise RiffError("no fmt chunk before the data chunk")
    if len(fmt) < FMT_BYTES:
        raise RiffError(f"fmt chunk is {len(fmt)} bytes, too short to describe a format")
    tag, channels, rate, _, _, bits = struct.unpack("<HHIIHH", fmt[:FMT_BYTES])
    if tag == EXTENSIBLE:
        tag = _subformat(fmt)
    if tag not in (PCM, IEEE_FLOAT):
        raise RiffError(f"format tag {tag} is neither PCM ({PCM}) nor IEEE float ({IEEE_FLOAT})")

    width = bits // BITS_PER_BYTE
    if bits % BITS_PER_BYTE or width == 0:
        raise RiffError(f"{bits}-bit samples do not fall on a byte boundary")
    if channels < 1 or rate < 1:
        raise RiffError(f"header claims {channels} channels at {rate} Hz")
    if tag == IEEE_FLOAT and width not in FLOAT_WIDTHS:
        raise RiffError(f"{bits}-bit floating point is neither single nor double precision")

    align = width * channels
    return Format(
        sample_rate=rate,
        channels=channels,
        sample_width=width,
        is_float=tag == IEEE_FLOAT,
        frames=data_bytes // align,
    )


def _subformat(fmt: bytes) -> int:
    """An extensible header's real tag: the first two bytes of its SubFormat GUID."""
    if len(fmt) < EXTENSIBLE_FMT_BYTES:
        raise RiffError(
            f"extensible fmt chunk is {len(fmt)} bytes, too short to hold a SubFormat GUID"
        )
    return int(struct.unpack("<H", fmt[24:26])[0])
