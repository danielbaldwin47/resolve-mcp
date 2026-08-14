"""Closing state check: which timeline is current, what the piece-3 build left behind.

Proves 'Zinc SYNC' is current and that 'Zinc - Set 2 Main' still has the item count and
duration it had before this session touched the project (it was never opened or edited).
"""

from __future__ import annotations

import json

CODE = """
names = [project.GetTimelineByIndex(i).GetName()
         for i in range(1, project.GetTimelineCount() + 1)]
main = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName() == 'Zinc - Set 2 Main':
        main = {'name': tl.GetName(), 'start': tl.GetStartFrame(), 'end': tl.GetEndFrame(),
                'v1_items': len(tl.GetItemListInTrack('video', 1) or [])}
mid = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName() == 'Taurus People Mid P3 R1 v1':
        items = tl.GetItemListInTrack('video', 1) or []
        mid = {'name': tl.GetName(), 'start': tl.GetStartFrame(), 'end': tl.GetEndFrame(),
               'v1_items': len(items),
               'first': items[0].GetName() if items else None,
               'last': items[-1].GetName() if items else None,
               'width': tl.GetSetting('timelineResolutionWidth'),
               'height': tl.GetSetting('timelineResolutionHeight')}
result = {'current': project.GetCurrentTimeline().GetName(), 'timelines': names,
          'set2_main': main, 'mid_piece': mid}
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch

    out = escape_hatch.run_python(code=CODE)
    print(json.dumps(out, indent=1, default=str)[:2500], flush=True)


if __name__ == "__main__":
    main()
