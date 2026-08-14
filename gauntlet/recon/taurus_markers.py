"""Mark the two uncertainties on the built opening, then leave Zinc SYNC current."""

from __future__ import annotations

import json

TIMELINE = "Taurus People Opening v3"
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
                "frame": START + 921,
                "color": "Yellow",
                "name": "front change unverified",
                "note": (
                    "The change at +38.4 s reads as the sax finishing the head and a "
                    "bass-forward solo starting: the 180-1600 Hz band drops 14 dB while "
                    "the low band holds, and the FX6 pans off the sax onto piano+bass. "
                    "separate_stems died with its launching process, so "
                    "analyze_structure(solos=true) could not run - who is out front here "
                    "is inferred from band energy plus framing, not measured."
                ),
            },
            {
                "frame": START + 550,
                "color": "Yellow",
                "name": "cut sits 43 ms off the transient",
                "note": (
                    "2 ms outside the corpus's 17-41 ms band. The neighbouring frames give "
                    "+1 ms (on the transient - the fourth-wall risk) and +85 ms, so 43 ms "
                    "is the nearest non-on option at this onset's sub-frame phase."
                ),
            },
        ],
    )
    print(json.dumps(res, default=str)[:900], flush=True)
    print(json.dumps(escape_hatch.run_python(code=BACK), default=str)[:300], flush=True)


if __name__ == "__main__":
    main()
