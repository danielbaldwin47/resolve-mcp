"""How long the sax player stays parked in the A7IV's left foreground. READ-ONLY.

After the crossing (t 62.8-65.4) he does not leave: at t=66.0 his back stands in the left third
with the pianist behind it, and by the control frame at t=76.99 he is back at his mic, side-on
and clean. The scan reads 0.000 for that whole stretch — a static body at the frame edge is
invisible to it, which is the same blindness that made it under-call the crossing — so the only
way to bound the stretch is to look.

A7IV clip = 89649 + round(t * 23.976); t = seconds into the window.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occl_mid_grabs3.json")
FPS = 24000.0 / 1001.0
A7_BASE = 89649

A7_T = [67.0, 70.0, 73.0]


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import video as video_tools

    wanted = [A7_BASE + round(t * FPS) for t in A7_T]
    got = video_tools.grab_frames(
        clip="20260617_D_A7IV_0006.MP4", bin="Zinc Bar/Footage/A7IV/Set 2", times=wanted
    )
    report: dict[str, Any] = {"window_t": A7_T, "frames": wanted, "grab": got}
    print("A7IV", got.get("ok"), got.get("error"), flush=True)
    for t, one in zip(A7_T, got.get("frames") or [], strict=False):
        print(f"  t={t:6.2f}  {one.get('path')}", flush=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
