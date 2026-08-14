"""Closing state check: which timelines exist, which is current, and the R2 build's shape."""

from __future__ import annotations

import json

CHECK = """
names = [project.GetTimelineByIndex(i).GetName()
         for i in range(1, project.GetTimelineCount() + 1)]
r2 = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName() == 'Taurus People Full P4 R2 v3':
        r2 = {'name': tl.GetName(),
              'start': tl.GetStartFrame(), 'end': tl.GetEndFrame(),
              'video_tracks': tl.GetTrackCount('video'),
              'audio_tracks': tl.GetTrackCount('audio'),
              'width': tl.GetSetting('timelineResolutionWidth'),
              'height': tl.GetSetting('timelineResolutionHeight'),
              'v1_items': len(tl.GetItemListInTrack('video', 1) or []),
              'v2_items': len(tl.GetItemListInTrack('video', 2) or [])}
result = {'timelines': names,
          'current': project.GetCurrentTimeline().GetName(),
          'set2_main_present': 'Zinc - Set 2 Main' in names,
          'r2': r2}
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch

    print(json.dumps(escape_hatch.run_python(code=CHECK), indent=1, default=str)[:2000], flush=True)


if __name__ == "__main__":
    main()
