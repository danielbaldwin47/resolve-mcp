"""The calibration behind the #179 constants: which settings find the five human starts.

Drives the shipped `resolve_mcp.analysis.applause` — never a copy of it — over the curve
`board_curve_dump.py` wrote and the loudness curve `analyze_music` wrote, across the three
numbers the two new rules turn on:

* ``scale``, how much of the file's own applause peak counts as applause;
* ``settle_db``, how far under the file's median loudness still counts as playing;
* ``settle_seconds``, how long it has to stay there.

The receipt is the plateau: every combination that finds all five starts within the
tolerance and invents nothing. A constant picked in the middle of a wide plateau is a
measurement; one picked on an edge is a fit, and the difference is visible here.

Usage: python board_boundary_sweep.py <curve.npz> <energy.json> <out.json> [duration]
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

SCALES = (0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.15)
MARGINS = (4.0, 6.0, 8.0, 10.0, 12.0)
HOLDS = (5.0, 10.0, 15.0, 20.0)


def main() -> None:
    from resolve_mcp.analysis import applause

    loaded = np.load(sys.argv[1])
    curve = applause.Curve(
        seconds=tuple(float(one) for one in loaded["seconds"]),
        probability=tuple(float(one) for one in loaded["probability"]),
    )
    rows = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["energy"]
    loudness = applause.Loudness(
        seconds=tuple(float(row["t"]) for row in rows),
        lufs=tuple(float(row["lufs"]) for row in rows),
    )
    out = Path(sys.argv[3])
    duration = float(sys.argv[4]) if len(sys.argv) > 4 else DURATION

    runs: list[dict[str, Any]] = []
    for scale in SCALES:
        read = applause.reading(curve, applause.DEFAULT_THRESHOLD, scale)
        spans = applause.spans(
            curve, read.threshold, read.burst_seconds, applause.DEFAULT_GAP_SECONDS
        )
        found = applause.tunes(spans, duration, applause.DEFAULT_TUNE_SECONDS)
        for margin in MARGINS:
            for hold in HOLDS:
                settled = applause.settled(
                    found, loudness, margin, hold, applause.DEFAULT_TUNE_SECONDS
                )
                starts = [one.start for one in settled.kept]
                errors = [min((abs(one - want) for one in starts), default=1e9) for want in TRUTH]
                within = sum(1 for one in errors if one <= TOLERANCE)
                runs.append(
                    {
                        "scale": scale,
                        "settle_db": margin,
                        "settle_seconds": hold,
                        "threshold_used": round(read.threshold, 4),
                        "burst_seconds_used": read.burst_seconds,
                        "tunes": len(starts),
                        "within_tolerance": within,
                        "invented": len(starts) - within,
                        "worst_error_s": round(max(errors), 2) if within == len(TRUTH) else None,
                        "starts_s": [round(one, 1) for one in starts],
                    }
                )

    clean = [one for one in runs if one["within_tolerance"] == len(TRUTH) and not one["invented"]]
    report = {
        "curve": sys.argv[1],
        "energy": sys.argv[2],
        "human_cut_starts_s": list(TRUTH),
        "tolerance_s": TOLERANCE,
        "peak_probability": round(max(curve.probability), 4),
        "clean_settings": [
            [one["scale"], one["settle_db"], one["settle_seconds"]] for one in clean
        ],
        "clean_count": len(clean),
        "of": len(runs),
        "worst_error_s": max((one["worst_error_s"] for one in clean), default=None),
        # Per axis, and therefore a union rather than a cross-product: a value listed here
        # is clean *somewhere*, not everywhere. Read `clean_settings` before quoting any of
        # these as a range — the three axes are not independent, and taking them for a box
        # is how a docstring ends up recommending a setting that loses a boundary.
        "clean_per_axis": {
            "scale": sorted({one["scale"] for one in clean}),
            "settle_db": sorted({one["settle_db"] for one in clean}),
            "settle_seconds": sorted({one["settle_seconds"] for one in clean}),
        },
        "runs": runs,
    }
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("DONE", out, "plateau", len(clean), "of", len(runs))


if __name__ == "__main__":
    main()
