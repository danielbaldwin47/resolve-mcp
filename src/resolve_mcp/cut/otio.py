"""The tail as document surgery: cutting a fade into an exported OTIO, and reading it back.

Nothing here talks to Resolve. An OTIO document is a dict of dicts, so every decision this
module makes — which track the picture ends on, whether the last shot is long enough to
fade, what a transition is in frames — is answerable with a document alone, and the tests
for it are plain dicts rather than fakes. The half that owns the export/import round trip
is :mod:`resolve_mcp.resolve.tail`; it calls :func:`inject` before the import and
:func:`transitions` after it, and those two are compared to each other by track name.

Two things are decisions rather than mechanics, and both are about where the picture
actually stops:

* **The fade goes after the last shot, not after the last child.** Resolve pads every
  exported track with a trailing ``Gap`` out to the length of the *timeline*, so a concert
  cut — whose mix is authored to outlive the picture, as all five surveyed deliverables do
  — exports a V1 that ends in black (verified live, 21.0.3: a 480-frame picture under a
  520-frame mix exported V1 as three clips and a 40-frame gap). A transition appended to
  the end of that track would dissolve black into black. Nothing is appended *after* the
  picture either: black at the end of a cut is the cut file's own gap, subject to W8 like
  any other, and adding one here would be this module inventing record time.
* **Every frame count is read in the timeline's rate.** A tail counts in the rate the cut
  file counts in; OTIO stamps each clip in its own *media* rate. On the ordinary
  mixed-rate concert kit those are not the same number of frames, and comparing them raw
  measures two layers in two units.
"""

from __future__ import annotations

from typing import Any, Final

from .tail import Tail

Document = dict[str, Any]
Track = dict[str, Any]
Item = dict[str, Any]

DISSOLVE: Final = "SMPTE_Dissolve"
"""The only transition type in play: a fade to black is a dissolve into nothing."""


def inject(document: Document, tail: Tail) -> dict[str, Any]:
    """Edit ``tail`` into an exported OTIO document. Returns what was put where.

    The dissolve goes on every video track that reaches the end of the cut, which on an
    ordinary concert build is V1 alone: an overlay that stopped earlier has nothing to do
    with how the picture leaves, and fading it would be a second device nobody asked for.
    The audio fade goes on every audio track's last clip, because the master mix is the only
    thing this pillar puts there.

    Also returned is what did *not* get one where one was needed — ``unfaded_video`` and
    ``unfaded_audio``. A per-track refusal is invisible in the totals: a document where V1
    faded and an opaque V2 did not still reports a dissolve, and the picture then pops back
    out of black the frame the overlay ends. The caller refuses on either list rather than
    delivering half a device.
    """
    video: list[str] = []
    audio: list[str] = []
    unfaded_video: list[str] = []
    unfaded_audio: list[str] = []
    rate = _timeline_rate(document)
    if tail.dissolves:
        ending = _tracks(document, "Video", last_only=True, rate=rate)
        for track in ending:
            placed = video if _append_transition(track, rate, tail.frames) else unfaded_video
            placed.append(_name(track))
        unfaded_video.extend(_name(track) for track in _inside(document, tail, ending, rate))
    if tail.fades_audio:
        for track in _tracks(document, "Audio", last_only=False, rate=rate):
            placed = audio if _append_transition(track, rate, tail.audio_frames) else unfaded_audio
            placed.append(_name(track))
    return {
        "video_tracks": video,
        "audio_tracks": audio,
        "unfaded_video": unfaded_video,
        "unfaded_audio": unfaded_audio,
    }


def _name(track: Track) -> str:
    """What a track is called on *both* sides of the round trip — one vocabulary, not two.

    An OTIO track need not carry a name, and :func:`inject` and :func:`transitions` are
    compared to each other, by name, by the round trip's own read-back. A fallback invented
    separately on each side is therefore not a cosmetic difference: an unnamed track would
    have its dissolve recorded under one word and read back under another, so every such
    build would refuse a tail that landed perfectly — and take a correct import down with it
    on the way out.
    """
    return str(track.get("name") or "") or str(track.get("kind") or "").lower()


