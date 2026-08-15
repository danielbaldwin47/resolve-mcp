"""How good one frame looks: the image-quality arithmetic behind the quality scan.

The gap this closes is the other half of the one ``blocking`` closes. That module says
whether something is standing in front of the camera; this one says whether the picture the
camera got is worth cutting to at all — soft, blown out, or shaky. A builder can see all
three on a contact sheet and none of them by measurement, so a soft take gets vetoed by eye
or not at all, and "not at all" is what happens at three in the morning on the twelfth song.

Four readings, and each is a different failure:

* **Sharpness** — is this take in focus? Not the frame's gradient energy, which says more
  about the subject than the lens: a busy crowd shot out-gradients a clean close-up. What is
  read instead is *acutance*, the ratio of the frame's own detail to the detail left after a
  small box blur. A frame the blur cannot soften further was already soft; a crisp one loses
  most of its gradient to it. That ratio is close to content-free, which is what lets one
  number rank two takes of different subjects.
* **Exposure** — mean luma, with contrast beside it. Both, because a frame can sit at a
  perfect midpoint and be flat, and a shot lit only by a wash reads exactly that way.
* **Clipped highlights** — the fraction of the frame at or above the top of the range, where
  a stage light has burned through and no grade will bring anything back. ``crushed`` is the
  same reading at the bottom of the range, because underexposure is the failure the same
  builder makes on the same night with the same veto.
* **Stability** — the one that needs two frames. Handheld wobble is invisible in a still and
  ruinous in motion, and it is *not* the same thing as camera movement: a slow developing pan
  is a deliberate shot and a locked-off camera someone leaned on is not. So the global shift
  between neighbouring frames is estimated by phase correlation, and what is scored is the
  **residual** — how far each shift sits from the trend of its neighbours. A steady pan has a
  steady shift and no residual; a wobble has a shift that changes sign, and all of it is
  residual.

Two guards keep stability honest, because both failures invent shake that is not there. A
frame pair straddling a **cut** is a 100%-different picture, and a correlator handed one
answers with a large, meaningless shift; a pair of **flat** frames (a black hold, a whiteout)
has nothing to align at all. Both come back as ``discontinuity`` with no stability score,
never as zero: "unmeasurable here" and "this camera is shaking" are opposite facts, and
scoring the first as the second would veto every shot after a cut.

Numbers are the frame's own — luma 0 to 1, fractions of frame area, shifts in grid pixels.
Pure: no I/O, no decode. ``quality`` owns the range, the sampling and the windows.
"""

from __future__ import annotations

import statistics
from typing import Any, NamedTuple

import numpy as np
from numpy.typing import NDArray
from scipy import ndimage

from ..errors import QualityScanError

GRID_WIDTH = 320
GRID_HEIGHT = 180
"""The grid every sample is measured on, 16:9 — wider than the occlusion grid on purpose.
Occlusion asks what fraction of the frame a body covers, which survives any downscale;
sharpness asks about detail, and a scan that threw the detail away before measuring it would
report every take as equally soft. 320 wide is a 6x reduction of a 1080 master: a focus miss
of a few pixels there is still a pixel here, while a whole song stays tens of megabytes."""

BLUR_WINDOW = 3
"""Side of the box the acutance comparison blurs with, in grid pixels. Small on purpose — it
has to remove the finest detail and leave everything coarser, so that what separates the two
readings is the scale a lens misses focus at."""

SHARP_FLOOR = 1.0
SHARP_SPAN = 0.9
"""Acutance of a frame the blur cannot soften at all is 1.0 — it is already that soft — and
``SHARP_FLOOR + SHARP_SPAN`` is the acutance that scores a full 1.0. Calibrated against the
corpus: `docs/reference/image-quality-calibration.md`."""

CLIP_LEVEL = 250.0 / 255.0
"""At or above this, a pixel is blown: nothing in the highlight survives to be graded back."""

CRUSH_LEVEL = 6.0 / 255.0
"""At or below this, a pixel is crushed — the same failure at the other end of the range."""

FLAT_CONTRAST = 0.01
"""Luma standard deviation below which a frame carries nothing to align against. A black
hold, a whiteout, a strobe frame: the correlator would answer, and the answer would be noise."""

