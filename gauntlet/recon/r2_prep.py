"""Put the built R2 timeline on 1080p custom settings and mark the song.

The titles file anchors every event to a blue marker by name (titles schema section 2),
and build_timeline found no markers to carry, so the marker is written here. Frame
86400 is the timeline start, which is window t=0.
"""

from __future__ import annotations

import json

TIMELINE = "Taurus People Opening R2 v3"
START = 86400

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
           'fps': found.GetSetting('timelineFrameRate')}}
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import timeline as timeline_tools

    print("hd:", json.dumps(escape_hatch.run_python(code=SET_HD), default=str)[:700], flush=True)

    res = timeline_tools.set_markers(
        timeline=TIMELINE,
        markers=[
            {
                "frame": START,
                "color": "Blue",
                "name": "taurus-people",
                "note": "song anchor for projects/mcp-tests-zinc/taurus-people.titles.json",
            }
        ],
    )
    print("marker:", json.dumps(res, default=str)[:700], flush=True)
    print(
        "markers now:",
        json.dumps(timeline_tools.list_markers(timeline=TIMELINE), default=str)[:700],
        flush=True,
    )


if __name__ == "__main__":
    main()
