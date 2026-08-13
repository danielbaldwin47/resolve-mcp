"""READ-ONLY frame grabs for angle labelling on 'Zinc SYNC'. Writes grab_probe.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("grab_probe.json")

PLAN = [
    ("A014C002_2606170Z.MXF", "Zinc Bar/Footage/FX6/Set 2", [3000, 15000, 27000]),
    ("A015C001_2606170J.MXF", "Zinc Bar/Footage/FX6/Set 2", [5000, 35000, 65000]),
    (
        "20260617_D_A7IV_0006.MP4",
        "Zinc Bar/Footage/A7IV/Set 2",
        [2000, 20000, 45000, 70000, 95000],
    ),
]


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import media as media_tools
    from resolve_mcp.tools import video as video_tools

    report: dict[str, Any] = {}
    for clip, binpath, times in PLAN:
        entry: dict[str, Any] = {}
        entry["inspect"] = media_tools.inspect_clip(clip=clip, bin=binpath)
        entry["grab"] = video_tools.grab_frames(clip=clip, bin=binpath, times=times)
        report[clip] = entry
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
