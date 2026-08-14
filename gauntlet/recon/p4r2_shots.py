"""Dump a whole-song cut file in song-domain (d) with section labels.

The base measurement for the R2 gearbox revision: R1's shots, where each sits in the
song's own sections, and the per-section rate/spread that the arc-gear bullet is read at.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FPS = 24000 / 1001.0

SECTIONS: list[tuple[str, float, float]] = [
    ("head", 0.00, 36.06),
    ("floor", 36.06, 96.02),
    ("breath", 96.02, 153.52),
    ("trade", 153.52, 232.92),
    ("plateau", 232.92, 294.52),
    ("build", 294.52, 328.02),
    ("fast", 328.02, 381.02),
    ("summit", 381.02, 474.64),
    ("ending", 474.64, 497.664),
]

# The human's own numbers for the same song (styles/concert.md arc-gear table).
HUMAN = {
    "head": 9.98, "floor": 8.01, "breath": 7.30, "trade": 9.07, "plateau": 7.79,
    "build": 8.96, "fast": 14.72, "summit": 10.90, "ending": 2.61,
}


def sec_of(d: float) -> str:
    for name, lo, hi in SECTIONS:
        if lo <= d < hi:
            return name
    return "ending"


def rows(cut: dict[str, Any]) -> tuple[list[dict[str, Any]], float]:
    d = 0.0
    out: list[dict[str, Any]] = []
    for s in cut["segments"]:
        if "gap" in s:
            n, src, a, b = s["gap"], "(gap)", None, None
        else:
            n, src, a, b = s["out"] - s["in"], s["source"], s["in"], s["out"]
        out.append(
            {
                "id": s["id"], "src": src, "frames": n, "sec": round(n / FPS, 3),
                "d": round(d, 3), "section": sec_of(d), "in": a, "out": b,
                "note": s.get("note", ""),
            }
        )
        d += n / FPS
    return out, d


def report(cut: dict[str, Any]) -> None:
    rs, total = rows(cut)
    shots = [r for r in rs if r["src"] != "(gap)"]
    lens = [r["sec"] for r in shots]
    print(f"total {total:.3f}s  segments={len(rs)}  shots={len(shots)}  cuts={len(shots) - 1}")
    print(
        f"song cpm={(len(shots) - 1) / total * 60:.2f}  mean={statistics.fmean(lens):.2f} "
        f"median={statistics.median(lens):.2f} "
        f"cv={statistics.pstdev(lens) / statistics.fmean(lens):.3f} "
        f"sub2s={sum(1 for x in lens if x < 2.0)}"
    )
    for r in rs:
        print(
            f"{r['id']:>5} d={r['d']:7.2f} {r['sec']:6.2f} {r['section']:8} "
            f"{r['src']:9} {r['in']}-{r['out']}"
        )
    print("--- per section ---")
    print(f"{'section':9}{'span':>7}{'shots':>6}{'cpm':>7}{'human':>7}{'mean':>7}{'med':>7}{'cv':>7}{'min':>7}{'max':>7}")
    within = []
    for name, lo, hi in SECTIONS:
        ss = [r for r in shots if r["section"] == name]
        if not ss:
            continue
        length = [r["sec"] for r in ss]
        cv = statistics.pstdev(length) / statistics.fmean(length)
        within.append(cv)
        print(
            f"{name:9}{hi - lo:7.1f}{len(ss):6d}{len(ss) / (hi - lo) * 60:7.2f}"
            f"{HUMAN[name]:7.2f}{statistics.fmean(length):7.2f}"
            f"{statistics.median(length):7.2f}{cv:7.3f}{min(length):7.2f}{max(length):7.2f}"
        )
    print(f"mean within-section CV: {statistics.fmean(within):.3f}  (human 0.692, R1 0.478)")
    drive = [r for r in shots if 330.0 <= r["d"] < 480.0]
    print(f"d330-480: shots={len(drive)} cpm={len(drive) / 150.0 * 60:.2f}  (human 12.0)")
    sub2 = [(r["id"], r["d"], r["sec"], r["section"]) for r in shots if r["sec"] < 2.0]
    print(f"sub-2s: {sub2}")


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "projects" / "mcp-tests-zinc" / "taurus-people-full.cut.json"
    )
    report(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    main()
