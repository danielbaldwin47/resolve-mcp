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

That heuristic finds every real blocking the gauntlet has seen and a great many things that
are not one — two of six windows were real on the round it was tuned against (#172). What the
score cannot say is the thing that separates them, and the evidence set is unambiguous about
what that is: **a true blocking covers a player the shot is framed on**. Motion does not
separate the classes (the drummer's arm moves and is not an obstruction) and neither does the
score (a real body crossing the near field read 0.416 while a drummer read 0.472). So a second
reading sits beside the first, and it is the one the *windows* are classed on:

* **novel** — how much of the blob stands where this shot is usually clear. Every pixel's
  share of samples it is near-field in is the run's ``occupancy``; a blob over a region that
  is dark and flat in most frames is the shot's own furniture — a piano lid, a head parked in
  a corner, the drummer at the edge of his own four-shot — and it hides nothing, because it
  was never not there. This is persistence, not stillness: the drummer's arm sweeps, and the
  region it sweeps is occupied all night either way.
* **hidden** — how much of what the shot normally shows at that spot is gone. The run's own
  per-pixel median is what it looks like unobstructed; a blob sitting where the median is
  brighter is covering something, and on this stage what is on the stage is players.

Either one clearing its level makes the sample an obstruction, because they measure the same
claim from two sides and a body that only one of them catches is still a body. On the whole
adjudicated evidence set — three true blockings, eleven false positives across two angles and
three pieces — the worst false positive reads 0.018 novel and 0.007 hidden against a weakest
true blocking of 0.066 and 0.049.

Both readings are *spatial*, and that is why the subject labelling of #181 is not what answers
this. ``analysis/subject`` says which player an angle is framed on by name, off the sidecar and
the solo map; it holds no idea of where in the frame that player is, so it cannot say whether a
blob is in front of one. What the run's own occupancy and median give instead is the same claim
measured where it happens: the stage is what this shot shows at that spot when nothing is in
the way, and on this stage what is on the stage is players.

**What this does not measure**, because it was tried and it does not separate: the detector
goes blind to a body once it stops moving, and nothing here recovers it. In the one adjudicated
crossing the sax player stands over the pianist for a second and a half after the last sample
that scores, and those frames are indistinguishable at this grid from the clean frames after he
turns side-on. An obstruction window therefore spans the samples that *scored*, not the body's
own in and out, and the scan says so in its result rather than implying a precision it has not
got. A separate class for the mid-take reframe was tried too — the settle test and the global
drift both overlap the true blockings — so a reframe is reported as what it measurably is,
scene rather than obstruction, on the one example the evidence set holds.

**And both readings are only as wide as the range asked about**, because the occupancy and the
median are the scanned range's own. Scan a stretch a body dominates and that body becomes the
shot's furniture — the same shape as the failure the ending ledger named, where a baseline taken
over a stretch the piano lid filled produced five false windows in the piece next door. Scanning
per song, which is what the tool is for, keeps a crossing a small share of its range.

Numbers are the frame's own: coverage is a fraction of frame area, luma is 0 to 1. scipy
does the labelling and the box filter — it is already a dependency for the loudness filter,
and hand-rolling connected components in Python would be slower and less correct.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

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

Verdict = Literal["obstruction", "scene"]
"""What a blocked sample or window is. ``obstruction`` is the veto — something is between the
lens and a player. ``scene`` is everything the near-field heuristic finds that the shot was
framed to include: furniture, a parked head, the shot's own foreground subject, and a mid-take
reframe carrying the furniture to a new place in frame."""

OBSTRUCTION: Verdict = "obstruction"
SCENE: Verdict = "scene"
"""The two verdicts by name, for the callers that compare against them."""

OCCUPANCY_LEVEL = 0.25
"""Share of the run's samples a pixel has to be near-field in before that spot counts as part
of the shot rather than as clear stage. A quarter is deliberately low: the failure to avoid is
calling a real body 'furniture', and a body that is in a quarter of a song's frames is furniture
by any reading a builder cares about."""

NOVEL_AREA = 0.035
"""Frame fraction of a blob standing where the shot is usually clear, at or above which the
sample is an obstruction. Between the weakest true blocking in the evidence set (0.066) and the
worst false positive (0.018)."""

HIDE_LEVEL = 0.05
"""How much darker than the run's own median a pixel has to be to count as covering something.
In luma units, so it survives the lighting moving under it."""

HIDDEN_AREA = 0.025
"""Frame fraction of a blob covering what the median shows, at or above which the sample is an
obstruction. Between the weakest true blocking (0.049) and the worst false positive (0.007)."""

REFERENCE_SAMPLES = 240
"""How many samples the per-pixel median is taken over, spread evenly across the run. A median
is a median: 240 frames describe an unobstructed shot as well as 14400 do, and taking it over
every sample of the longest scan this tool allows would cost a gigabyte to say the same thing."""


class Reading(NamedTuple):
    """One sampled frame, scored."""

    coverage: float
    largest: float
    blobs: int
    score: float
    novel: float
    hidden: float


class Scan(NamedTuple):
    """Every sample of a range, and the baseline they were scored against."""

    readings: tuple[Reading, ...]
    baseline: float


def read_grid(data: bytes, width: int = GRID_WIDTH, height: int = GRID_HEIGHT) -> NDArray[np.uint8]:
    """Raw 8-bit grey bytes as ``(samples, height, width)``, or ``ValueError``.

    A trailing partial frame fails the read rather than being dropped. ffmpeg killed mid-write
    leaves one, and so does a second scan writing over this one's scratch file; either way the
    frames that survived cover a shorter range than the one asked about, and scoring them
    would answer for footage nobody decoded. The answer that gets invented is the dangerous
    direction — a truncated tail reads as a clear stretch, so the cut goes where the blocker
    was.

    The refusal is about the buffer, so it is a ``ValueError`` about the buffer: this module is
    grey bytes in and scores out, and knows nothing of the job that produced the bytes. Whoever
    ran the decode catches it and says what to do about it — for the scan, ``occlusion``.
    """
    frame_bytes = width * height
    remainder = len(data) % frame_bytes
    if remainder:
        raise ValueError(
            f"{len(data)} bytes of grey is {remainder} past a whole number of "
            f"{width}x{height} frames"
        )
    return np.asarray(np.frombuffer(data, dtype=np.uint8).reshape(-1, height, width))


def measure(frames: NDArray[np.uint8]) -> Scan:
    """Score every frame in ``frames``: the geometry, then the run's own baseline and reference.

    Two passes, because both readings that separate an obstruction from the shot's own
    furniture are against the whole run and neither is known while the first frame is being
    looked at. The second pass labels each frame again rather than keeping the first pass's
    masks, and that is the deliberate end of the trade: a scan of an hour at four samples a
    second is 130 MB of grey and a mask per sample would add as much again, while the labelling
    is milliseconds against a 4K decode that is minutes.
    """
    covers: list[tuple[float, float, int]] = []
    # Occupancy counts the raw candidate pixels, not the ones that survived the size and
    # anchor filters: the question it answers is whether this *spot* is near-field most of the
    # time, and a lid that only qualifies as a blob in the frames where a head joins it is
    # furniture in all of them.
    occupied = np.zeros(frames.shape[1:], dtype=np.int32)
    for one in frames:
        candidate = _candidate(_luma(one))
        occupied += candidate
        _, weighted, largest, blobs = _qualifying(candidate)
        covers.append((weighted, largest, blobs))

    occupancy = occupied / max(1, len(frames))
    reference = _reference(frames)
    signals = [_discriminate(one, occupancy, reference) for one in frames]
    return score(covers, signals)


def verdict(novel: float, hidden: float) -> Verdict:
    """``OBSTRUCTION`` or ``SCENE`` for one sample's discriminator readings.

    Either signal clearing its level is enough. They measure the same claim — this mass is
    covering a player rather than being part of the shot — from two sides, and requiring both
    would throw away a body that only one of them catches.
    """
    return OBSTRUCTION if novel >= NOVEL_AREA or hidden >= HIDDEN_AREA else SCENE


def score(covers: list[tuple[float, float, int]], signals: list[tuple[float, float]]) -> Scan:
    """Turn per-frame geometry into per-sample scores, against the run's own baseline.

    Separated from ``coverage`` because it is the one part that cannot be decided frame by
    frame: what counts as an obstruction depends on what this shot looks like unobstructed.
    """
    areas = [one[0] for one in covers]
    baseline = _baseline(areas)
    readings: list[Reading] = []
    for index, ((area, largest, blobs), (novel, hidden)) in enumerate(
        zip(covers, signals, strict=True)
    ):
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
                novel=round(novel, 4),
                hidden=round(hidden, 4),
            )
        )
    return Scan(tuple(readings), round(baseline, 4))


