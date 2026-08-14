"""Extra confirming grabs for angle labelling. READ-ONLY."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("grab_probe2.json")
PLAN = [
    ("A014C002_2606170Z.MXF", "Zinc Bar/Footage/FX6/Set 2", [8000, 21000]),
    ("A015C001_2606170J.MXF", "Zinc Bar/Footage/FX6/Set 2", [20000, 50000]),
    ("20260617_D_A7IV_0006.MP4", "Zinc Bar/Footage/A7IV/Set 2", [6000, 33000, 82000]),
]


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import video as video_tools

    report: dict[str, Any] = {}
    for clip, binpath, times in PLAN:
        report[clip] = video_tools.grab_frames(clip=clip, bin=binpath, times=times)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    for clip, g in report.items():
        print(clip, g.get("ok"))
        for f in g.get("frames") or []:
            print("  ", f.get("path"), f.get("time"))


if __name__ == "__main__":
    main()
