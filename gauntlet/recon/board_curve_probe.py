"""What the applause curve actually looks like on a board mix, against the human spans.

Reads the .npz `board_curve_dump.py` wrote and answers three questions without re-tagging:
the distribution of the curve, whether there is any excursion near each human boundary at
all, and where the tallest excursions in the file are. Prints; writes nothing.

Usage: python board_curve_probe.py <curve.npz>
"""

from __future__ import annotations

import sys

import numpy as np

TRUTH = (107.4405, 1373.8725, 1920.1265, 2725.014, 3568.4815)
"""Human cut starts on Zinc Set 2 (gauntlet/recon/tunes_sweep.py)."""


def main() -> None:
    loaded = np.load(sys.argv[1])
    seconds = loaded["seconds"].astype(np.float64)
    probability = loaded["probability"].astype(np.float64)
    print(f"frames={len(seconds)} span={seconds[0]:.2f}..{seconds[-1]:.2f}")
    step = float(np.median(np.diff(seconds)))
    print(f"frame_step={step:.4f}s")

    quantiles = (0.5, 0.9, 0.99, 0.999, 0.9999, 1.0)
    for one in quantiles:
        print(f"q{one:<8} {np.quantile(probability, one):.5f}")

    print("\n-- near each human boundary (window -120s..+20s) --")
    for truth in TRUTH:
        window = (seconds >= truth - 120) & (seconds <= truth + 20)
        if not window.any():
            print(f"{truth:9.2f}  no frames")
            continue
        local = probability[window]
        times = seconds[window]
        best = int(np.argmax(local))
        print(f"{truth:9.2f}  local_peak={local[best]:.4f} at {times[best]:8.2f}s")

    print("\n-- 15 tallest separated excursions --")
    for time, height in _peaks(seconds, probability, 15, 30.0):
        near = min(TRUTH, key=lambda one: abs(one - time))
        print(f"{time:9.2f}  p={height:.4f}  nearest_truth={near:9.2f} ({time - near:+.1f}s)")


def _peaks(
    seconds: np.ndarray, probability: np.ndarray, count: int, apart: float
) -> list[tuple[float, float]]:
    """The tallest frames, greedily thinned so one burst does not fill the list."""
    order = np.argsort(probability)[::-1]
    found: list[tuple[float, float]] = []
    for index in order:
        time = float(seconds[index])
        if any(abs(time - one) < apart for one, _ in found):
            continue
        found.append((time, float(probability[index])))
        if len(found) >= count:
            break
    return sorted(found)


if __name__ == "__main__":
    main()
