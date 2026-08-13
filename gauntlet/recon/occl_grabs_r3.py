"""Round-3 visual adjudication of the occlusion tool's flagged windows. READ-ONLY.

The tool flags most of the FX6 back half. Before vetoing 23 s of the piece on a
score, look at frames inside each window and decide near-field blocking vs false
positive (dark piano lid, stage shadow).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occl_grabs_r3.json")
FPS = 24000.0 / 1001.0
FX6_BASE = 54383  # clip frame at piece t=0
A7_BASE = 85653

FX6_T = [13.5, 16.5, 44.5, 47.5, 53.5, 57.5, 59.5, 68.5, 73.0, 78.0, 83.0, 88.0]
A7_T = [50.0, 62.0, 72.0, 85.0]


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import video as video_tools

    report: dict[str, Any] = {}
    plan = [
        ("A015C001_2606170J.MXF", "Zinc Bar/Footage/FX6/Set 2", FX6_BASE, FX6_T),
        ("20260617_D_A7IV_0006.MP4", "Zinc Bar/Footage/A7IV/Set 2", A7_BASE, A7_T),
    ]
    for clip, binpath, base, times in plan:
        fr = [base + round(t * FPS) for t in times]
        g = video_tools.grab_frames(clip=clip, bin=binpath, times=fr)
        report[clip] = {"piece_t": times, "frames": fr, "grab": g}
        print(clip, g.get("ok"), flush=True)
        for t, f in zip(times, g.get("frames") or [], strict=False):
            print(f"  t={t:6.2f}  {f.get('path')}", flush=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
