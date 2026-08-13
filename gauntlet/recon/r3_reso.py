"""Compare the R2 and R3 timelines' resolution settings; optionally set R3 to 1080p.

The R3 render came out 3840x2160 from the same "YouTube - 1080p" preset that gave R2
1920x1080, so the difference is the timeline, not the preset. Usage:
    python r3_reso.py        -> report only
    python r3_reso.py set    -> set R3 v3 to 1920x1080, then report again
"""

from __future__ import annotations

import json
import sys

REPORT = """
out = {}
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    n = tl.GetName()
    if n.startswith('Taurus People Opening'):
        out[n] = {
            'w': tl.GetSetting('timelineResolutionWidth'),
            'h': tl.GetSetting('timelineResolutionHeight'),
            'custom': tl.GetSetting('useCustomSettings'),
        }
result = out
"""

SET = """
found = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName() == 'Taurus People Opening R3 v3':
        found = tl
steps = {'custom': found.SetSetting('useCustomSettings', '1')}
steps['w'] = found.SetSetting('timelineResolutionWidth', '1920')
steps['h'] = found.SetSetting('timelineResolutionHeight', '1080')
steps['now'] = [found.GetSetting('timelineResolutionWidth'), found.GetSetting('timelineResolutionHeight')]
result = steps
"""

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
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch

    print("BEFORE:", json.dumps(escape_hatch.run_python(code=REPORT), default=str)[:1200], flush=True)
    if len(sys.argv) > 1 and sys.argv[1] == "set":
        print("SET:", json.dumps(escape_hatch.run_python(code=SET), default=str)[:800], flush=True)
        print("AFTER:", json.dumps(escape_hatch.run_python(code=REPORT), default=str)[:1200], flush=True)
    print("back:", json.dumps(escape_hatch.run_python(code=BACK), default=str)[:300], flush=True)


if __name__ == "__main__":
    main()