def _inside(document: Document, tail: Tail, ending: list[Track], rate: float) -> list[Track]:
    """Video tracks whose picture stops *inside* the dissolve, which nothing here fades.

    Such a track is opaque over part of the ramp and then ends, so the picture underneath
    comes back partway through a fade to black — a visible second ending. It is not a track
    to fade either: the dissolve reaches back into the shot that ends the cut, and one
    starting where an overlay happens to stop would be a device nobody asked for. So it is
    reported, and the build refuses rather than delivering a tail with a hole in it.
    """
    others = [
        track
        for track in _tracks(document, "Video", last_only=False, rate=rate)
        if not any(track is one for one in ending)
    ]
    furthest = max((_span(track, rate) for track in ending), default=0)
    return [track for track in others if furthest - tail.frames < _span(track, rate) < furthest]


def _tracks(document: Document, kind: str, last_only: bool, rate: float) -> list[Track]:
    """The tracks of one kind a transition belongs on, in document order.

    ``last_only`` keeps the video dissolve to the layer the cut actually ends on. Ties are
    kept, not broken: two video tracks both running to the last frame are both the end of
    the picture, and fading one of them would leave the other opaque over black.
    """
    found = [
        track
        for track in ((document.get("tracks") or {}).get("children") or [])
        if track.get("kind") == kind and _clips(track)
    ]
    if not last_only or not found:
        return found
    furthest = max(_span(track, rate) for track in found)
    return [track for track in found if _span(track, rate) == furthest]


def _clips(track: Track) -> list[Item]:
    return [item for item in (track.get("children") or []) if _is_clip(item)]


def _last_clip(track: Track) -> int:
    """Where the last clip on this track sits among its children, or -1 if there is none.

    Not simply the last child. Resolve pads every track it exports with a trailing ``Gap``
    out to the length of the *timeline*, so a concert cut — whose mix is authored to outlive
    the picture, as all five surveyed deliverables do — exports a V1 that ends in black
    rather than in a shot (verified live, 21.0.3). A fade appended after that gap would
    dissolve black into black.
    """
    children = list(track.get("children") or [])
    for index in range(len(children) - 1, -1, -1):
        if _is_clip(children[index]):
            return index
    return -1


def _span(track: Track, rate: float) -> int:
    """Where the *picture* on this track stops — trailing black is not part of the answer.

    Measured to the end of the last clip rather than to the end of the track, for the same
    reason: Resolve pads every track out to the timeline's length, so track length is the
    one number that cannot tell the layer the cut ends on from a layer that stopped early.

    In *timeline* frames, which is what makes the answer comparable across tracks at all: a
    mixed-rate multicam stack carries a different media rate per clip, and summing the raw
    numbers would measure two layers in two units and call the shorter one the end.

    Summed unrounded and rounded once, because this is a sum and the comparison it feeds is
    an equality. Rounding per clip spends up to half a frame each time, so a forty-shot V1
    can come out two frames short of an overlay that ends on the very same frame — which
    drops V1 out of the ending layers, straight into the window ``_inside`` refuses, and the
    build then fails a correct mixed-rate cut with the shots already on a staging timeline.
    """
    children = list(track.get("children") or [])
    last = _last_clip(track)
    return round(
        sum(
            _exact(_duration(item), rate)
            for item in children[: last + 1]
            if not _is_transition(item)
        )
    )


def _append_transition(track: Track, rate: float, frames: int) -> bool:
    """Put a fade at the end of the track's picture, or answer False and leave it alone.

    The transition goes immediately after the last clip, which is a clip→gap boundary
    whenever something outlives the picture — reaching ``frames`` back into the shot and
    nothing forward, so it lands on black on the shot's own last frame.

    Refused rather than trimmed when that clip is too short: the length was validated (E12)
    against the cut file, so a document that cannot carry it means the built timeline
    disagrees with the cut, and quietly shortening the device would hide that.

    Both numbers are timeline frames. ``frames`` comes from the cut file, which counts in
    the timeline's rate; the clip's own duration is stamped in its *media* rate, so on a
    mixed-rate multicam the raw comparison asks whether a 23.976 count fits inside a 25
    one — and answers "the shot is too short to fade" about a shot that is not.
    """
    children = list(track.get("children") or [])
    index = _last_clip(track)
    if index < 0 or _frames(children[index], rate) <= frames:
        return False
    children.insert(index + 1, _transition(rate or _rate(children[index]), frames))
    track["children"] = children
    return True


