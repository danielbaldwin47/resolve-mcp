"""Round-3: adjudicate the 38-45 s handover, where both angles are compromised.

A7IV is confirmed blocked 41.96-42.92; FX6 is reframing 41.08-44.25 after the sax
walks off. Look at what each camera actually shows through those seconds before
deciding where the shot boundary goes. READ-ONLY.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occl_grabs_r3b.json")
FPS = 24000.0 / 1001.0
FX6_BASE = 54383
A7_BASE = 85653

FX6_T = [37.0, 38.5, 39.5, 40.5, 41.5, 42.5, 43.5, 44.3]
A7_T = [41.5, 43.2, 43.9]


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import video as video_tools

    report: dict[str, Any] = {}
    for clip, binpath, base, times in [
        ("A015C001_2606170J.MXF", "Zinc Bar/Footage/FX6/Set 2", FX6_BASE, FX6_T),
        ("20260617_D_A7IV_0006.MP4", "Zinc Bar/Footage/A7IV/Set 2", A7_BASE, A7_T),
    ]:
        fr = [base + round(t * FPS) for t in times]
        g = video_tools.grab_frames(clip=clip, bin=binpath, times=fr)
        report[clip] = {"piece_t": times, "frames": fr, "grab": g}
        print(clip, g.get("ok"), flush=True)
        for t, f in zip(times, g.get("frames") or [], strict=False):
            print(f"  t={t:6.2f}  {f.get('path')}", flush=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
