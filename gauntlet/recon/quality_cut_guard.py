"""Where the discontinuity guard has to sit. READ-ONLY.

`picture.travel` refuses to score a frame pair it cannot align, and everything about the
stability reading depends on that refusal being right. A pair across a cut is two different
pictures: the correlator answers anyway, with a large meaningless shift, and that shift then
poisons the trend its neighbours are judged against. On the Taurus People deliverable one
such pair (peak 0.02, shift 30 px) slipped a 0.01 peak floor and a 32 px shift ceiling, and
dragged six samples of a locked-off shot to a stability of zero — a delivered shot the report
called shaky (#182).

So the guard is calibrated rather than guessed. Every frame pair of every deliverable is
measured and split by whether a detected cut falls inside it, and the receipt is the two
distributions: what a pair *within* a shot correlates at, and what a pair *across* a cut
correlates at. The floor goes in the gap.

Usage: uv run python gauntlet/recon/quality_cut_guard.py [--songs N] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gauntlet" / "tools"))

import ab_pack  # noqa: E402

from resolve_mcp.video import picture  # noqa: E402

HUMAN_DIR = Path(r"S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos")
OUT = ROOT / "gauntlet" / "recon" / "quality_cut_guard.json"

RATE = 4.0
"""The sampling the quality scan runs at, which is the interval a pair spans."""

PEAK_CANDIDATES = (0.01, 0.03, 0.05, 0.08, 0.12, 0.20)
SHIFT_CANDIDATES = (0.04, 0.06, 0.08, 0.10, 0.15)


def quantile(values: list[float], at: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(at * len(ordered)))], 5)


def spread(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p01": quantile(values, 0.01),
        "p05": quantile(values, 0.05),
        "median": quantile(values, 0.5),
        "p95": quantile(values, 0.95),
        "max": round(max(values), 5) if values else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--songs", type=int, default=0)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    songs = sorted(HUMAN_DIR.glob("*.mp4"))
    if args.songs:
        songs = songs[: args.songs]

    in_peak: list[float] = []
    in_shift: list[float] = []
    cut_peak: list[float] = []
    cut_shift: list[float] = []
    per_song: list[dict[str, Any]] = []

    for song in songs:
        cuts = ab_pack.detect_cuts(song)
        frames = ab_pack.decode_grey(
            song, picture.GRID_WIDTH, picture.GRID_HEIGHT, fps=RATE, dtype=np.uint8
        )
        lumas = [np.asarray(one, dtype=np.float64) / 255.0 for one in frames]
        song_in: list[float] = []
        song_cut: list[float] = []
        for index in range(1, len(lumas)):
            first, last = (index - 1) / RATE, index / RATE
            move = picture.travel(lumas[index - 1], lumas[index])
            # A cut inside the pair's own interval, with a sample's slack either side: the
            # detector's time and the sample grid are two different clocks.
            crossed = any(first - 1.0 / RATE <= one <= last + 1.0 / RATE for one in cuts)
            reach = max(abs(move.dx), abs(move.dy)) / picture.GRID_WIDTH
            if crossed:
                cut_peak.append(move.peak)
                cut_shift.append(reach)
                song_cut.append(move.peak)
            else:
                in_peak.append(move.peak)
                in_shift.append(reach)
                song_in.append(move.peak)
        per_song.append(
            {
                "song": song.stem,
                "cuts": len(cuts),
                "in_shot_peak": spread(song_in),
                "across_cut_peak": spread(song_cut),
            }
        )

    receipt = {
        "question": "where does the phase-correlation peak separate a pair inside a shot "
        "from a pair across a cut, and how far can a real in-shot pair move",
        "generated_by": "gauntlet/recon/quality_cut_guard.py",
        "sample_fps": RATE,
        "grid": f"{picture.GRID_WIDTH}x{picture.GRID_HEIGHT}",
        "guards_under_test": {
            "peak_floor": picture.PEAK_FLOOR,
            "cut_shift": picture.CUT_SHIFT,
        },
        "in_shot": {"peak": spread(in_peak), "shift_fraction": spread(in_shift)},
        "across_cut": {"peak": spread(cut_peak), "shift_fraction": spread(cut_shift)},
        "peak_sweep": [
            {
                "floor": floor,
                "in_shot_refused": round(
                    sum(1 for one in in_peak if one < floor) / max(1, len(in_peak)), 4
                ),
                "across_cut_caught": round(
                    sum(1 for one in cut_peak if one < floor) / max(1, len(cut_peak)), 4
                ),
            }
            for floor in PEAK_CANDIDATES
        ],
        "shift_sweep": [
            {
                "ceiling": ceiling,
                "in_shot_refused": round(
                    sum(1 for one in in_shift if one > ceiling) / max(1, len(in_shift)), 4
                ),
                "across_cut_caught": round(
                    sum(1 for one in cut_shift if one > ceiling) / max(1, len(cut_shift)), 4
                ),
            }
            for ceiling in SHIFT_CANDIDATES
        ],
        "per_song": per_song,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"wrote {args.out}")
    print(json.dumps(receipt["in_shot"], indent=2))
    print(json.dumps(receipt["across_cut"], indent=2))
    print(json.dumps(receipt["peak_sweep"], indent=2))
    print(json.dumps(receipt["shift_sweep"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
