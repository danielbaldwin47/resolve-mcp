"""Calibrate the image-quality floors on the corpus. READ-ONLY.

`resolve_mcp.video.picture` reports four readings and `analyze_quality` vetoes a shot on
three floors. What counts as soft, blown or shaky is not a number anyone can pick from a
chair: it has to sit below the human editor's own delivered footage — those are the frames a
judge accepted — and above footage that has actually failed. This script produces the
evidence for both sides.

**Known-good** is the five human deliverables as shipped, sampled through the same decode
`analyze_quality` runs. **Known-bad** is those same frames with each failure applied to them:
a gaussian defocus, a gain that burns the highlights, a gain that closes them down, and a
synthetic handheld wobble.
Deriving the bad side from the corpus rather than from a synthetic fixture is the point — the
question is whether the reading separates *this footage* from *this footage gone wrong*, and
a fixture pair could be separated by a measurement that says nothing about a concert.

A window rather than a whole song: five 4K masters decoded end to end is an hour of ffmpeg to
answer a question 180 s of each already answers, and the window is taken from a quarter of the
way in so it is the band playing rather than the count-in.

Usage: uv run python gauntlet/recon/image_quality_calib.py [--songs N] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gauntlet" / "tools"))

import ab_pack  # noqa: E402

from resolve_mcp.video import picture  # noqa: E402
from resolve_mcp.video.quality import (  # noqa: E402
    DEFAULT_MAX_CLIPPED,
    DEFAULT_MIN_SHARPNESS,
    DEFAULT_MIN_STABILITY,
    DEFAULT_SAMPLE_FPS,
)

HUMAN_DIR = Path(r"S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos")
OUT = ROOT / "gauntlet" / "recon" / "image_quality_calib.json"

WINDOW_SEC = 180.0
WINDOW_AT = 0.25
"""How much of each song is sampled, and where it starts as a fraction of the song."""

DEFOCUS_SIGMAS = (1.0, 2.0, 3.0)
"""Gaussian sigmas, in grid pixels. On the quality grid a 4K master is reduced 6x, so sigma 1
here is a defocus of about six pixels at the master — a miss a director would reject and an
operator might not see on a small monitor."""

BLOWN_GAINS = (1.6, 2.2)
DARK_GAINS = (0.35,)
"""Multiplied on luma before clipping at 1.0: an angle exposed for the wrong light, too open
and too closed. The dark case is what shows the exposure reading discriminating at all — it
is the only failure of the four that clipping does not also catch, and it has no floor of its
own, because what counts as correctly exposed is a property of the room rather than of the
shot (this corpus sits at a mean luma of 0.09 and is not underexposed)."""

SHAKE_PIXELS = (3, 6)
"""Alternating translations, in grid pixels — a wobble with no trend for the residual to
subtract. Six is 2% of frame width, the point the stability score reaches zero."""

SHOW_WORST = 6
"""How many of the corpus's own worst samples the receipt names, with their times."""

SHARPNESS_CANDIDATES = (0.25, 0.30, 0.35, 0.40, 0.45, 0.50)
STABILITY_CANDIDATES = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85)
CLIPPED_CANDIDATES = (0.005, 0.01, 0.015, 0.02, 0.03, 0.05)
"""The sweep. A floor is only defensible against the two numbers either side of it — how
much of the delivered corpus it would veto, and how much of the failure it would let past —
so the receipt carries the whole curve rather than the one number that got shipped."""

RULE = (
    "midway between the corpus 5th percentile and the median of the mildest failure applied "
    "to that same footage, moved to the neighbouring grid point that vetoes less of the "
    "corpus (0.05, or 0.005 for clipped). The 5th percentile rather than the extreme on both "
    "sides: the tails of a 3600-sample scan are whip pans and strobe frames, and a floor set "
    "on those is a floor set on the estimator's worst moment rather than on the footage. "
    "Rounding towards the laxer grid point so that no floor vetoes delivered footage because "
    "of where a grid line fell."
)


def decoded(clip: Path, start: float, seconds: float, rate: float) -> np.ndarray:
    return ab_pack.decode_grey(
        clip,
        picture.GRID_WIDTH,
        picture.GRID_HEIGHT,
        fps=rate,
        start=start,
        dur=seconds,
        dtype=np.uint8,
    )


