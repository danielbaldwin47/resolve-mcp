"""How blocked a frame is: the arithmetic behind the occlusion job.

The question this answers is the one that cost an edit its verdict — *is something in the
near field standing between the camera and the stage?* At a club that something is an
audience head, a hat, a raised phone or a shoulder: a metre from the lens, so it is far
outside the focal plane and carries no texture, and lit by nothing, so it is far darker than
the stage it covers. It runs off the bottom or the side of the frame, because a body does,
and it moves independently of the camera, so it arrives and leaves while the shot holds.

That is the whole heuristic, and every part of it is a guard against a different false
positive:

* **Dark or flat, not dark alone.** A club's back wall is as black as a head. The pixels
  that count are the ones that are below the frame's own midpoint *and* textureless after a
  local-detail pass — a busy dark background survives, a soft silhouette does not.
* **One contiguous blob, not a scatter.** A head is a region; grain and shadowed detail are
  speckle. Components are labelled and the small ones are dropped.
* **Anchored low or to the side.** A blob has to run off the bottom edge, or off a side
  edge with its mass in the lower half. This is what keeps a black ceiling — which touches
  the top edge of every frame in the room — from reading as an obstruction.
* **Above this shot's own baseline.** A locked-off camera with a dark table in the bottom
  corner would otherwise report every frame as blocked. The quiet end of the scan's own
  coverage is treated as scene rather than obstruction, up to a cap — past the cap a truly
  blocked range still scores, because "all of it is blocked" is a real answer.
* **Worse when it wipes through.** An obstruction that appears mid-shot is the one that
  ruins a cut, so a rise in coverage lifts the score of a sample that is already blocked. It
  can never lift a clean one.

Numbers are the frame's own: coverage is a fraction of frame area, luma is 0 to 1. scipy
does the labelling and the box filter — it is already a dependency for the loudness filter,
and hand-rolling connected components in Python would be slower and less correct.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

GRID_WIDTH = 128
GRID_HEIGHT = 72
"""The grid every sample is measured on, 16:9. Small enough that a scan of a whole song is
megabytes of raw grey and milliseconds of arithmetic; large enough that a head is hundreds of
pixels rather than a dozen. Sources of other shapes are squeezed onto it: an area fraction
does not care about aspect, and a near-field blocker is a fraction of the frame either way."""

DETAIL_WINDOW = 5
"""Side of the box the local-detail average is taken over, in grid pixels. Wide enough that
one hard edge inside an otherwise soft region does not rescue it from reading as flat."""

FLAT_LEVEL = 0.02
"""Mean absolute gradient, in luma units, below which a neighbourhood carries no texture."""

DARK_FRACTION = 0.55
DARK_FLOOR = 0.05
DARK_CEILING = 0.30
"""A pixel is dark when it is below ``DARK_FRACTION`` of the frame's median luma, clamped
into [floor, ceiling]. Relative because a concert frame's midpoint moves with the lighting;
clamped because a blackout would otherwise make the whole frame 'not dark' and a white-out
would make all of it dark."""

MIN_BLOB_AREA = 0.015
"""Fraction of the frame a component has to reach before it is a body rather than speckle."""

LOWER_BIAS = 1.25
"""How much more a blob whose mass sits in the lower half counts. Heads and shoulders sit
low; something anchored high is more often set dressing."""

SATURATION_AREA = 0.30
"""Coverage above the baseline that scores 1.0. Nearly a third of the frame gone to a
foreground body is as unusable as a shot gets."""

BASELINE_QUANTILE = 0.2
BASELINE_CAP = 0.10
"""The scan's own quiet coverage is subtracted as scene, but never more than the cap — so a
range that is blocked end to end still reads as blocked."""

WIPE_DELTA = 0.06
WIPE_BONUS = 0.15
"""A rise of ``WIPE_DELTA`` in coverage between neighbouring samples earns the full bonus,
and only on a sample that is already blocked."""


class Reading(NamedTuple):
    """One sampled frame, scored."""

    coverage: float
    largest: float
    blobs: int
    score: float


class Scan(NamedTuple):
    """Every sample of a range, and the baseline they were scored against."""

    readings: tuple[Reading, ...]
    baseline: float


def read_grid(data: bytes, width: int = GRID_WIDTH, height: int = GRID_HEIGHT) -> NDArray[np.uint8]:
    """Raw 8-bit grey bytes as ``(samples, height, width)``.

    A trailing partial frame is dropped rather than reshaped into nonsense: ffmpeg killed
    mid-write leaves one, and half a frame is not a sample.
    """
    frame_bytes = width * height
    raw = np.frombuffer(data, dtype=np.uint8)
    usable = raw.size - raw.size % frame_bytes
    return np.asarray(raw[:usable].reshape(-1, height, width))


def measure(frames: NDArray[np.uint8]) -> Scan:
    """Score every frame in ``frames``: coverage per frame, then the run's own baseline."""
    covers = [coverage(one) for one in frames]
    return score(covers)


