"""Set the song anchor on the whole-song timeline, then leave Zinc SYNC current.

Titles are positioned as offsets from a blue marker named for the song key (titles/schema.py
sec 2), so the anchor sits on frame 0 of the cut - the first frame of the title card.
"""

from __future__ import annotations

import json

TIMELINE = "Taurus People Full P4 R1 v2"
START = 86400

BACK = """
found = None
for i in range(1, project.GetTimelineCount() + 1):
    t = project.GetTimelineByIndex(i)
    if t.GetName() == 'Zinc SYNC':
        found = t
project.SetCurrentTimeline(found)
result = project.GetCurrentTimeline().GetName()
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import timeline as timeline_tools

    res = timeline_tools.set_markers(
        timeline=TIMELINE,
        markers=[
            {
                "frame": START,
                "color": "Blue",
                "name": "taurus-people",
                "note": "Song anchor for taurus-people.titles.json "
                        "(card at +0, personnel at +495).",
            }
        ],
    )
    print("MARKERS:", json.dumps(res, default=str)[:900], flush=True)
    print("back:", json.dumps(escape_hatch.run_python(code=BACK), default=str)[:300], flush=True)


if __name__ == "__main__":
    main()
