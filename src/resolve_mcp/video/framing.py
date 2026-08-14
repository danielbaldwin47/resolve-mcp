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

Three terms, each a fraction, each measuring a different way two frames can differ:

* **layout** — where the light is. The frame's row and column profiles are
  cross-correlated over a small lag, so a picture that merely slid sideways still
  matches itself. What survives a lag search is composition: a real angle change
  moves the subject relative to the background and no shift undoes it. The lag
  itself is reported, because *matched at a shift of three pixels* is the exact
  signature of the cut this measurement exists to catch.
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
moved, and calling them matched would hide exactly the cuts worth seeing."""

SCALE_SPAN = 2.0
"""Doublings of subject spread that count as a full size change. Two of them — a
four-fold change in how much of the frame the subject occupies — is a wide against a
close-up, which is as large a size step as a concert cut makes."""

WEIGHT_LAYOUT = 0.5
WEIGHT_CONTENT = 0.3
WEIGHT_SCALE = 0.2
"""How the three terms make one number. Layout leads because composition is what the
eye reads across a cut; content follows because a change of light or of room is a
real step even when the framing rhymes; size is last because it is the rule's
escape clause rather than its subject."""

JUMP_DELTA = 0.20
"""Below this composite the cut is a jump-cut candidate.

Calibrated on the five human deliverables (``gauntlet/recon/cut_delta_calib.py``,
receipt in ``cut_delta_calib.json``): the threshold sits under the human's own
cuts, so their edits raise no flags, and above the synthetic near-jump-cut this
tier fixes. Re-run the calibration before moving it."""

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
    scale: float
    shift_x: int
    shift_y: int
    jump_cut: bool
    reason: str

    def as_record(self) -> dict[str, Any]:
        """The reading as plain JSON types, for a manifest or a cut record."""
        return {
            "delta": self.delta,
            "content": self.content,
            "layout": self.layout,
            "scale": self.scale,
            "shift_x": self.shift_x,
            "shift_y": self.shift_y,
            "jump_cut": self.jump_cut,
            "reason": self.reason,
        }


def read_pair(before: NDArray[Any], after: NDArray[Any]) -> Delta:
    """Read the step between the outgoing frame and the incoming one."""
    out = np.asarray(before, dtype=np.float64) / 255.0
    into = np.asarray(after, dtype=np.float64) / 255.0

    content = _content(out, into)
    layout, shift_x, shift_y = _layout(out, into)
    scale = _scale(out, into)

    delta = WEIGHT_LAYOUT * layout + WEIGHT_CONTENT * content + WEIGHT_SCALE * scale
    jumped = delta < JUMP_DELTA
    return Delta(
        delta=round(delta, 4),
        content=round(content, 4),
        layout=round(layout, 4),
        scale=round(scale, 4),
        shift_x=shift_x,
        shift_y=shift_y,
        jump_cut=jumped,
        reason=_reason(layout, scale) if jumped else "",
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


def summarize(readings: list[Delta]) -> dict[str, Any]:
    """The run's own distribution: how big its steps are and how many are flagged.

    The flag count alone reads as a verdict; the quantiles are what say whether an
    edit lives near the threshold or clear of it.
    """
    if not readings:
        return {"cuts": 0, "jump_cuts": 0, "delta": {}}
    values = np.asarray([one.delta for one in readings], dtype=np.float64)
    return {
        "cuts": len(readings),
        "jump_cuts": sum(1 for one in readings if one.jump_cut),
        "delta": {
            "min": round(float(values.min()), 4),
            "p10": round(float(np.quantile(values, 0.10)), 4),
            "median": round(float(np.median(values)), 4),
            "mean": round(float(values.mean()), 4),
            "max": round(float(values.max()), 4),
        },
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


def _layout(out: NDArray[np.float64], into: NDArray[np.float64]) -> tuple[float, int, int]:
    """``(distance, shift_x, shift_y)`` — how much composition survives a lag search."""
    across, shift_x = _profile_peak(out.mean(axis=0), into.mean(axis=0))
    down, shift_y = _profile_peak(out.mean(axis=1), into.mean(axis=1))
    matched = 0.5 * (max(0.0, across) + max(0.0, down))
    return 1.0 - matched, shift_x, shift_y


def _profile_peak(out: NDArray[np.float64], into: NDArray[np.float64]) -> tuple[float, int]:
    """Best normalised correlation of two profiles over the lag search, and its lag.

    A positive lag is the picture moving towards the start of the axis — left, or up.
    Two profiles with no variation at all match perfectly, which is the honest answer
    for two blank frames; one flat against one not is no match at all.
    """
    a = out - out.mean()
    b = into - into.mean()
    flat_a = bool(np.allclose(a, 0.0))
    flat_b = bool(np.allclose(b, 0.0))
    if flat_a or flat_b:
        return (1.0, 0) if flat_a and flat_b else (0.0, 0)

    size = a.size
    maxlag = max(1, int(round(size * MAX_SHIFT)))
    best, best_lag = -1.0, 0
    for lag in range(-maxlag, maxlag + 1):
        left = a[lag:] if lag >= 0 else a[: size + lag]
        right = b[: size - lag] if lag >= 0 else b[-lag:]
        denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
        if denominator == 0.0:
            continue
        score = float(np.dot(left, right)) / denominator
        if score > best:
            best, best_lag = score, lag
    return best, best_lag


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


def _reason(layout: float, scale: float) -> str:
    """What a flagged cut failed to change."""
    if layout < LAYOUT_MATCH and scale < SCALE_MATCH:
        return "same angle and size"
    if layout < LAYOUT_MATCH:
        return "same angle"
    if scale < SCALE_MATCH:
        return "same size"
    return "step below threshold"
