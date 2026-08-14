"""The #179 receipt: the shipped pipeline's boundaries on a real mix, old settings and new.

Runs `resolve_mcp.analysis.applause` itself — no reimplementation — over a curve dumped by
`board_curve_dump.py` and the loudness curve `analyze_music` wrote, and prints what each
setting calls. Two mixes matter: the Zinc Set 2 board mix, where five human-established
starts are the acceptance criterion, and the Scullers room mic, which is what "no
regression on material with an audible crowd" means.

Usage: python board_boundary_check.py <curve.npz> <energy.json> <duration> [truth,truth,...]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
    truth = [float(one) for one in sys.argv[4].split(",")] if len(sys.argv) > 4 and sys.argv[4] else []
    grid: list[float] = []
    if len(sys.argv) > 5:
        beats = json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))["beats"]
        grid = [float(row["t"]) for row in beats]
        globals()["GRID"] = grid

    print(f"peak={max(curve.probability):.4f} median_lufs={np.median(loudness.lufs):.2f}")
    _run(applause, "before #179", curve, loudness, duration, truth, scale=0.0, hold=0.0)
    _run(applause, "settle only", curve, loudness, duration, truth, scale=0.0)
    _run(applause, "after #179", curve, loudness, duration, truth)


def _run(
    applause,
    name: str,
    curve,
    loudness,
    duration: float,
    truth: list[float],
    scale: float = None,
    hold: float = None,
) -> None:
    scale = applause.DEFAULT_SCALE if scale is None else scale
    hold = applause.DEFAULT_SETTLE_SECONDS if hold is None else hold

    read = applause.reading(
        curve, applause.DEFAULT_THRESHOLD, scale, applause.DEFAULT_MINIMUM_SECONDS
    )
    threshold, burst = read.threshold, read.burst_seconds
    spans = applause.spans(curve, threshold, burst, applause.DEFAULT_GAP_SECONDS)
    found = applause.tunes(spans, duration, applause.DEFAULT_TUNE_SECONDS)
    settled = applause.settled(
        found, loudness, applause.DEFAULT_SETTLE_DB, hold, applause.DEFAULT_TUNE_SECONDS
    )
    starts = [one.start for one in settled.kept]
    print(
        f"\n-- {name} -- threshold={threshold:.4f} burst={burst} hold={hold}\n"
        f"   bursts={len(spans)} tunes={len(settled.kept)} "
        f"silent={len(settled.silent)} brief={len(settled.brief)}"
    )
    print(f"   starts={[round(one, 1) for one in starts]}")
    print(f"   talk={[one.talk_seconds for one in settled.kept]}")
    for label, calls in (("silent", settled.silent), ("brief", settled.brief)):
        for one in calls:
            print(f"   {label} call {one.start:.1f}-{one.end:.1f}")
    grid = globals().get("GRID") or []
    if grid:
        sifted = applause.sifted(applause.counted(settled.kept, grid))
        print(f"   after the density floor: {len(sifted.kept)} tunes")
        for one in sifted.dropped:
            print(f"   no pulse {one.start:.1f}-{one.end:.1f} at {one.beats_per_second}/s")
    if truth:
        errors = [min((abs(one - want) for one in starts), default=1e9) for want in truth]
        print(
            f"   hit={sum(1 for one in errors if one <= TOLERANCE)}/{len(truth)} "
            f"errors={[round(one, 2) for one in errors]}"
        )


if __name__ == "__main__":
    main()
