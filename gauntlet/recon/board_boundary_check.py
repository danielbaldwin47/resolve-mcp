"""The #179 receipt: the shipped pipeline's boundaries on a real mix, old settings and new.

Runs `resolve_mcp.analysis.applause` itself — no reimplementation — over a curve dumped by
`board_curve_dump.py` and the loudness curve `analyze_music` wrote, and reports what each
setting calls. Two mixes matter: the Zinc Set 2 board mix, where five human-established
starts are the acceptance criterion, and the Scullers room mic, which is what "no
regression on material with an audible crowd" means. Writes one receipt per mix.

Usage:

    python board_boundary_check.py <curve.npz> <energy.json> <duration> <out.json>
                                   [truth,truth,...] [beats.json]

The human starts are optional (the room mic has none) and so is the beat grid; pass it and
each reading also reports what the density floor would drop, which is how the two filters
were checked against each other.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

TOLERANCE = 5.0


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
    duration = float(sys.argv[3])
    out = Path(sys.argv[4])
    wanted = sys.argv[5] if len(sys.argv) > 5 else ""
    truth = [float(one) for one in wanted.split(",")] if wanted else []
    grid: list[float] = []
    if len(sys.argv) > 6:
        beats = json.loads(Path(sys.argv[6]).read_text(encoding="utf-8"))["beats"]
        grid = [float(row["t"]) for row in beats]

    report: dict[str, Any] = {
        "curve": sys.argv[1],
        "energy": sys.argv[2],
        "duration_s": duration,
        "human_cut_starts_s": truth,
        "peak_probability": round(max(curve.probability), 4),
        "median_lufs": round(float(np.median(loudness.lufs)), 2),
        "readings": {
            "before #179": _run(
                applause, curve, loudness, duration, truth, grid, scale=0.0, hold=0.0
            ),
            "settle only": _run(applause, curve, loudness, duration, truth, grid, scale=0.0),
            "after #179": _run(applause, curve, loudness, duration, truth, grid),
        },
    }
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("DONE", out)


def _run(
    applause: Any,
    curve: Any,
    loudness: Any,
    duration: float,
    truth: list[float],
    grid: list[float],
    scale: float | None = None,
    hold: float | None = None,
) -> dict[str, Any]:
    """One reading of the curve, end to end, as the tune half would do it."""
    scale = applause.DEFAULT_SCALE if scale is None else scale
    hold = applause.DEFAULT_SETTLE_SECONDS if hold is None else hold

    read = applause.reading(
        curve, applause.DEFAULT_THRESHOLD, scale, applause.DEFAULT_MINIMUM_SECONDS
    )
    spans = applause.spans(curve, read.threshold, read.burst_seconds, applause.DEFAULT_GAP_SECONDS)
    found = applause.tunes(spans, duration, applause.DEFAULT_TUNE_SECONDS)
    settled = applause.settled(
        found, loudness, applause.DEFAULT_SETTLE_DB, hold, applause.DEFAULT_TUNE_SECONDS
    )
    starts = [one.start for one in settled.kept]
    entry: dict[str, Any] = {
        "threshold_used": round(read.threshold, 4),
        "burst_seconds_used": read.burst_seconds,
        "read_at_own_scale": read.own_scale,
        "settle_seconds": hold,
        "applause_count": len(spans),
        "tunes": len(settled.kept),
        "starts_s": [round(one, 1) for one in starts],
        "talk_seconds": [one.talk_seconds for one in settled.kept],
        "silent_calls": [f"{one.start:.1f}-{one.end:.1f}" for one in settled.silent],
        "brief_calls": [f"{one.start:.1f}-{one.end:.1f}" for one in settled.brief],
    }
    if grid:
        sifted = applause.sifted(applause.counted(settled.kept, grid))
        entry["tunes_after_the_density_floor"] = len(sifted.kept)
        entry["no_pulse"] = [
            f"{one.start:.1f}-{one.end:.1f} at {one.beats_per_second}/s" for one in sifted.dropped
        ]
    if truth:
        errors = [min((abs(one - want) for one in starts), default=1e9) for want in truth]
        entry["errors_s"] = [round(one, 2) for one in errors]
        entry["within_tolerance"] = sum(1 for one in errors if one <= TOLERANCE)
    return entry


if __name__ == "__main__":
    main()
