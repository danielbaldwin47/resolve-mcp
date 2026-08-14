"""How far the picture steps across a cut, and whether it stepped far enough.

The motion track answers *what moved inside a shot*. This answers the other
question a viewer-judge keeps asking and no measurement here could reach: **is the
shot after the cut a different picture from the shot before it?** A cut between two
genuinely different angles is invisible — the eye accepts it. A cut between two
near-identical framings of the same subject is a jump: the frame twitches, the
subject teleports a few pixels, and the edit announces itself. That is what the
30-degree rule is for, and the rule has an escape clause — *angle or size*. Cutting
along the same sight line is legal if the shot size really changes, which is why a
size term sits beside the layout term rather than under it.

Four terms, each a fraction, each measuring a different way two frames can differ:

* **layout** — where the light is, allowing for a slide. The frame's row and column
  profiles are cross-correlated over a small lag, so a picture that merely moved
  sideways still matches itself. What survives a lag search is composition: a real
  angle change moves the subject relative to the background and no shift undoes it.
  The lag itself is reported, because *matched at a shift of three pixels* is the
  exact signature of the cut this measurement exists to catch.
* **structure** — where the light is, in two dimensions. Row and column profiles are
  *marginals*, and marginals are blind by construction: two pictures with their
  bright patches at opposite corners have identical profiles on both axes. A club
  stage is the worst case for that — every camera in the room shares one bright band
  across the middle — so the frame is also compared as a coarse grid of block means,
  which no rearrangement of the same marginals can fake.
* **content** — what the light is. Total-variation distance between the two luma
  histograms, which is what separates a blackout, a lighting state change or a
  different part of the room from a reframe of the same one.
* **scale** — how big the subject is. The spread of the frame's luma mass, in frame
  fractions, compared as a ratio. A tight shot spreads its mass across the frame; a
  wide one concentrates it. Taken as a ratio of spreads rather than an absolute, so
  it reads the *change* in shot size and not the shot size.

Everything is measured on a small grey grid, which is deliberate. A cut is a
composition event; the arithmetic wants the picture's shape and its light, not its
detail, and a 128x72 grid is enough shape to correlate and cheap enough that a whole
song's cuts cost milliseconds. Frames arrive as raw grey from the same ffmpeg route
the occlusion scan uses.

What this cannot do: it does not know who is on screen. Two different cameras
pointed at the same soloist from twenty degrees apart score as a step here if the
backgrounds differ enough, and the true 30-degree rule would call that a jump. The
flag is therefore a *candidate* — a cut worth a human's eye — and it is calibrated
so the human's own deliverables sit clear of it (see ``JUMP_DELTA``).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray

GRID_WIDTH = 128
GRID_HEIGHT = 72
"""The grid a cut is read on, 16:9 — the same one the occlusion scan measures on, for
the same reason: it is the smallest picture that still has a composition. Every term
below is a fraction of the frame rather than a pixel count, so frames decoded on
another grid still read correctly; this is the size to prefer, not a requirement."""

HISTOGRAM_BINS = 32
"""Luma bins the content distance is taken over. Coarse enough that grain and a
half-stop of exposure drift do not read as a different picture."""

MAX_SHIFT = 0.125
"""How far the lag search slides one profile against the other, as a fraction of the
axis. An eighth of the frame covers a reframe, a small pan and the parallax between
two cameras a few feet apart — past that the pictures are not the same picture that
moved, and calling them matched would hide exactly the cuts worth seeing.

A best match found *at* the limit is treated as no match at all rather than as the
match it appears to be: the search ran out of room, which means the alignment it
wanted lies outside the reframe this term is about, and the two profiles are then
compared where they actually sit."""

BLOCK_COLS = 8
BLOCK_ROWS = 4
"""The coarse grid the structure term compares — 32 cells across the frame. Fine
enough that a subject moving from one side to the other lands in different cells,
coarse enough that grain, a head bobbing and a few pixels of reframe do not."""

SCALE_SPAN = 1.0
"""Doublings of subject spread that count as a full size change. One of them — the
subject taking up twice as much of the frame — is a wide against a medium, which is
already the size step the 30-degree rule accepts in place of an angle change."""

WEIGHT_LAYOUT = 0.30
WEIGHT_STRUCTURE = 0.25
WEIGHT_CONTENT = 0.30
WEIGHT_SCALE = 0.15
"""How the four terms make one number. The two composition terms carry the most
between them, split so neither can speak alone: layout forgives a slide and would
call a re-arranged frame matched, structure forgives nothing and would call a pan a
new picture. Content is weighted with them because a change of light or of room is a
real step even when the framing rhymes. Size is last because it is the rule's escape
clause rather than its subject."""

JUMP_DELTA = 0.20
"""Below this composite the cut is a jump-cut candidate.

