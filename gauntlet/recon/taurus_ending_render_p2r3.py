"""Put the round-3 ending timeline on 1080p custom settings, render it, restore Zinc SYNC.

G13: a build inherits the project's 4K default, so the resolution is set on the timeline
before the render rather than after it. The closing check also reports any tail-staging
timeline left in the project -- the hardened round trip (b743b7e) is supposed to leave none.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDERS = HERE.parents[1] / "gauntlet" / "renders"
TIMELINE = "Taurus People Ending P2 R3 v1"
NAME = "taurus_ending_p2r3"

SET_HD = f"""
found = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName() == {TIMELINE!r}:
        found = tl
project.SetCurrentTimeline(found)
before = (found.GetSetting('timelineResolutionWidth'), found.GetSetting('timelineResolutionHeight'))
found.SetSetting('useCustomSettings', '1')
ok_w = found.SetSetting('timelineResolutionWidth', '1920')
ok_h = found.SetSetting('timelineResolutionHeight', '1080')
result = {{'before': before, 'ok_w': ok_w, 'ok_h': ok_h,
           'after': (found.GetSetting('timelineResolutionWidth'),
                     found.GetSetting('timelineResolutionHeight')),
           'end_frame': found.GetEndFrame(), 'start_frame': found.GetStartFrame()}}
"""

BACK = """
found = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName() == 'Zinc SYNC':
        found = tl
ok = project.SetCurrentTimeline(found) if found else None
staging = [project.GetTimelineByIndex(i).GetName()
           for i in range(1, project.GetTimelineCount() + 1)
           if '(tail staging)' in project.GetTimelineByIndex(i).GetName()
           or 'tail-staging' in project.GetTimelineByIndex(i).GetName()]
result = {'set': ok, 'current': project.GetCurrentTimeline().GetName(),
          'staging_left': staging}
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import jobs as job_tools
    from resolve_mcp.tools import render as render_tools

    print("hd:", json.dumps(escape_hatch.run_python(code=SET_HD), default=str)[:700], flush=True)

    job = render_tools.render_timeline(
        preset="YouTube - 1080p",
        timeline=TIMELINE,
        name=NAME,
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

    print("back:", json.dumps(escape_hatch.run_python(code=BACK), default=str)[:400], flush=True)


if __name__ == "__main__":
    main()
