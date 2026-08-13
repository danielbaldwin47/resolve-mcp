"""Beat-grid trust and spacing for the Zinc mix — read off the beats file, no Resolve.

The grid is what every cut-placement decision is measured against, so this asks the only
question that matters before trusting it: is the spacing between beats stable, and where is
it not. Writes gauntlet/recon/beat_trust.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

BEATS = Path(
    r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis\Zinc-Set-2-Reaper-v4-62537c590a14-beats.json"
)
OUT = Path(__file__).with_name("beat_trust.json")
BIN_SECONDS = 60.0


def main() -> None:
    raw = json.loads(BEATS.read_text(encoding="utf-8"))
    times = np.array([b["t"] for b in raw["beats"]], dtype=float)
    gaps = np.diff(times)
    median = float(np.median(gaps))

    # A beat is "in tempo" when its own gap is within 10% of the local median (31-beat window).
    local = np.array(
        [np.median(gaps[max(0, i - 15) : i + 16]) for i in range(len(gaps))], dtype=float
    )
    steady = np.abs(gaps - local) <= 0.10 * local

    edges = np.arange(0.0, raw["duration_seconds"] + BIN_SECONDS, BIN_SECONDS)
    which = np.digitize(times[:-1], edges) - 1
    per_minute = []
    for b in range(len(edges) - 1):
        mask = which == b
        if not mask.any():
            per_minute.append({"t": float(edges[b]), "beats": 0})
            continue
        per_minute.append(
            {
                "t": float(edges[b]),
                "beats": int(mask.sum()),
                "median_gap_s": round(float(np.median(gaps[mask])), 4),
                "bpm": round(60.0 / float(np.median(gaps[mask])), 1),
                "steady_share": round(float(steady[mask].mean()), 3),
            }
        )

    report = {
        "beats_file": str(BEATS),
        "count": int(len(times)),
        "duration_seconds": raw["duration_seconds"],
        "reported_tempo_bpm": raw.get("tempo_bpm"),
        "reported_meter": raw.get("meter"),
        "gap_seconds": {
            "median": round(median, 4),
            "mean": round(float(gaps.mean()), 4),
            "p05": round(float(np.percentile(gaps, 5)), 4),
            "p95": round(float(np.percentile(gaps, 95)), 4),
            "min": round(float(gaps.min()), 4),
            "max": round(float(gaps.max()), 4),
        },
        "implied_bpm": {
            "median": round(60.0 / median, 2),
            "p05": round(60.0 / float(np.percentile(gaps, 95)), 2),
            "p95": round(60.0 / float(np.percentile(gaps, 5)), 2),
        },
        "steady_share_overall": round(float(steady.mean()), 4),
        "gaps_over_2s": int((gaps > 2.0).sum()),
        "largest_gaps": [
            {"t": round(float(times[i]), 2), "gap_s": round(float(gaps[i]), 2)}
            for i in np.argsort(gaps)[-15:][::-1]
        ],
        "per_minute": per_minute,
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("DONE", OUT)


if __name__ == "__main__":
    main()
