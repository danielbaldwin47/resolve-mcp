"""Render the built R3 timeline to gauntlet/renders, then leave Zinc SYNC current.

Usage: python r3_render.py <output_name>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDERS = HERE.parents[1] / "gauntlet" / "renders"
TIMELINE = "Taurus People Opening R3 v3"

BACK = """
found = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName() == 'Zinc SYNC':
        found = tl
ok = project.SetCurrentTimeline(found) if found else None
result = {'set': ok, 'current': project.GetCurrentTimeline().GetName()}
"""


def main() -> None:
    name = sys.argv[1]
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import jobs as job_tools
    from resolve_mcp.tools import render as render_tools

    job = render_tools.render_timeline(
        preset="YouTube - 1080p",
        timeline=TIMELINE,
        name=name,
        target_dir=str(RENDERS),
        refresh=True,
    )["job"]
    last = ""
    while True:
        j = job_tools.get_job(job["job_id"])["job"]
        step = f"{j.get('state')} {j.get('step')} {j.get('progress')}"
        if step != last:
            print(" ", step, flush=True)
            last = step
        if j.get("state") in ("completed", "failed"):
            print(json.dumps(j.get("result") or j, indent=1, default=str)[:1500], flush=True)
            break
        time.sleep(4.0)

    print("back:", json.dumps(escape_hatch.run_python(code=BACK), default=str)[:300], flush=True)


if __name__ == "__main__":
    main()
