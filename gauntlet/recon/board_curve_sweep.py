"""How long a burst lasts on a board mix, and therefore what the burst minimum has to be.

The evidence behind ``QUIET_BURST_SECONDS``. Sweeps the threshold and the burst minimum
over the dumped curve with the shipped `applause.spans`/`tunes` — no settle step, because
this question is only about which bursts are found at all — and records where the
boundaries land. The finding: at a 3-second minimum two of the five measured boundaries
have no burst at any threshold, because a compressed curve clears its own threshold at the
burst's peak and nowhere else.

Usage: python board_curve_sweep.py <curve.npz> <out.json> [duration_seconds]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

TRUTH = (107.4405, 1373.8725, 1920.1265, 2725.014, 3568.4815)
TOLERANCE = 5.0
DURATION = 4450.5

THRESHOLDS = (0.3, 0.2, 0.12, 0.09, 0.06, 0.045, 0.03, 0.02)
BURSTS = (1.5, 2.0, 2.5, 3.0)


def main() -> None:
    from resolve_mcp.analysis import applause

    loaded = np.load(sys.argv[1])
    curve = applause.Curve(
        seconds=tuple(float(one) for one in loaded["seconds"]),
        probability=tuple(float(one) for one in loaded["probability"]),
    )
    out = Path(sys.argv[2])
    duration = float(sys.argv[3]) if len(sys.argv) > 3 else DURATION

    runs: list[dict[str, Any]] = []
    for threshold in THRESHOLDS:
        for burst in BURSTS:
            spans = applause.spans(curve, threshold, burst, applause.DEFAULT_GAP_SECONDS)
            found = applause.tunes(spans, duration, applause.DEFAULT_TUNE_SECONDS)
            starts = [one.start for one in found]
            errors = [min((abs(one - want) for one in starts), default=1e9) for want in TRUTH]
            runs.append(
                {
                    "threshold": threshold,
                    "burst_seconds": burst,
                    "applause_count": len(spans),
                    "tunes": len(found),
                    # Before the settle step the boundary is the end of the applause, so a
                    # "hit" here means a burst was found near the start, not that the start
                    # was called correctly. That is the question this sweep is asking.
                    "bursts_near_a_human_start": sum(1 for one in errors if one <= 90.0),
                    "starts_s": [round(one, 1) for one in starts],
                }
            )

    best = max(one["bursts_near_a_human_start"] for one in runs)
    report = {
        "curve": sys.argv[1],
        "human_cut_starts_s": list(TRUTH),
        "peak_probability": round(max(curve.probability), 4),
        "most_boundaries_reached": best,
        "burst_seconds_that_reach_all_five": sorted(
            {one["burst_seconds"] for one in runs if one["bursts_near_a_human_start"] == len(TRUTH)}
        ),
        "runs": runs,
    }
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("DONE", out, "best", best, "of", len(TRUTH))


if __name__ == "__main__":
    main()
