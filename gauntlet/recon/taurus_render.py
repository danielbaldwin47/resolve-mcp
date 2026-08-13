"""Render the built Taurus opening, waiting on the job in-process."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RENDERS = HERE.parents[1] / "gauntlet" / "renders"
TIMELINE = "Taurus People Opening v3"
NAME = "taurus_opening_r1"


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import jobs as job_tools
    from resolve_mcp.tools import render as render_tools

    presets = render_tools.list_render_presets()
    print("current:", presets.get("current"))
    names = presets.get("presets")
    print("presets:", json.dumps(names, default=str)[:1500])
    if "--list" in sys.argv:
        return

    RENDERS.mkdir(parents=True, exist_ok=True)
    preset = sys.argv[1] if len(sys.argv) > 1 else None
    job = render_tools.render_timeline(
        preset=preset,
        timeline=TIMELINE,
        name=NAME,
        target_dir=str(RENDERS),
        refresh=True,
    )["job"]
    print("job", job.get("job_id"), "preset params", job.get("params"), flush=True)

    last = ""
    result: dict[str, Any] = {}
    while True:
        j = job_tools.get_job(job["job_id"])["job"]
        step = f"{j.get('state')} {j.get('step')} {j.get('progress')}"
        if step != last:
            print(" ", step, flush=True)
            last = step
        if j.get("state") in ("completed", "failed"):
            result = j
            break
        time.sleep(4.0)
    print(json.dumps(result, indent=1, default=str)[:2500], flush=True)

    # leave Zinc SYNC as the current timeline (no tool owns this; escape hatch)
    back = escape_hatch.run_python(
        code=(
            "pool = project.GetMediaPool()\n"
            "found = None\n"
            "for i in range(1, project.GetTimelineCount() + 1):\n"
            "    tl = project.GetTimelineByIndex(i)\n"
            "    if tl.GetName() == 'Zinc SYNC':\n"
            "        found = tl\n"
            "ok = project.SetCurrentTimeline(found) if found else None\n"
            "result = {'set': ok, 'current': project.GetCurrentTimeline().GetName()}\n"
        )
    )
    print("current timeline now:", json.dumps(back, default=str)[:500], flush=True)


if __name__ == "__main__":
    main()