def defocused(frames: np.ndarray, sigma: float) -> np.ndarray:
    """The same footage out of focus. Gaussian, because that is the shape a lens misses in —
    and deliberately not the box filter the sharpness reading itself uses."""
    blurred = ndimage.gaussian_filter(frames.astype(np.float64), sigma=(0.0, sigma, sigma))
    return np.clip(np.rint(blurred), 0, 255).astype(np.uint8)


def gained(frames: np.ndarray, gain: float) -> np.ndarray:
    """The same footage exposed for different light — above 1 burns it, below 1 closes it."""
    return np.clip(np.rint(frames.astype(np.float64) * gain), 0, 255).astype(np.uint8)


def shaken(frames: np.ndarray, pixels: int) -> np.ndarray:
    """Every other frame translated, which is a wobble with no trend behind it."""
    out = frames.copy()
    for index in range(1, len(out), 2):
        out[index] = np.roll(np.roll(out[index], pixels, axis=1), pixels // 2, axis=0)
    return out


def readings(frames: np.ndarray) -> list[picture.Reading]:
    return list(picture.measure(frames).readings)


def spread(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "min": round(ordered[0], 4),
        "p05": round(ordered[max(0, int(0.05 * len(ordered)) - 1)], 4),
        "median": round(statistics.median(ordered), 4),
        "p95": round(ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))], 4),
        "max": round(ordered[-1], 4),
    }


def _share(values: list[float], failing: Any) -> float:
    """What fraction of a set a floor would flag."""
    return round(sum(1 for one in values if failing(one)) / max(1, len(values)), 4)


def sharpness_of(rows: list[picture.Reading]) -> list[float]:
    return [one.sharpness for one in rows]


def clipped_of(rows: list[picture.Reading]) -> list[float]:
    return [one.clipped for one in rows]


