"""Applause + loudness -> tune boundaries: score the candidate rules against the human spans.

Three rules under test, all arithmetic over two already measured tracks (the PANNs curve
`board_curve_dump.py` wrote and the loudness curve `analyze_music` writes):

* the applause threshold scales with the file's own peak rather than being absolute;
* a call starts where the mix comes up to playing level and stays there (the announcement
  between the clapping and the downbeat is 20-40 dB down on a board mix);
* a call that never comes up to playing level is not a tune.

Usage: python board_boundary_sweep.py <curve.npz> <energy.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

TRUTH = (107.4405, 1373.8725, 1920.1265, 2725.014, 3568.4815)
TOLERANCE = 5.0
DURATION = 4450.5


def main() -> None:
    from resolve_mcp.analysis import applause

    loaded = np.load(sys.argv[1])
    curve = applause.Curve(
        seconds=tuple(float(one) for one in loaded["seconds"]),
        probability=tuple(float(one) for one in loaded["probability"]),
    )
    rows = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["energy"]
    times = np.asarray([row["t"] for row in rows], dtype=np.float64)
    lufs = np.asarray([row["lufs"] for row in rows], dtype=np.float64)

    print(f"applause peak={max(curve.probability):.4f}")
    for quantile in (0.5, 0.6, 0.75, 0.9):
        print(f"lufs q{quantile} = {np.quantile(lufs, quantile):.2f}")

    playing = float(np.median(lufs))
    for fraction in (0.08, 0.1, 0.12, 0.15):
        threshold = max(curve.probability) * fraction
        for burst in (1.5, 2.0, 2.5, 3.0):
            spans = applause.spans(curve, threshold, burst, 1.5)
            found = applause.tunes(spans, DURATION, 60.0)
            for margin in (4.0, 6.0, 8.0, 10.0, 12.0):
                for hold in (5.0, 10.0, 15.0, 20.0):
                    _score(
                        f"p*{fraction} burst{burst} -{margin}dB hold{hold}",
                        found,
                        times,
                        lufs,
                        playing - margin,
                        hold,
                    )


def _score(name, found, times, lufs, floor: float, hold: float) -> None:
    starts = []
    for tune in found:
        start = _settle(times, lufs, tune.start, tune.end, floor, hold)
        if start is not None:
            starts.append(start)
    hits = [min((abs(one - truth) for one in starts), default=1e9) for truth in TRUTH]
    within = sum(1 for one in hits if one <= TOLERANCE)
    extra = len(starts) - within
    print(
        f"{name:<34} floor={floor:6.1f} calls={len(starts):<3} hit={within}/5 "
        f"extra={extra:<3} err={[round(one, 1) for one in hits]}"
    )
    if within == 5:
        print(f"{'':<34} starts={[round(one, 1) for one in starts]}")


SHARE = 0.75
"""How much of the hold window has to be over the floor. Music dips; it does not stop."""


def _settle(times, lufs, start: float, end: float, floor: float, hold: float) -> float | None:
    """First moment in the call where loudness reaches the floor and mostly stays there."""
    window = (times >= start) & (times < end)
    inside_t = times[window]
    over = (lufs[window] >= floor).astype(float)
    if not len(inside_t):
        return None
    step = float(np.median(np.diff(inside_t))) if len(inside_t) > 1 else 0.5
    width = max(int(round(hold / step)), 1)
    if len(over) < width:
        return None
    shares = np.convolve(over, np.ones(width) / width, mode="valid")
    hits = np.flatnonzero((shares >= SHARE) & (over[: len(shares)] > 0))
    return float(inside_t[hits[0]]) if len(hits) else None


if __name__ == "__main__":
    main()
