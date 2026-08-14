"""What the graphics layer burned into the picture, and where a cut lands on one.

A **super** is anything the graphics layer put on the frame rather than something a
camera saw: a lower third naming the band, a title card, a bug in the corner. The
questions this answers are the two an editor asks about one — *when is it up* and
*does a cut land inside it* — because a cut that lands inside a super is the one
mistake in this material that no amount of musical accuracy excuses. The lower third
is mid-word when the picture jumps, and the graphic reads as a mistake rather than as
a caption.

**Why the obvious reading does not work here.** A super is a graphic held still over
moving footage, so the obvious detector is "find what holds still". Measured on the
human deliverables that finds nothing: the cameras are locked off in a dark room, half
of every frame is already bit-identical to the frame before it, and a hold long enough
to exclude the stage furniture excludes the supers too. Static is what this footage
*is*.

What separates a graphic from the picture is not that it holds still but that **it is
not part of the picture at all** — so it survives the picture being replaced. Two frames
a couple of seconds apart often show different shots; everything the camera saw is then
different and everything the graphics layer drew is identical, to the grey level. That
is the reading here, and it needs no cut list, which matters because the scene detector
is blind to the dissolve-cut songs (#203) and those carry supers like any other.

Two shapes, told apart by how much of the frame agreed:

* **card** — the frames agree everywhere (``HELD``), which no two frames of this footage
  ever do: the screen is holding one picture, and if that picture carries a compact
  high-contrast region it is a graphic being held rather than a shot. A title card over
  black. The region requirement is what keeps a black gap or a freeze frame from reading
  as one.
* **overlay** — the pictures are genuinely different (``STEP``) and a compact
  high-contrast region agrees across them anyway. Lower thirds and titles over footage.

Everything else is ``unread`` — nothing is claimed. Three of this measurement's four
guards exist to widen that answer rather than narrow it, because the failure mode here is
not a missed caption but an invented one. On a dark stage of locked-off cameras the
frames of one *still* shot disagree pixel by pixel from noise alone, while its brightest
static object agrees with itself perfectly, in the same place, in every reading — and on
this stage that object is a piano keyboard with a maker's name written across it. So a
pair must show a real change of *composition* before what it agrees on is called a
graphic (``STEP``), and a span must be seen twice in the same pixels before it is
reported at all (:func:`_persists`).

Everything is measured on a 720x405 grey grid rather than the 128x72 one a cut's
composition is read on. Text is the subject, and text is the first thing a small grid
destroys.

What this cannot do: it cannot see a super that never outlives a picture change — one
held for less than the lag, or one held through a single long take. Both come back as
nothing rather than as absence, which is a floor on recall taken deliberately: see
``STEP``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray
from scipy.ndimage import binary_dilation, find_objects, label, uniform_filter

from resolve_mcp.video import framing

GRID_WIDTH = 720
GRID_HEIGHT = 405
"""The grid a super is read on, 16:9. Four times the linear resolution of the framing
grid, because the subject is lettering: the personnel lower third on the corpus anchor
is sixteen pixels tall here, and half of that is a smudge. Every threshold below is a
frame fraction or a grey level, so another grid of the same aspect still reads — this
is the size to prefer, not a requirement."""

LEVEL_TOL = 3.0
"""Grey levels two frames may differ by and still count as the same pixel. Wide enough
to absorb what the codec does to an unchanged region between keyframes, narrow enough
that a fade of any speed breaks it — which is what keeps a ramp out of the hold."""

SURROUND = 15
CONTRAST = 25.0
"""How far above its own surroundings a pixel has to sit to be graphic-like, and the box
that surrounding is averaged over. Burned-in supers are drawn to be read off a dark
stage, so they are the brightest thing local to them by a wide margin; the local
comparison rather than an absolute level is what keeps this from reading a stage lamp
as a caption, and what would still find dark lettering on a bright card."""

CHANGED = 0.65
"""At or below this share of pixels agreeing, the frames are worth reading further.