PEAK_FLOOR = 0.05
"""Normalised phase-correlation peak below which two frames are not the same picture at all.

Measured, not guessed, and the measurement is why it is not lower: across the five delivered
songs, pairs *inside* a shot correlate at 0.29 or better at the 1st percentile, while pairs
across a cut cluster below 0.03 — and the sweep between 0.03 and 0.12 is perfectly flat,
because nothing at all lies in that gap (`gauntlet/recon/quality_cut_guard.json`). This sits
in the middle of it. The 0.01 it started at caught 1.5% of the cut pairs where 0.03 catches
all of them, and the ones it let through are the expensive kind: a cut scored as a lurch
poisons the trend its neighbours are judged against, which put six samples of a locked-off
delivered shot at zero stability (#182)."""

CUT_SHIFT = 0.04
"""Fraction of frame width a single-pair shift may reach before it is read as a different
picture rather than a move. Above the largest step any in-shot pair of the corpus actually
took (3.1% of frame width, at four samples a second), rounded up to the next grid line: past
that it is a whip pan at best and a cut at worst, and either way there is no wobble to
measure across it. Belt and braces behind the peak floor, which is what catches a cut whose
two sides happen to sit in the same place."""

SMOOTH_WINDOW = 5
"""How many neighbouring shifts the trend is taken over, samples. Wide enough that a steady
pan is a trend, narrow enough that a sway lasting a second is still a departure from one."""

SHAKE_SPAN = 0.02
"""Residual, as a fraction of frame width, that scores a full zero for stability. Two percent
of the frame of unpredicted movement between neighbouring samples is unusable handheld."""


class Frame(NamedTuple):
    """One frame, on its own — everything that needs no neighbour."""

    sharpness: float
    exposure: float
    contrast: float
    clipped: float
    crushed: float


class Move(NamedTuple):
    """The global shift between two neighbouring frames, and whether it means anything."""

    dx: float
    dy: float
    peak: float
    discontinuity: bool


class Reading(NamedTuple):
    """One sampled frame, fully scored.

    ``stability`` is ``None`` rather than 0.0 wherever it could not be measured — the first
    frame of a scan, a pair across a cut, a pair of flat frames.
    """

    sharpness: float
    exposure: float
    contrast: float
    clipped: float
    crushed: float
    stability: float | None
    discontinuity: bool


class Floors(NamedTuple):
    """What a usable picture has to clear. Every field is one veto a builder can set."""

    sharpness: float
    clipped: float
    stability: float


class Scan(NamedTuple):
    """Every sample of a range, scored."""

    readings: tuple[Reading, ...]


def read_grid(data: bytes, width: int = GRID_WIDTH, height: int = GRID_HEIGHT) -> NDArray[np.uint8]:
    """Raw 8-bit grey bytes as ``(samples, height, width)``, or a refusal.

    A trailing partial frame fails the read rather than being dropped, for the reason it does
    in ``blocking``: the frames that survived a killed decode cover a shorter range than the
    one asked about, and the invented answer runs the dangerous way — a truncated tail reads
    as footage nobody has any complaint about.
    """
    frame_bytes = width * height
    remainder = len(data) % frame_bytes
    if remainder:
        raise QualityScanError(
            cause=(
                f"The sampled grey is {len(data)} bytes — {remainder} past a whole number of "
                f"{width}x{height} frames, so the decode did not finish."
            ),
            fix=(
                "Run the scan again. Half a frame is what a decode killed mid-write leaves "
                "behind; scoring the frames that survived would report on a shorter range "
                "than the one asked about, and report it as clean."
            ),
            detail={"bytes": len(data), "frame_bytes": frame_bytes, "remainder": remainder},
        )
    return np.asarray(np.frombuffer(data, dtype=np.uint8).reshape(-1, height, width))


def measure(frames: NDArray[np.uint8]) -> Scan:
    """Score every frame: the four still readings, then stability across the neighbours.

    One pass, holding two frames' worth of floats at a time rather than the whole scan's.
    A song-length range is a hundred megabytes of 8-bit grey and eight times that as doubles,
    and the arithmetic never needs more than a frame and its neighbour.
    """
    looks: list[Frame] = []
    moves: list[Move] = []
    previous: NDArray[np.float64] | None = None
    for one in frames:
        luma = np.asarray(one, dtype=np.float64) / 255.0
        looks.append(look(luma))
        if previous is not None:
            moves.append(travel(previous, luma))
        previous = luma
    scores = stability(moves, int(frames.shape[2]))
    readings = [
        Reading(
            sharpness=frame.sharpness,
            exposure=frame.exposure,
            contrast=frame.contrast,
            clipped=frame.clipped,
            crushed=frame.crushed,
            # The nth frame is scored on the move that arrived at it, so the first has none.
            stability=None if index == 0 else scores[index - 1],
            discontinuity=index > 0 and moves[index - 1].discontinuity,
        )
        for index, frame in enumerate(looks)
    ]
    return Scan(tuple(readings))