def coverage(frame: NDArray[np.uint8]) -> tuple[float, float, int]:
    """``(coverage, largest, blobs)`` for one frame — the geometry, before any baseline.

    ``coverage`` is the weighted area fraction of every qualifying blob, ``largest`` the
    plain area fraction of the biggest one, ``blobs`` how many qualified.
    """
    luma = np.asarray(frame, dtype=np.float32) / 255.0
    candidate = _candidate(luma)
    labels, found = ndimage.label(candidate)
    if not found:
        return 0.0, 0.0, 0

    height, width = luma.shape
    area = float(height * width)
    sizes = np.bincount(labels.ravel(), minlength=found + 1)
    lower = np.bincount(labels[height // 2 :].ravel(), minlength=found + 1)
    bottom = _touching(labels[-1:])
    sides = _touching(labels[:, :1]) | _touching(labels[:, -1:])

    weighted = 0.0
    largest = 0.0
    blobs = 0
    for label in range(1, found + 1):
        fraction = float(sizes[label]) / area
        if fraction < MIN_BLOB_AREA:
            continue
        low_share = float(lower[label]) / float(sizes[label])
        if not _anchored(label in bottom, label in sides, low_share):
            continue
        blobs += 1
        largest = max(largest, fraction)
        weighted += fraction * (LOWER_BIAS if low_share > 0.5 else 1.0)
    return min(weighted, 1.0), largest, blobs


def score(covers: list[tuple[float, float, int]]) -> Scan:
    """Turn per-frame geometry into per-sample scores, against the run's own baseline.

    Separated from ``coverage`` because it is the one part that cannot be decided frame by
    frame: what counts as an obstruction depends on what this shot looks like unobstructed.
    """
    areas = [one[0] for one in covers]
    baseline = _baseline(areas)
    readings: list[Reading] = []
    for index, (area, largest, blobs) in enumerate(covers):
        excess = max(0.0, area - baseline)
        base = min(1.0, excess / SATURATION_AREA)
        rise = area - areas[index - 1] if index else 0.0
        bonus = WIPE_BONUS * min(1.0, max(0.0, rise) / WIPE_DELTA) if base > 0.0 else 0.0
        readings.append(
            Reading(
                coverage=round(area, 4),
                largest=round(largest, 4),
                blobs=blobs,
                score=round(min(1.0, base + bonus), 3),
            )
        )
    return Scan(tuple(readings), round(baseline, 4))


def _candidate(luma: NDArray[np.float32]) -> NDArray[np.bool_]:
    """The pixels that could be near-field: dark, or textureless and below the midpoint.

    Opened by one pixel afterwards, which is what separates a blob from a scatter of grain.
    """
    median = float(np.median(luma))
    dark_cut = min(DARK_CEILING, max(DARK_FLOOR, DARK_FRACTION * median))
    dark = luma < dark_cut
    flat = _detail(luma) < FLAT_LEVEL
    mask = dark | (flat & (luma < median))
    return np.asarray(ndimage.binary_opening(mask, structure=np.ones((3, 3), dtype=bool)))


def _detail(luma: NDArray[np.float32]) -> NDArray[np.float32]:
    """Local texture: absolute gradient, averaged over a box so one edge is not a texture."""
    across = np.abs(np.diff(luma, axis=1, prepend=luma[:, :1]))
    down = np.abs(np.diff(luma, axis=0, prepend=luma[:1, :]))
    return np.asarray(ndimage.uniform_filter(across + down, size=DETAIL_WINDOW))


def _anchored(bottom: bool, side: bool, low_share: float) -> bool:
    """Whether a blob is where a body would be: off the bottom, or off a side and sitting low.

    The asymmetry is the point. Anything touching only the top edge is the room, not a
    person — a black ceiling touches the top of every frame shot in a basement club.
    """
    return bottom or (side and low_share > 0.5)


def _touching(edge: NDArray[np.int32]) -> set[int]:
    """The labels present on an edge of the frame, background excluded."""
    return {int(one) for one in np.unique(edge) if one}


def _baseline(areas: list[float]) -> float:
    """What this shot covers even when nothing is in the way, capped."""
    if not areas:
        return 0.0
    quiet = float(np.quantile(np.asarray(areas, dtype=np.float64), BASELINE_QUANTILE))
    return min(quiet, BASELINE_CAP)