A cheap pre-filter and nothing more. Measured on the corpus anchor, agreement within one
locked-off shot three seconds apart runs 0.75-0.79 and across a shot change 0.28-0.60 — but
a *moving* shot dips to 0.55 without the picture having changed at all, which is why
whether the picture really changed is asked separately, and asked of the composition."""

STEP = 0.30
"""How far the picture must have stepped between two frames before what they agree about
counts as a graphic.

This is the whole precision of the overlay reading. Pixel agreement says only that the
frames differ, and on a dark stage they differ from noise; a locked-off camera then offers
a bright static band with a maker's name across it — a piano keyboard — that agrees with
itself in every reading, in the same place, forever. Composition is what tells a new
picture from a still one, so the step is `video.framing`'s own reading of that.

Measured on the corpus anchor at a two-second lag: pairs inside one shot step 0.01-0.17,
pairs across a shot change step 0.47-0.68. This sits in the empty middle, nearer the still
population because the cost of the two errors is not symmetric — a graphic invented under a
critic's cut is worse than one missed.

What it costs: a super that never outlives a shot boundary is not reported at all. On
Hardest Part the personnel lower third holds through one long take and comes back as
nothing. That is a floor on *recall*, deliberately, and the receipt names it."""

HELD = 0.98
"""At or above this share, nothing changed at all — the screen is holding one picture.