def look(luma: NDArray[np.float64]) -> Frame:
    """The four readings one frame answers for by itself. ``luma`` is 0 to 1."""
    return Frame(
        sharpness=round(_sharpness(luma), 4),
        exposure=round(float(np.mean(luma)), 4),
        contrast=round(float(np.std(luma)), 4),
        clipped=round(float(np.mean(luma >= CLIP_LEVEL)), 4),
        crushed=round(float(np.mean(luma <= CRUSH_LEVEL)), 4),
    )


def travel(before: NDArray[np.float64], after: NDArray[np.float64]) -> Move:
    """How far the picture moved between two frames, and whether that reading means anything.

    Phase correlation rather than a lag search over projections: the whole frame votes, so a
    stage light pulsing in one corner does not drag the estimate the way a column profile
    lets it. The peak height is kept because it is the tell that the two frames are not the
    same picture — across a cut it collapses, and that is the one case where a large shift
    has to be thrown away rather than reported as a lurch.
    """
    height, width = before.shape
    if float(np.std(before)) < FLAT_CONTRAST or float(np.std(after)) < FLAT_CONTRAST:
        return Move(0.0, 0.0, 0.0, True)

    spectrum = np.fft.rfft2(before - float(np.mean(before))) * np.conj(
        np.fft.rfft2(after - float(np.mean(after)))
    )
    magnitude = np.abs(spectrum)
    # Where two frames share no energy at all the ratio is 0/0; leaving that cell at zero is
    # the honest reading and keeps the transform finite.
    normalised = np.divide(spectrum, magnitude, out=np.zeros_like(spectrum), where=magnitude > 0)
    surface = np.fft.irfft2(normalised, s=(height, width))
    flat = int(np.argmax(surface))
    peak = float(surface.flat[flat])
    down, across = divmod(flat, width)
    dy = float(down - height if down > height // 2 else down)
    dx = float(across - width if across > width // 2 else across)
    far = max(abs(dx), abs(dy)) > CUT_SHIFT * width
    return Move(dx, dy, round(peak, 5), peak < PEAK_FLOOR or far)


def stability(moves: list[Move], width: int) -> list[float | None]:
    """Score each move on how far it sits from the trend of its neighbours.

    This is the whole distinction between a shot that moves and a shot that shakes. The trend
    is the mean of the window's *measurable* moves — the discontinuities are dropped before it
    is taken, so the one outlier a median would have been protecting against is already gone,
    and a mean is what reads a wobble correctly. A median of a shift that alternates sign
    lands on one of the two extremes and reports half the wobble as no wobble at all.
    """
    good = [None if one.discontinuity else one for one in moves]
    scores: list[float | None] = []
    for index, move in enumerate(good):
        if move is None:
            scores.append(None)
            continue
        window = _neighbours(good, index)
        trend_x = statistics.fmean([one.dx for one in window])
        trend_y = statistics.fmean([one.dy for one in window])
        residual = float(np.hypot(move.dx - trend_x, move.dy - trend_y))
        scores.append(round(max(0.0, 1.0 - residual / (SHAKE_SPAN * width)), 4))
    return scores


def _neighbours(good: list[Move | None], index: int) -> list[Move]:
    """The moves the trend at ``index`` is taken over: its own run, and no further.

    A window is not allowed to reach across a discontinuity, and this is the difference
    between a stability score and a report on where the cuts are. Half a second either side
    of a boundary is a window holding shifts from two different shots — a locked-off camera
    next to a panning one — and their average predicts neither, so both sides would come back
    reading as unpredicted movement. The trend a shot's movement is judged against has to be
    that shot's own.
    """
    window = [good[index]]
    for step in range(1, SMOOTH_WINDOW // 2 + 1):
        for at in (index - step, index + step):
            if 0 <= at < len(good) and good[at] is not None and _unbroken(good, index, at):
                window.append(good[at])
    return [one for one in window if one is not None]


def _unbroken(good: list[Move | None], index: int, at: int) -> bool:
    """Whether every move between the two is measurable — no cut sits in between."""
    first, last = (index, at) if index < at else (at, index)
    return all(good[one] is not None for one in range(first, last + 1))


def failures(reading: Reading, floors: Floors) -> tuple[str, ...]:
    """Which floors this sample misses, named. Empty is a sample worth cutting to.

    A stability that could not be measured is not a failure: a black frame is a lot of things,
    but it is not a shaky camera, and reporting it as one would put an unusable window over
    every cut in the edit.
    """
    missed: list[str] = []
    if reading.sharpness < floors.sharpness:
        missed.append("soft")
    if reading.clipped > floors.clipped:
        missed.append("clipped")
    if reading.stability is not None and reading.stability < floors.stability:
        missed.append("shaky")
    return tuple(missed)


def summarize(readings: list[Reading], floors: Floors | None = None) -> dict[str, Any]:
    """One block describing a stretch of samples — a shot, a window, a whole scan.

    Middles for the two readings that describe the picture, and the worst moment for the two
    that veto it. A frame with a stage light burned through it, or a half-second where the
    camera lurches, is visible the instant it is on screen — a shot is as usable as its worst
    moment, not as its average one. Sharpness and exposure are the opposite: they are
    properties of the take, and their extremes over a stretch are a focus pull and a lighting
    change rather than a defect.

    Taking stability as a minimum is only honest because the estimator is quiet enough to
    bear it: over 3553 measurable samples of the delivered corpus the *lowest single sample*
    is 0.844, well clear of the 0.75 floor
    (`gauntlet/recon/image_quality_calib.json`). It was not always — the guard that refuses
    to score a pair across a cut used to let some through, and the resulting one-in-a-hundred
    false dips would have made a minimum unusable. That was worth fixing at the source rather
    than smoothing over here (#182). ``stability_median`` is carried beside it, because the
    difference between the two is what says whether a shot wobbled once or throughout.

    A stretch nothing could be measured over reports ``samples: 0`` rather than zeroes.
    """
    if not readings:
        return {"samples": 0}
    steady = [one.stability for one in readings if one.stability is not None]
    summary: dict[str, Any] = {
        "samples": len(readings),
        "sharpness": _rounded(statistics.median(one.sharpness for one in readings)),
        "exposure": _rounded(statistics.median(one.exposure for one in readings)),
        "contrast": _rounded(statistics.median(one.contrast for one in readings)),
        "clipped": _rounded(max(one.clipped for one in readings)),
        "crushed": _rounded(max(one.crushed for one in readings)),
        "stability": _rounded(min(steady)) if steady else None,
        "stability_median": _rounded(statistics.median(steady)) if steady else None,
        "stability_samples": len(steady),
    }
    if floors is not None:
        missed = [name for one in readings for name in failures(one, floors)]
        # Two different questions, and both are worth an answer. ``failures`` counts the
        # samples that missed each floor — the raw evidence. ``usable`` is the verdict on the
        # stretch as a whole, taken against the aggregate rather than against its unluckiest
        # sample, because a stretch is not unusable for having had one bad quarter-second.
        summary["failures"] = {name: missed.count(name) for name in sorted(set(missed))}
        summary["verdict"] = list(failures(_aggregate(summary), floors))
        summary["usable"] = not summary["verdict"]
    return summary


def _aggregate(summary: dict[str, Any]) -> Reading:
    """The stretch read back as though it were one frame, for a verdict on the whole of it."""
    return Reading(
        sharpness=float(summary["sharpness"]),
        exposure=float(summary["exposure"]),
        contrast=float(summary["contrast"]),
        clipped=float(summary["clipped"]),
        crushed=float(summary["crushed"]),
        stability=None if summary["stability"] is None else float(summary["stability"]),
        discontinuity=False,
    )


def _sharpness(luma: NDArray[np.float64]) -> float:
    """Acutance mapped onto 0 to 1: how much detail a small blur can still take away."""
    fine = _detail(luma)
    coarse = _detail(np.asarray(ndimage.uniform_filter(luma, size=BLUR_WINDOW)))
    if fine <= 0.0 or coarse <= 0.0:
        # A frame with no gradient anywhere is not sharp; it is a flat field.
        return 0.0
    return min(1.0, max(0.0, (fine / coarse - SHARP_FLOOR) / SHARP_SPAN))


def _detail(luma: NDArray[np.float64]) -> float:
    """Mean absolute gradient of a frame, both axes, prepended so the shape is kept."""
    across = np.abs(np.diff(luma, axis=1, prepend=luma[:, :1]))
    down = np.abs(np.diff(luma, axis=0, prepend=luma[:1, :]))
    return float(np.mean(across) + np.mean(down))


def _rounded(value: float) -> float:
    return round(float(value), 4)