def _transition(rate: float, frames: int) -> Item:
    """A dissolve reaching ``frames`` back into what precedes it and nothing forward.

    ``out_offset`` of zero is what makes it a fade *out* rather than a cross: there is
    nothing after it to reach into, and black is the absence of the frames it gives up.
    """
    return {
        "OTIO_SCHEMA": "Transition.1",
        "name": "Fade to Black",
        "metadata": {},
        "transition_type": DISSOLVE,
        "in_offset": _rational(rate, frames),
        "out_offset": _rational(rate, 0),
    }


def transitions(document: Document) -> list[dict[str, Any]]:
    """Every transition in a document, as ``{track, kind, in_offset}`` — the read-back.

    ``track`` is named by the same helper :func:`inject` records with, and ``in_offset`` is
    in timeline frames, because both are compared against what ``inject`` asked for.
    """
    rate = _timeline_rate(document)
    found = []
    for track in (document.get("tracks") or {}).get("children") or []:
        for item in track.get("children") or []:
            if _is_transition(item):
                found.append(
                    {
                        "track": _name(track),
                        "kind": str(track.get("kind") or ""),
                        "name": str(item.get("name") or ""),
                        "in_offset": _at_rate(item.get("in_offset"), rate),
                    }
                )
    return found


def _rational(rate: float, value: int) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": value}


def _is_clip(item: Item) -> bool:
    return str(item.get("OTIO_SCHEMA", "")).startswith("Clip.")


def _is_transition(item: Item) -> bool:
    return str(item.get("OTIO_SCHEMA", "")).startswith("Transition.")


def _duration(item: Item) -> dict[str, Any]:
    duration: dict[str, Any] = ((item.get("source_range") or {}).get("duration")) or {}
    return duration


def _frames(item: Item, rate: float) -> int:
    """One item's duration in whole timeline frames."""
    return _at_rate(_duration(item), rate)


def _rate(item: Item) -> float:
    try:
        return float(_duration(item).get("rate") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _timeline_rate(document: Document) -> float:
    """The rate the *timeline* counts in, or 0.0 when the document does not say.

    Every frame count a tail carries — ``duration_frames``, ``audio_fade_frames`` — is in
    the timeline's rate, because that is the rate the cut file counts in and the rate E12
    validated them against. OTIO stamps each clip's ``source_range`` in its own *media*
    rate instead, so on a mixed-rate multicam (an FX6 at 23.976 beside an A7IV at another
    rate, the ordinary concert kit) the two units are not the same number of frames.

    ``global_start_time`` is the OTIO timeline's own ``RationalTime``, and its rate is the
    timeline's — the one number in the document that is not somebody's media. **Not yet
    confirmed against a live mixed-rate export**; every rate in the documents seen so far is
    the same rate, which is exactly why this cannot be told apart by looking at them.

    Zero — a document that carries no start time — means "do not convert" rather than a
    guess: the arithmetic then runs on the items' own numbers, as it did before it could ask.
    """
    time = document.get("global_start_time")
    if not isinstance(time, dict):
        return 0.0
    try:
        rate = float(time.get("rate") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return rate if rate > 0 else 0.0


def _at_rate(time: Any, rate: float) -> int:
    """A ``RationalTime`` as a whole number of frames at ``rate``, its own rate if that is 0."""
    return round(_exact(time, rate))


def _exact(time: Any, rate: float) -> float:
    """The same reading, unrounded — what a sum has to be built out of. See :func:`_span`.

    A rate this cannot read is not a reason to measure the item as nothing. Only ``value``
    decides how long something is; the rate decides what unit that is in, and an unreadable
    one means the value is taken as it comes — which is what this module did before it
    converted between rates at all. Reading the two in one ``try`` would instead turn a
    stray rate into a zero-length clip, a track measuring zero, and every fade on it
    refused.
    """
    if not isinstance(time, dict):
        return 0.0
    try:
        value = float(time.get("value") or 0)
    except (TypeError, ValueError):
        return 0.0
    if rate <= 0:
        return value
    try:
        own = float(time.get("rate") or 0.0)
    except (TypeError, ValueError):
        return value
    return value * rate / own if own > 0 and own != rate else value


__all__ = ["Document", "inject", "transitions"]