def stability_of(rows: list[picture.Reading]) -> list[float]:
    return [one.stability for one in rows if one.stability is not None]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--songs", type=int, default=0, help="only the first N songs")
    parser.add_argument("--rate", type=float, default=DEFAULT_SAMPLE_FPS)
    parser.add_argument("--seconds", type=float, default=WINDOW_SEC)
    parser.add_argument("--out", type=Path, default=OUT)
    # The guard that refuses to score a frame pair across a cut is what decides whether a
    # delivered edit reads as stable, so its effect has to be reproducible rather than
    # remembered: run this with the pre-#182 values (--peak-floor 0.01 --cut-shift 0.10) and
    # the receipt shows what the loose guard cost.
    parser.add_argument("--peak-floor", type=float, default=None)
    parser.add_argument("--cut-shift", type=float, default=None)
    args = parser.parse_args(argv)

    if args.peak_floor is not None:
        picture.PEAK_FLOOR = args.peak_floor
    if args.cut_shift is not None:
        picture.CUT_SHIFT = args.cut_shift

    songs = sorted(HUMAN_DIR.glob("*.mp4"))
    if args.songs:
        songs = songs[: args.songs]
    if not songs:
        print(f"error: no deliverables under {HUMAN_DIR}", file=sys.stderr)
        return 1

    per_song: list[dict[str, Any]] = []
    good: list[picture.Reading] = []
    bad: dict[str, list[picture.Reading]] = {}

    for song in songs:
        duration = ab_pack.probe_duration(song)
        start = max(0.0, duration * WINDOW_AT)
        seconds = min(args.seconds, max(0.0, duration - start))
        frames = decoded(song, start, seconds, args.rate)
        rows = readings(frames)
        good += rows

        worst = sorted(range(len(rows)), key=lambda i: rows[i].sharpness)[:SHOW_WORST]
        per_song.append(
            {
                "song": song.stem,
                "duration_sec": round(duration, 2),
                "window": {"start_sec": round(start, 2), "seconds": round(seconds, 2)},
                "samples": len(rows),
                "sharpness": spread(sharpness_of(rows)),
                "exposure": spread([one.exposure for one in rows]),
                "contrast": spread([one.contrast for one in rows]),
                "clipped": spread(clipped_of(rows)),
                "crushed": spread([one.crushed for one in rows]),
                "stability": spread(stability_of(rows)),
                "unmeasurable_stability": sum(1 for one in rows if one.stability is None),
                "softest_samples": [
                    {
                        "t_sec": round(start + index / args.rate, 2),
                        "sharpness": rows[index].sharpness,
                        "exposure": rows[index].exposure,
                    }
                    for index in worst
                ],
            }
        )

        for sigma in DEFOCUS_SIGMAS:
            bad.setdefault(f"defocus_sigma_{sigma:g}", []).extend(
                readings(defocused(frames, sigma))
            )
        for gain in BLOWN_GAINS:
            bad.setdefault(f"blown_gain_{gain:g}", []).extend(readings(gained(frames, gain)))
        for gain in DARK_GAINS:
            bad.setdefault(f"dark_gain_{gain:g}", []).extend(readings(gained(frames, gain)))
        for pixels in SHAKE_PIXELS:
            bad.setdefault(f"shake_{pixels}px", []).extend(readings(shaken(frames, pixels)))

    good_sharp = sharpness_of(good)
    good_clip = clipped_of(good)
    good_steady = stability_of(good)

    degraded = {
        name: {
            "sharpness": spread(sharpness_of(rows)),
            "exposure": spread([one.exposure for one in rows]),
            "clipped": spread(clipped_of(rows)),
            "crushed": spread([one.crushed for one in rows]),
            "stability": spread(stability_of(rows)),
        }
        for name, rows in sorted(bad.items())
    }

    sweep = {
        "min_sharpness": [
            {
                "floor": floor,
                "corpus_vetoed": _share(good_sharp, lambda v, f=floor: v < f),
                **{
                    name: _share(sharpness_of(rows), lambda v, f=floor: v < f)
                    for name, rows in sorted(bad.items())
                    if name.startswith("defocus")
                },
            }
            for floor in SHARPNESS_CANDIDATES
        ],
        "min_stability": [
            {
                "floor": floor,
                "corpus_vetoed": _share(good_steady, lambda v, f=floor: v < f),
                **{
                    name: _share(stability_of(rows), lambda v, f=floor: v < f)
                    for name, rows in sorted(bad.items())
                    if name.startswith("shake")
                },
            }
            for floor in STABILITY_CANDIDATES
        ],
        "max_clipped": [
            {
                "floor": floor,
                "corpus_vetoed": _share(good_clip, lambda v, f=floor: v > f),
                **{
                    name: _share(clipped_of(rows), lambda v, f=floor: v > f)
                    for name, rows in sorted(bad.items())
                    if name.startswith("blown")
                },
            }
            for floor in CLIPPED_CANDIDATES
        ],
    }

    receipt = {
        "question": "do the image-quality readings separate the delivered corpus from the "
        "same footage soft, blown or shaky, and where do the floors sit between them",
        "rule": RULE,
        "floor_sweep": sweep,
        "generated_by": "gauntlet/recon/image_quality_calib.py",
        "grid": f"{picture.GRID_WIDTH}x{picture.GRID_HEIGHT}",
        "sample_fps": args.rate,
        "guards": {"peak_floor": picture.PEAK_FLOOR, "cut_shift": picture.CUT_SHIFT},
        "window": f"{args.seconds:g}s from {WINDOW_AT:.0%} into each song",
        "floors_under_test": {
            "min_sharpness": DEFAULT_MIN_SHARPNESS,
            "max_clipped": DEFAULT_MAX_CLIPPED,
            "min_stability": DEFAULT_MIN_STABILITY,
        },
        "known_good": {
            "songs": len(songs),
            "samples": len(good),
            "sharpness": spread(good_sharp),
            "exposure": spread([one.exposure for one in good]),
            "clipped": spread(good_clip),
            "crushed": spread([one.crushed for one in good]),
            "stability": spread(good_steady),
            "below_sharpness_floor": sum(1 for one in good_sharp if one < DEFAULT_MIN_SHARPNESS),
            "above_clipped_floor": sum(1 for one in good_clip if one > DEFAULT_MAX_CLIPPED),
            "below_stability_floor": sum(
                1 for one in good_steady if one < DEFAULT_MIN_STABILITY
            ),
        },
        "known_bad": degraded,
        "caught_by_the_floors": {
            name: {
                "sharpness": round(
                    sum(1 for one in sharpness_of(rows) if one < DEFAULT_MIN_SHARPNESS)
                    / max(1, len(rows)),
                    4,
                ),
                "clipped": round(
                    sum(1 for one in clipped_of(rows) if one > DEFAULT_MAX_CLIPPED)
                    / max(1, len(rows)),
                    4,
                ),
                "stability": round(
                    sum(1 for one in stability_of(rows) if one < DEFAULT_MIN_STABILITY)
                    / max(1, len(stability_of(rows)) or 1),
                    4,
                ),
            }
            for name, rows in sorted(bad.items())
        },
        "per_song": per_song,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    print(json.dumps(receipt["known_good"], indent=2))
    print(json.dumps(receipt["caught_by_the_floors"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