Calibrated on the human deliverables — ``gauntlet/recon/cut_delta_calib.py``,
receipt in ``cut_delta_calib.json``, measured 2026-08-14 over the five Zinc Bar
songs. 200 cuts read through the same path a pack build uses: the smallest step
any of them makes is 0.44, the median 0.63, the largest 0.73. The threshold is
half that floor, rounded down to a 0.05 grid, and none of the 200 reaches it.

Half rather than at the floor, because that floor is the floor of *real angle
changes* — every cut a professional made between two cameras. A threshold sitting
on it would flag any cut that merely steps less than their smallest, which reports
the footage rather than the edit. A jump cut is not a small step; it is a step of
almost nothing, and the synthetic near-jump-cuts the fixture tier fixes score
0.01-0.05, an order below this line.

One deliverable, Soultrane, contributed none of the 200: it is cut with
multi-second dissolves throughout, and the per-frame scene detector that finds the
boundaries cannot see them — measured in ``soultrane_dissolves.json``, where the
picture steps 0.67 across three seconds while no frame pair exceeds 4.95 against a
noise floor of 1.5, and the detector finds nothing even at a scene threshold of
0.015 (#203). The threshold therefore rests on four hard-cut songs. Re-run the
calibration before moving it."""

LAYOUT_MATCH = 0.15
SCALE_MATCH = 0.10
"""Below these a flagged cut is named for what it failed to change — the angle, the
size, or both. Reporting only, never part of the flag itself."""

GUARD_FRAMES = 1
STACK_FRAMES = 3
"""Frames skipped either side of a boundary, and frames median-stacked once clear of
it. The guard exists because a cut detected at frame *n* is a cut somewhere in the
neighbourhood of frame *n* — a dissolve's blend, a compression-smeared boundary
frame or a one-frame detector error would otherwise be read as the incoming shot.
The stack exists because one frame of a handheld camera is a coin toss."""


class Delta(NamedTuple):
    """One cut, read across its boundary."""

    delta: float
    content: float
    layout: float
    structure: float
    scale: float
    shift_x: int | None
    shift_y: int | None
    """Where the lag search found this axis' best match, in grid pixels — or ``None``
    when its best score sat at the edge of the search, which means no alignment was
    found inside the distance a reframe covers, and the axis was scored where it sits.

    ``None`` is therefore the *ordinary* answer at a real cut, not an unusual one: two
    different angles are not each other slid over, so the correlation has no interior
    peak to find. In the calibration receipt 188 of the 200 human cuts report a null
    vertical shift and 72 a null horizontal one — the vertical axis pegs more often
    because a club frame's vertical profile is nearly the same band of stage light in
    every camera, so what little structure it has rarely lines up at a small offset. A
    *number* here is the interesting case: it says these two frames are one picture,
    moved, which is the signature of the cut this measurement exists to catch."""

    jump_cut: bool
    reason: str

    def as_record(self) -> dict[str, Any]:
        """The reading as plain JSON types, for a manifest or a cut record.

        The fields above *are* the record — every value is already a float, an int, a
        bool or a string, rounded at construction — so the written shape and the type
        cannot drift apart the way a hand-listed dict would.
        """
        return dict(self._asdict())


def read_pair(before: NDArray[Any], after: NDArray[Any]) -> Delta:
    """Read the step between the outgoing frame and the incoming one."""
    out = np.asarray(before, dtype=np.float64) / 255.0
    into = np.asarray(after, dtype=np.float64) / 255.0

    content = _content(out, into)
    layout, shift_x, shift_y = _layout(out, into)
    structure = _structure(out, into)
    scale = _scale(out, into)

    delta = (
        WEIGHT_LAYOUT * layout
        + WEIGHT_STRUCTURE * structure
        + WEIGHT_CONTENT * content
        + WEIGHT_SCALE * scale
    )
    jumped = delta < JUMP_DELTA
    return Delta(
        delta=round(delta, 4),
        content=round(content, 4),
        layout=round(layout, 4),
        structure=round(structure, 4),
        scale=round(scale, 4),
        shift_x=shift_x,
        shift_y=shift_y,
        jump_cut=jumped,
        reason=_reason(layout, structure, scale) if jumped else "",
    )


def read_boundary(window: NDArray[Any], index: int) -> Delta:
    """Read the cut at ``index`` — the first incoming frame — out of a decoded window.

    The guard either side is what a hard cut needs. A transition with a ramp knows
    where its own ends are, and should say so through ``read_across`` instead.
    """
    return read_across(window, index - GUARD_FRAMES, index + GUARD_FRAMES)


def read_across(window: NDArray[Any], out_index: int, in_index: int) -> Delta:
    """Read across a boundary whose ends are known: one past the outgoing shot's last
    frame, and the incoming shot's first.

    Both sides are median-stacked clear of the boundary, so a dissolve's blend frames
    and a one-frame detector error land outside the reading rather than inside it. A
    window without room for both stacks is a refusal: reading off a single frame would
    answer a different question and never say so.
    """
    if out_index - STACK_FRAMES < 0 or in_index + STACK_FRAMES > len(window):
        raise ValueError(
            f"A boundary from frame {out_index} to {in_index} needs {STACK_FRAMES} frames "
            f"either side inside a window of {len(window)}; decode a wider window or drop "
            f"the cut."
        )
    out = _stack(window[out_index - STACK_FRAMES : out_index])
    into = _stack(window[in_index : in_index + STACK_FRAMES])
    return read_pair(out, into)


def summarize(readings: Sequence[Delta], unread: int = 0) -> dict[str, Any]:
    """The run's own distribution: how big its steps are and how many are flagged.

    The flag count alone reads as a verdict; the quantiles are what say whether an edit
    lives near the threshold or clear of it. ``unread`` is the boundaries that could not
    be read at all, and it belongs in the same block as the counts it qualifies — a
    consumer that pastes it on afterwards writes half of this shape somewhere else.
    """
    values = np.asarray([one.delta for one in readings], dtype=np.float64)
    return {
        "cuts": len(readings),
        "cuts_unread": unread,
        "jump_cuts": sum(1 for one in readings if one.jump_cut),
        "delta": {
            "min": round(float(values.min()), 4),
            "p10": round(float(np.quantile(values, 0.10)), 4),
            "median": round(float(np.median(values)), 4),
            "mean": round(float(values.mean()), 4),
            "max": round(float(values.max()), 4),
        }
        if readings
        else None,
        "threshold": JUMP_DELTA,
    }


def _stack(frames: NDArray[Any]) -> NDArray[np.uint8]:
    """The median of a short run of frames — one picture, with the noise voted out."""
    return np.asarray(np.median(np.asarray(frames, dtype=np.float64), axis=0).astype(np.uint8))


def _content(out: NDArray[np.float64], into: NDArray[np.float64]) -> float:
    """Total-variation distance between the two luma histograms, 0 to 1."""
    edges = np.linspace(0.0, 1.0, HISTOGRAM_BINS + 1)
    left, _ = np.histogram(out, bins=edges)
    right, _ = np.histogram(into, bins=edges)
    a = left / max(1, left.sum())
    b = right / max(1, right.sum())
    return float(np.abs(a - b).sum()) / 2.0


def _layout(
    out: NDArray[np.float64], into: NDArray[np.float64]
) -> tuple[float, int | None, int | None]:
    """``(distance, shift_x, shift_y)`` — how much composition survives a lag search."""
    across, shift_x = _profile_peak(out.mean(axis=0), into.mean(axis=0))
    down, shift_y = _profile_peak(out.mean(axis=1), into.mean(axis=1))
    matched = 0.5 * (max(0.0, across) + max(0.0, down))
    return 1.0 - matched, shift_x, shift_y


def _profile_peak(
    out: NDArray[np.float64], into: NDArray[np.float64]
) -> tuple[float, int | None]:
    """Best normalised correlation of two profiles over the lag search, and its lag.

    A positive lag is the picture moving towards the start of the axis — left, or up.
    Two profiles with no variation at all match perfectly, which is the honest answer
    for two blank frames; one flat against one not is no match at all.

    A peak found at the edge of the search is refused. The search is bounded because a
    match only means something inside the distance a reframe covers; a best score at the
    boundary says the alignment it was climbing towards is further out than that, and
    crediting it would let two unrelated pictures that happen to correlate at maximum
    slide read as the same picture, nudged. Those are scored where they actually sit.
    """
    a = out - out.mean()
    b = into - into.mean()
    flat_a = bool(np.allclose(a, 0.0))
    flat_b = bool(np.allclose(b, 0.0))
    if flat_a or flat_b:
        return (1.0, 0) if flat_a and flat_b else (0.0, 0)

    size = a.size
    maxlag = max(1, int(round(size * MAX_SHIFT)))
    scores: dict[int, float] = {}
    for lag in range(-maxlag, maxlag + 1):
        left = a[lag:] if lag >= 0 else a[: size + lag]
        right = b[: size - lag] if lag >= 0 else b[-lag:]
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator == 0.0:
            continue
        scores[lag] = float(np.dot(left, right)) / denominator
    if not scores:
        return 0.0, None
    best_lag = max(scores, key=lambda lag: scores[lag])
    if abs(best_lag) == maxlag:
        return scores.get(0, 0.0), None
    return scores[best_lag], best_lag


def _structure(out: NDArray[np.float64], into: NDArray[np.float64]) -> float:
    """How differently the light is arranged, on a grid marginals cannot fake.

    Each frame becomes ``BLOCK_ROWS x BLOCK_COLS`` block means, and the two grids are
    correlated after their own mean and level are divided out — the arrangement is the
    question here, and how bright or contrasty the shot is belongs to ``content``.
    """
    left = _blocks(out)
    right = _blocks(into)
    flat_left = bool(np.allclose(left, 0.0))
    flat_right = bool(np.allclose(right, 0.0))
    if flat_left or flat_right:
        return 0.0 if flat_left and flat_right else 1.0
    matched = float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right)))
    return 1.0 - max(0.0, matched)


def _blocks(frame: NDArray[np.float64]) -> NDArray[np.float64]:
    """The frame as a flat vector of block means, with its own mean taken out."""
    rows, cols = frame.shape
    edges_y = np.linspace(0, rows, BLOCK_ROWS + 1).astype(int)
    edges_x = np.linspace(0, cols, BLOCK_COLS + 1).astype(int)
    means = np.asarray(
        [
            [
                float(frame[edges_y[y] : edges_y[y + 1], edges_x[x] : edges_x[x + 1]].mean())
                for x in range(BLOCK_COLS)
            ]
            for y in range(BLOCK_ROWS)
        ],
        dtype=np.float64,
    ).ravel()
    return np.asarray(means - means.mean())


def _scale(out: NDArray[np.float64], into: NDArray[np.float64]) -> float:
    """Change in how much of the frame the subject occupies, in doublings, clamped."""
    before = _spread(out)
    after = _spread(into)
    if before <= 0.0 or after <= 0.0:
        return 0.0
    return min(1.0, abs(float(np.log2(after / before))) / SCALE_SPAN)


def _spread(frame: NDArray[np.float64]) -> float:
    """How widely the frame's luma mass sits, as a fraction of the frame.

    Taken on both axes off the frame's own floor, so the ground the subject stands
    against drops out and only the lit part carries weight. The geometric mean of the
    two keeps one axis from speaking for the picture: a subject that fills the width
    and a third of the height is not the same size as one that fills both.
    """
    across = _profile_spread(frame.mean(axis=0))
    down = _profile_spread(frame.mean(axis=1))
    if across <= 0.0 or down <= 0.0:
        return 0.0
    return float(np.sqrt(across * down))


def _profile_spread(profile: NDArray[np.float64]) -> float:
    """Standard deviation of one profile's mass, in fractions of that axis."""
    mass = profile - profile.min()
    total = float(mass.sum())
    if total <= 0.0:
        return 0.0
    positions = np.arange(profile.size, dtype=np.float64) / float(profile.size)
    mean = float((mass * positions).sum()) / total
    variance = float((mass * (positions - mean) ** 2).sum()) / total
    return float(np.sqrt(max(0.0, variance)))


def _reason(layout: float, structure: float, scale: float) -> str:
    """What a flagged cut failed to change."""
    same_angle = layout < LAYOUT_MATCH and structure < LAYOUT_MATCH
    same_size = scale < SCALE_MATCH
    if same_angle and same_size:
        return "same angle and size"
    if same_angle:
        return "same angle"
    if same_size:
        return "same size"
    return "step below threshold"
