"""Edge frames for the one TRUE blocking in the mid window. READ-ONLY.

The first pass (occl_mid_grabs.py) found that the A7IV flag at t=63.98 is not the drummer: the
sax player walks right-to-left through the near field, and at 63.98 his out-of-focus back covers
the kit and the bassist. The scan samples once a second and scored 0.093 / 0.416 / 0.087 across
it, so the tool's own numbers do not bound the crossing — only frames do. These are the half
seconds either side of the two samples that read clean, which is what turns "somewhere in this
second" into an in/out a cutter can use.

A7IV clip = 89649 + round(t * 23.976); t = seconds into the window (SYNC 175955, mix 3735.16,
deliverable 166.68).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occl_mid_grabs2.json")
FPS = 24000.0 / 1001.0
A7_BASE = 89649

A7_T = [61.5, 62.0, 62.5, 63.5, 64.7, 65.0, 65.4, 66.0]


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