def _luma(frame: NDArray[np.uint8]) -> NDArray[np.float32]:
    """One grey frame as luma, 0 to 1."""
    return np.asarray(frame, dtype=np.float32) / 255.0


def _qualifying(
    candidate: NDArray[np.bool_],
) -> tuple[NDArray[np.bool_], float, float, int]:
    """The candidate pixels that are a body: labelled, size-filtered and anchor-filtered.

    Comes back as the mask of everything that qualified plus ``(coverage, largest, blobs)`` —
    the weighted area fraction of every qualifying blob, the plain area fraction of the biggest
    one, and how many qualified. Both callers want one of the two halves and neither wants to
    walk the labels twice inside its own pass.
    """
    labels, found = ndimage.label(candidate)
    keep = np.zeros_like(candidate)
    if not found:
        return keep, 0.0, 0.0, 0

    height, width = candidate.shape
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
        keep |= labels == label
        blobs += 1
        largest = max(largest, fraction)
        weighted += fraction * (LOWER_BIAS if low_share > 0.5 else 1.0)
    return keep, min(weighted, 1.0), largest, blobs


def _reference(frames: NDArray[np.uint8]) -> NDArray[np.float32]:
    """What this shot looks like when nothing is in the way: the per-pixel median.

    Median rather than mean because a blocker is an outlier and the point of the reading is to
    have something to be an outlier against; taken over an even spread of at most
    ``REFERENCE_SAMPLES`` frames because ``np.median`` promotes its input to float64 and the
    longest scan this tool allows would not fit.
    """
    if len(frames) <= REFERENCE_SAMPLES:
        chosen = frames
    else:
        chosen = frames[np.linspace(0, len(frames) - 1, REFERENCE_SAMPLES, dtype=np.intp)]
    return np.asarray(np.median(chosen, axis=0), dtype=np.float32) / 255.0


def _discriminate(
    frame: NDArray[np.uint8],
    occupancy: NDArray[np.float64],
    reference: NDArray[np.float32],
) -> tuple[float, float]:
    """``(novel, hidden)`` for one frame — is this mass covering a player, or is it the shot?

    ``novel`` is the share of the frame taken by body pixels standing where the run is usually
    clear; ``hidden`` the share taken by body pixels darker than what the run normally shows
    there. Both are fractions of frame area, so they read against ``coverage`` directly.
    """
    luma = _luma(frame)
    keep, _, _, blobs = _qualifying(_candidate(luma))
    if not blobs:
        return 0.0, 0.0
    area = float(luma.size)
    novel = float(np.count_nonzero(keep & (occupancy < OCCUPANCY_LEVEL))) / area
    hidden = float(np.count_nonzero(keep & ((reference - luma) > HIDE_LEVEL))) / area
    return novel, hidden


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
