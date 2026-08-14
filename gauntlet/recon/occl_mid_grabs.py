"""Frames for the mid-window occlusion adjudication. READ-ONLY.

The ledger's test for a true blocking is that it MOVES, so every flagged window gets frames
inside it plus a control outside it.

A7IV: three one-second flags (63.98, 77.99, 86.96 s into the window, peaks 0.416-0.472) against
a baseline of 0.0 and 87 samples at ~0.085. Two frames inside each, one a second before.

FX6: one 42 s flag — samples sit at 0.50-0.69 from t=0 to t=41.96 and then fall to 0.000 from
t=42.96 to the end of the window. That is NOT the piano-lid signature the ledger warns about
(furniture is in every frame of the take and would flag the whole 90 s); something is there for
the first 42 s and gone after. Frames are spread across the flagged stretch, at its two highest
samples, on both sides of the 42 s edge, and deep into the clean half.

A7IV clip = 89649 + round(t * 23.976), FX6 clip = 58379 + round(t * 23.976); t = seconds into
the window (SYNC 175955, mix 3735.16, deliverable 166.68).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occl_mid_grabs.json")
FPS = 24000.0 / 1001.0
A7_BASE = 89649
FX6_BASE = 58379

A7_T = [62.98, 63.98, 64.48, 76.99, 77.99, 78.49, 85.96, 86.96, 87.46]
FX6_T = [0.0, 8.97, 22.98, 30.99, 41.0, 42.96, 45.0, 60.0, 85.0]


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import video as video_tools

    report: dict[str, Any] = {}
    plan = [
        ("20260617_D_A7IV_0006.MP4", "Zinc Bar/Footage/A7IV/Set 2", A7_BASE, A7_T),
        ("A015C001_2606170J.MXF", "Zinc Bar/Footage/FX6/Set 2", FX6_BASE, FX6_T),
    ]
    for clip, binpath, base, times in plan:
        wanted = [base + round(t * FPS) for t in times]
        got = video_tools.grab_frames(clip=clip, bin=binpath, times=wanted)
        report[clip] = {"window_t": times, "frames": wanted, "grab": got}
        print(clip, got.get("ok"), got.get("error"), flush=True)
        for t, one in zip(times, got.get("frames") or [], strict=False):
            print(f"  t={t:6.2f}  {one.get('path')}", flush=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
