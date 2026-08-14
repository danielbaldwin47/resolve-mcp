"""Frames for the ending-window occlusion adjudication. READ-ONLY.

Two frames inside each window analyze_occlusion flagged, plus one a second before it, because
the round-3 ledger's own test for a true blocking is that it MOVES: a frame either side of the
flag is what separates a body crossing the near field from the piano lid, which is in every
frame of the take and moves in none of them. Two FX6 controls go with them — the scan flagged
nothing on the FX6 here, and a clean frame is what makes that readable as clear rather than
as the scan having given up.

A7IV base 95427, FX6 base 64157 (clip frame = base + round(t * 23.976), t = seconds into the
ending window, SYNC 181733).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occl_ending_grabs.json")
FPS = 24000.0 / 1001.0
A7_BASE = 95427
FX6_BASE = 64157

A7_T = [3.96, 4.96, 5.46, 6.97, 7.97, 8.47, 14.97, 15.97, 16.47, 39.0, 40.0, 40.5]
FX6_T = [16.0, 45.0, 84.0]


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
