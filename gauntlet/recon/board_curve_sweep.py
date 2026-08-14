"""Score candidate applause thresholds against the human spans, offline, on a dumped curve.

Every rule here is arithmetic over the curve `board_curve_dump.py` wrote, so a sweep costs
milliseconds instead of a 90-second tagging run. Prints a row per candidate: the tune
boundaries it calls, how many human boundaries it hits within the tolerance, and what it
invents. Usage: python board_curve_sweep.py <curve.npz> [duration_seconds]
"""

from __future__ import annotations

import sys

import numpy as np

TRUTH = (107.4405, 1373.8725, 1920.1265, 2725.014, 3568.4815)
TOLERANCE = 5.0
DURATION = 4450.5


def main() -> None:
    from resolve_mcp.analysis import applause

    loaded = np.load(sys.argv[1])
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else DURATION
    curve = applause.Curve(
        seconds=tuple(float(one) for one in loaded["seconds"]),
        probability=tuple(float(one) for one in loaded["probability"]),
    )
    peak = max(curve.probability)
    print(f"peak={peak:.4f} frames={len(curve.seconds)} duration={duration:.1f}")

    candidates: list[tuple[str, float]] = [
        (f"fixed {one:.3f}", one) for one in (0.3, 0.2, 0.12, 0.09, 0.06, 0.045, 0.03, 0.02)
    ]
    for fraction in (0.1, 0.15, 0.2, 0.25, 0.3):
        candidates.append((f"peak*{fraction}", peak * fraction))

    for name, threshold in candidates:
        for burst in (3.0, 2.0):
            _row(applause, curve, name, threshold, burst, duration)


def _row(applause, curve, name: str, threshold: float, burst: float, duration: float) -> None:
    spans = applause.spans(curve, threshold, burst, 1.5)
    tunes = applause.tunes(spans, duration, 60.0)
    starts = [one.start for one in tunes]
    hits = [
        min(starts, key=lambda one: abs(one - truth), default=1e9) - truth
        for truth in TRUTH
    ]
    within = sum(1 for one in hits if abs(one) <= TOLERANCE)
    print(
        f"{name:<14} burst={burst:<4} bursts={len(spans):<3} tunes={len(tunes):<3} "
        f"hit={within}/5 offsets={[round(one, 1) for one in hits]}"
    )
    print(f"{'':<14} starts={[round(one, 1) for one in starts]}")


if __name__ == "__main__":
    main()
