"""Where the master mix sits under a timeline — the one axis a rebuild does not move.

A concert cut is one continuous mix clip with the pictures laid over it (#22: the cutting
substrate). Every *record* frame on such a timeline is provisional: tighten a segment near
the head and everything downstream slides, which is why a rebuild produces a new version
rather than editing the reviewed one. The mix's own frames do not slide, because the mix is
one clip nobody re-times — so "which frame of the mix is under this record frame" is the
only coordinate two versions of the same cut agree on.

That reading is a single number, :attr:`MixShot.zero_frame`: the record frame the mix's own
first frame lands on. Everything built from it is addition. Two callers need it —
``correlate_timeline`` turns timeline positions into seconds of the analysed mix, and
``build_timeline`` carries hand-placed markers from the previous version onto the one it
just made — so the reading lives here once rather than in each, and cannot come out
differently in the two.

What is deliberately *not* shared is how hard each caller insists on it. Reading and
writing carry different costs for being wrong. ``correlate`` is producing a measurement to
look at: if nothing matches the file it analysed it falls back to the first audio clip and
labels the result ``matched: False``, so an assumed anchor is still useful and says it was
assumed. The carry is producing *marker writes*: there is nowhere to put a caveat on a
marker in the GUI, so it takes :func:`anchor`, which answers only when the shots agree, and
otherwise carries nothing. Same reading, two risk postures — that difference is the point,
not a drift between them.

The subtlety worth the module: a source frame is counted from the *start of the media
file*, not from the clip's own start timecode. A WAV stamped 01:00:00:00 reports source
frames an hour in, and forgetting to subtract that stamp shifts every derived time by an
hour — a failure that looks like a plausible reading rather than an error.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

from .timeline import Reader, clip_name, items_in_track, read_frames, source_bounds

Timeline = Any
TimelineItem = Any


class MixShot(NamedTuple):
    """One audio clip, in the two numbers that place it against its own media."""

    name: str
    record_in: int
    source_in: int
    """Counted from the first frame of the file, with any start stamp already subtracted."""

    @property
    def zero_frame(self) -> int:
        """The record frame this clip's own frame 0 lands on, extended past its in point.

        The number is not a position on the timeline — for a clip that starts part-way into
        its media it is before the clip, and may be before the timeline's own start. It is
        the constant that converts in both directions, which is all a caller wants from it.
        """
        return self.record_in - self.source_in


def audio_shots(reader: Reader, timeline: Timeline) -> list[MixShot]:
    """Every audio shot that will say where it sits, in track then timeline order.

    A shot that cannot answer both numbers is left out rather than guessed at: a caller
    choosing an anchor from this list must be choosing between readings it can trust.
    """
    count = int(read_frames(reader.optional(timeline, "GetTrackCount", 0, "audio")) or 0)
    found: list[MixShot] = []
    for index in range(1, count + 1):
        for item in items_in_track(timeline, "audio", index):
            record_in = read_frames(item.GetStart())
            source_in, _ = source_bounds(reader, item, read_frames(item.GetDuration()))
            if record_in is None or source_in is None:
                continue
            name = clip_name(reader, item) or str(item.GetName() or "")
            found.append(MixShot(name, record_in, source_in - _media_start(reader, item)))
    return found


def _media_start(reader: Reader, item: TimelineItem) -> int:
    """The first frame of the media itself, which is not zero on anything with a start stamp."""
    clip = reader.optional(item, "GetMediaPoolItem", None)
    if clip is None:
        return 0
    return read_frames(reader.optional(clip, "GetClipProperty", None, "Start")) or 0


def anchor(shots: Sequence[MixShot], name: str | None = None) -> MixShot | None:
    """The placement these shots agree on, or ``None`` when they do not agree on one.

    Counting items would be the obvious rule and is the wrong one. Resolve spreads a
    multi-channel clip across one track per channel, so a *single* append of an eight-channel
    mix reads back as eight shots — same clip, same record frame, same source frame, on A1
    to A8. Measured live on Studio 21.0.3.7 with an 8-channel MXF; a rule that wanted one
    item would refuse the commonest concert mix there is.

    What decides the question is not how many items there are but whether they agree where
    the mix begins. So the shots are narrowed to ``name`` when the caller knows which clip
    it wants, and answered only when every remaining one puts the mix's frame 0 on the same
    record frame. Disagreement is answered like absence: a timeline carrying the same clip
    at two offsets cannot say which one a derived position should be counted from, and
    picking the first would put every one of them on a coin toss.
    """
    wanted = [shot for shot in shots if name is None or shot.name == name]
    if not wanted or len({(shot.name, shot.zero_frame) for shot in wanted}) != 1:
        return None
    return wanted[0]


__all__ = ["MixShot", "anchor", "audio_shots"]