The same measurement: no two footage frames of the corpus anchor a second apart reach
0.87, and the Taurus People title card reaches 1.0000. There is no third population in
between, which is why the band between ``CHANGED`` and this one is refused rather than
split."""

CLUSTER = 9
"""The box lettering is dilated by before it is grouped, so the strokes of a word — and
the words of a line — become one graphic rather than forty. Sized to close the gap
between letters at this grid without reaching across the frame to an unrelated one."""

MIN_AREA = 0.0004
"""The share of the frame a graphic has to cover to be reported: about 120 pixels here.
Below it sit the coincidences — the handful of specular highlights that happen to agree
across a shot change — which run to a few dozen pixels and no more."""

MAX_BOX = 0.35
"""The share of the frame a graphic's bounding box may cover. A super is a caption, not
a picture: when the surviving pixels are scattered corner to corner, what agreed is a
half-static shot rather than a graphic, and the box is the cheapest thing that tells
those apart."""

HOLD_SHARE = 0.90
"""The share of a graphic's own pixels that must still match for it to count as up, when
its edges are walked out frame by frame. Not all of them, because a super over moving
footage has anti-aliased edges that the picture behind bleeds through."""

RAMP_CONTRAST = CONTRAST / 2
"""How far above its surroundings a graphic's own pixels have to stay for it to count as
still on screen while it fades. Half the level that finds it in the first place: a super
at half opacity is still a super a cut can land on, and a region that has become footage
sits nowhere near either number."""

OVERLAY = "overlay"
CARD = "card"
ABSENT = "absent"
UNREAD = "unread"
"""The four readings a frame pair can carry. ``ABSENT`` is a real answer — the pictures
differed and nothing survived — where ``UNREAD`` is the refusal."""


class Region(NamedTuple):
    """One graphic found in one frame pair, boxed in frame fractions.

    Fractions rather than pixels so a reading taken on one grid can be drawn on another,
    and so a box is legible without knowing what it was measured on: a lower third is
    ``top`` around 0.85 whatever the decode.
    """

    top: float
    left: float
    bottom: float
    right: float
    pixels: int
    share: float

    def as_record(self) -> dict[str, Any]:
        return dict(self._asdict())


class Reading(NamedTuple):
    """One frame pair: what the two frames agreed about, and what that means."""

    kind: str
    agreement: float
    regions: tuple[Region, ...]

    @property
    def found(self) -> bool:
        """Whether this pair carries a super — the one question its consumers ask."""
        return self.kind in (OVERLAY, CARD)


class Span(NamedTuple):
    """One super, over the stretch of frames it is up for.

    ``first`` and ``last`` are inclusive and in whatever clock the caller counted the
    frames in — scan indices out of :func:`read_run`, source frames once a caller has
    refined them. ``ramp_in`` and ``ramp_out`` are the fade frames either side of that
    hold, zero until something measures them: the hold is the part that can be proved,
    a ramp is the part that has to be walked to.
    """

    kind: str
    first: int
    last: int
    top: float
    left: float
    bottom: float
    right: float
    pixels: int
    pairs: int
    ramp_in: int = 0
    ramp_out: int = 0

    @property
    def visible_first(self) -> int:
        """The first frame anything of this super is on screen, ramp included."""
        return self.first - self.ramp_in

    @property
    def visible_last(self) -> int:
        """The last frame anything of this super is on screen, ramp included."""
        return self.last + self.ramp_out

    @property
    def frames(self) -> int:
        return self.visible_last - self.visible_first + 1

    def as_record(self) -> dict[str, Any]:
        record = dict(self._asdict())
        record["visible_first"] = self.visible_first
        record["visible_last"] = self.visible_last
        record["frames"] = self.frames
        return record


class Edges(NamedTuple):
    """Where a super starts and stops, walked out frame by frame from inside it."""

    first: int
    last: int
    ramp_in: int
    ramp_out: int


def read_pair(before: NDArray[Any], after: NDArray[Any]) -> Reading:
    """Read two frames of the same clip against each other for burned-in graphics.

    The two are expected to be seconds apart rather than adjacent: the whole reading
    rests on the picture having had time to change, and two adjacent frames of a locked
    camera are the case this refuses.
    """
    return _read(before, after)[0]


def carried(before: NDArray[Any], after: NDArray[Any]) -> NDArray[np.bool_]:
    """The graphic's own pixels, as a mask over the frame.

    The same reading :func:`read_pair` boxes, handed back unboxed — because walking a
    super's edges has to ask about the lettering rather than about the rectangle around
    it, and over moving footage that rectangle is mostly footage.
    """
    return _read(before, after)[1]


def _read(
    before: NDArray[Any], after: NDArray[Any]
) -> tuple[Reading, NDArray[np.bool_]]:
    """One frame pair, read once: the verdict and the pixels it was reached on."""
    out = np.asarray(before, dtype=np.float32)
    into = np.asarray(after, dtype=np.float32)
    same = np.abs(out - into) <= LEVEL_TOL
    agreement = round(float(same.mean()), 4)
    empty = np.zeros(same.shape, dtype=bool)

    if CHANGED < agreement < HELD:
        return Reading(kind=UNREAD, agreement=agreement, regions=()), empty

    mask, regions = _found(out, into, same)
    if not regions:
        return Reading(kind=ABSENT, agreement=agreement, regions=()), empty
    if agreement >= HELD:
        return Reading(kind=CARD, agreement=agreement, regions=regions), mask
    if _step(out, into) < STEP:
        # Agreeing pixels, but between two frames of the same picture: whatever is bright
        # and static in a locked-off shot agrees with itself, and on this stage that is a
        # piano keyboard with a maker's name written across it. Nothing is claimed.
        return Reading(kind=UNREAD, agreement=agreement, regions=()), empty
    return Reading(kind=OVERLAY, agreement=agreement, regions=regions), mask


def read_run(
    frames: NDArray[Any],
    lag: int,
    bridge: int = 1,
) -> tuple[Span, ...]:
    """Every super in a decoded run of frames, as spans of the run's own indices.

    Each frame is read against the one ``lag`` ahead of it, and a pair that carries a
    super marks *both* its ends and everything between: the graphic is identical at two
    frames a lag apart, so it was up across that whole stretch. A super therefore has to
    outlast the lag to be seen at all, which is the price of not needing a cut list — and
    to outlast it twice over, because a span only survives :func:`_persists` if two
    readings agree on where the graphic was.

    ``bridge`` is how many scanned frames of doubt a span may contain without being cut
    in two — a pair whose picture happened not to change is an ``unread``, not the end of
    the graphic, and a super that outlives one long locked-off shot would otherwise come
    back as two supers with a hole between them.
    """
    if lag < 1:
        raise ValueError(f"A lag of {lag} frames reads a frame against itself; use 1 or more.")
    total = len(frames)
    up = np.zeros(total, dtype=bool)
    kinds: list[str] = [""] * total
    seen: list[list[Region]] = [[] for _ in range(total)]
    masks: dict[int, NDArray[np.bool_]] = {}
    for index in range(total - lag):
        reading, mask = _read(frames[index], frames[index + lag])
        if not reading.found:
            continue
        up[index : index + lag + 1] = True
        masks[index] = mask
        for at in (index, index + lag):
            kinds[at] = reading.kind
            seen[at].extend(reading.regions)
    return _spans(up, kinds, seen, masks, bridge)


def edges(window: NDArray[Any], mask: NDArray[np.bool_], anchor: int) -> Edges:
    """Walk out from a frame the graphic is known up on, to the frames it holds between.

    ``mask`` is the graphic's own pixels, so the walk asks about the lettering rather
    than about the box around it — a box over moving footage is mostly footage, and
    comparing all of it would end the super at the first thing that moved behind it.

    A hold ends where the pixels stop matching, which a fade breaks on its first frame.
    The walk therefore keeps going while the graphic is *still drawn there* — its own
    pixels still standing out from what surrounds them — and reports those frames as the
    ramp: a super that fades is on screen through its fade, and a cut there lands on it as
    squarely as one in the middle.

    The ramp test is that lift and not the mere fact that the region changed. A hold that
    ends because the shot ended also changes, into footage, and a ramp walk that counted
    it would push the super past the very cut it cleared for — turning the convention this
    exists to measure into the violation it exists to catch.
    """
    if not mask.any():
        raise ValueError("An empty mask has no graphic to walk the edges of.")
    held = np.asarray(window[anchor], dtype=np.float32)[mask]

    def matches(index: int) -> bool:
        frame = np.asarray(window[index], dtype=np.float32)[mask]
        return bool((np.abs(frame - held) <= LEVEL_TOL).mean() >= HOLD_SHARE)

    def drawn(index: int) -> bool:
        frame = np.asarray(window[index], dtype=np.float32)
        return bool(_lift(frame)[mask].mean() >= RAMP_CONTRAST)

    first = anchor
    while first - 1 >= 0 and matches(first - 1):
        first -= 1
    last = anchor
    while last + 1 < len(window) and matches(last + 1):
        last += 1

    ramp_in = 0
    while first - ramp_in - 1 >= 0 and drawn(first - ramp_in - 1):
        ramp_in += 1
    ramp_out = 0
    while last + ramp_out + 1 < len(window) and drawn(last + ramp_out + 1):
        ramp_out += 1
    return Edges(first=first, last=last, ramp_in=ramp_in, ramp_out=ramp_out)


def straddles(spans: Sequence[Span], cuts: Sequence[int]) -> tuple[dict[str, Any], ...]:
    """Every cut that lands inside a super, counted in the clock both were measured in.

    A cut *at* a super's first frame is the super arriving with the new shot, and a cut
    one past its last frame is the super clearing for it — the two edits this exists to
    tell apart from the mistake. Only a cut with the graphic on screen either side of it
    is a straddle, and the record says how deep into the super it landed so that a cut
    two frames from the end reads differently from one mid-word.
    """
    found: list[dict[str, Any]] = []
    for span in spans:
        for cut in cuts:
            if span.visible_first < cut <= span.visible_last:
                found.append(
                    {
                        "cut": int(cut),
                        "kind": span.kind,
                        "in": span.visible_first,
                        "out": span.visible_last,
                        "into_super": int(cut - span.visible_first),
                        "left_of_super": int(span.visible_last - cut + 1),
                    }
                )
    return tuple(sorted(found, key=lambda one: one["cut"]))


def clearance(span: Span, cuts: Sequence[int]) -> int | None:
    """Frames between a super's last visible frame and the next cut after it.

    This is the title-card convention as a number: the human's cards clear the frame
    before the entrance they announce, which reads here as ``1``. ``None`` where no cut
    follows the super at all — the end of the piece is not a tight clearance, and
    counting it as one would put a made-up number in the same column as measured ones.
    """
    later = [cut for cut in cuts if cut > span.visible_last]
    return int(min(later) - span.visible_last) if later else None


def review(spans: Sequence[Span], cuts: Sequence[int]) -> dict[str, Any]:
    """The supers, the cuts that land inside one, and how much room each leaves.

    One block rather than three calls, because the count of straddles is only readable
    beside the count of supers it was drawn from: no straddles over no supers is a clean
    bill of health from a measurement that found nothing to check.
    """
    records = []
    for span in spans:
        record = span.as_record()
        record["clears_before"] = clearance(span, cuts)
        records.append(record)
    caught = straddles(spans, cuts)
    return {
        "supers": records,
        "cards": sum(1 for span in spans if span.kind == CARD),
        "overlays": sum(1 for span in spans if span.kind == OVERLAY),
        "cuts": len(cuts),
        "straddled": len(caught),
        "straddles": list(caught),
        "held_frames": _lengths(spans),
    }


def _step(out: NDArray[np.float32], into: NDArray[np.float32]) -> float:
    """How far the picture itself stepped between the two frames.

    The reading a cut is judged by (:mod:`resolve_mcp.video.framing`), asked of two frames
    that may be nowhere near a cut. Pixel agreement cannot answer this: on a dark stage two
    frames of the *same* shot disagree pixel by pixel from noise alone, so a still picture
    can look as changed as a new one. Composition cannot be faked that way.

    Taken on a strided-down copy, because framing's own grid is a composition grid and this
    one is a lettering grid — twelve times the pixels, for a question that does not want
    them.
    """
    stride = max(1, out.shape[1] // framing.GRID_WIDTH)
    return framing.read_pair(out[::stride, ::stride], into[::stride, ::stride]).delta


def _lift(frame: NDArray[np.float32]) -> NDArray[np.float32]:
    """How far each pixel sits above its own surroundings."""
    lifted: NDArray[np.float32] = frame - uniform_filter(frame, size=SURROUND)
    return lifted


def _found(
    out: NDArray[np.float32],
    into: NDArray[np.float32],
    same: NDArray[np.bool_],
) -> tuple[NDArray[np.bool_], tuple[Region, ...]]:
    """The graphic-like pixels two frames agreed on, and the captions they spell."""
    graphic = same & (_lift(out) > CONTRAST) & (_lift(into) > CONTRAST)
    return _regions(graphic)


def _regions(graphic: NDArray[np.bool_]) -> tuple[NDArray[np.bool_], tuple[Region, ...]]:
    """The graphic-like pixels, grouped into the captions they spell.

    The mask comes back beside the boxes with everything too small or too scattered to be
    lettering already dropped out of it, so the pixels a caller walks the edges on are the
    same pixels the box was drawn around.
    """
    empty = np.zeros_like(graphic)
    if not graphic.any():
        return empty, ()
    height, width = graphic.shape
    area = float(height * width)
    grouped, count = label(binary_dilation(graphic, np.ones((CLUSTER, CLUSTER), dtype=bool)))
    if count == 0:
        return empty, ()
    # Counted off the undilated mask, so a group's size is its lettering rather than the
    # halo the grouping drew around it.
    sizes = np.bincount(grouped[graphic].ravel(), minlength=count + 1)
    kept = np.zeros_like(graphic)
    found: list[Region] = []
    for tag, box in enumerate(find_objects(grouped), start=1):
        if box is None or sizes[tag] < MIN_AREA * area:
            continue
        lettering = graphic[box] & (grouped[box] == tag)
        rows, cols = np.nonzero(lettering)
        if not len(rows):
            continue
        top = box[0].start + int(rows.min())
        bottom = box[0].start + int(rows.max()) + 1
        left = box[1].start + int(cols.min())
        right = box[1].start + int(cols.max()) + 1
        if (bottom - top) * (right - left) > MAX_BOX * area:
            continue
        kept[box] |= lettering
        found.append(
            Region(
                top=round(top / height, 4),
                left=round(left / width, 4),
                bottom=round(bottom / height, 4),
                right=round(right / width, 4),
                pixels=int(sizes[tag]),
                share=round(float(sizes[tag]) / area, 6),
            )
        )
    return kept, tuple(sorted(found, key=lambda one: -one.pixels))


def _spans(
    up: NDArray[np.bool_],
    kinds: Sequence[str],
    seen: Sequence[Sequence[Region]],
    masks: Mapping[int, NDArray[np.bool_]],
    bridge: int,
) -> tuple[Span, ...]:
    """Frames a super was up on, grouped into the supers they belong to."""
    spans: list[Span] = []
    start: int | None = None
    gap = 0
    for index in range(len(up) + 1):
        lit = index < len(up) and bool(up[index])
        if lit:
            if start is None:
                start = index
            gap = 0
            continue
        if start is None:
            continue
        gap += 1
        if gap <= bridge and index < len(up):
            continue
        if _persists(start, index - gap, masks):
            spans.append(_span(start, index - gap, kinds, seen))
        start, gap = None, 0
    return tuple(spans)


def _persists(first: int, last: int, masks: Mapping[int, NDArray[np.bool_]]) -> bool:
    """Whether the same pixels were graphic every time this span was read.

    The test that separates a caption from a coincidence, and the only one that survives
    this material. On a dark stage two frames of the *same* shot disagree pixel by pixel
    from noise alone, so "the picture changed" is satisfied by footage sitting still, and
    whatever is bright and static in it — a music stand, the lit edge of a piano — carries
    across as reliably as lettering does. What it cannot do is carry across as the *same*
    pixels twice: each reading picks up a different scatter, and intersecting two of them
    leaves nothing. A graphic is drawn from the same file every frame, so the intersection
    is the graphic.

    A span with one reading behind it has nothing to intersect and is dropped: a super seen
    once is indistinguishable from a coincidence seen once, and this measurement exists to
    be quoted at an editor.
    """
    inside = [mask for at, mask in masks.items() if first <= at <= last]
    if len(inside) < 2:
        return False
    held = inside[0].copy()
    for mask in inside[1:]:
        held &= mask
    return bool(held.sum() >= MIN_AREA * held.size)


def _span(
    first: int,
    last: int,
    kinds: Sequence[str],
    seen: Sequence[Sequence[Region]],
) -> Span:
    """One span, boxed around every graphic that showed inside it.

    The box is the union rather than one pair's reading: where a lower third and a bug are
    up together the span covers both, and a consumer that wants them apart wants the pair
    readings, not this. The kind follows the majority of the pairs that carried it, so one
    frozen pair inside a long overlay does not rename it a card.
    """
    regions = [one for index in range(first, last + 1) for one in seen[index]]
    votes = [kinds[index] for index in range(first, last + 1) if kinds[index]]
    kind = CARD if votes.count(CARD) > votes.count(OVERLAY) else OVERLAY
    return Span(
        kind=kind,
        first=first,
        last=last,
        top=round(min((one.top for one in regions), default=0.0), 4),
        left=round(min((one.left for one in regions), default=0.0), 4),
        bottom=round(max((one.bottom for one in regions), default=0.0), 4),
        right=round(max((one.right for one in regions), default=0.0), 4),
        pixels=max((one.pixels for one in regions), default=0),
        pairs=len(votes),
    )


def _lengths(spans: Sequence[Span]) -> dict[str, Any] | None:
    """How long the supers in a run are held, for a report that has to say whether a
    measurement found two title cards or forty phantom ones."""
    if not spans:
        return None
    held = np.asarray([span.frames for span in spans], dtype=np.float64)
    return {
        "min": int(held.min()),
        "median": int(np.median(held)),
        "max": int(held.max()),
    }
