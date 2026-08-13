"""Read-only: dump V1/V2 items of every 'Taurus People Opening*' timeline."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "r1_items.json"

CODE = """
out = {'current': project.GetCurrentTimeline().GetName(), 'timelines': []}
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    nm = tl.GetName()
    if not nm.startswith('Taurus People Opening'):
        continue
    rec = {'name': nm,
           'start': tl.GetStartFrame(),
           'end': tl.GetEndFrame(),
           'vtracks': tl.GetTrackCount('video'),
           'atracks': tl.GetTrackCount('audio'),
           'items': []}
    for tr in range(1, tl.GetTrackCount('video') + 1):
        for it in (tl.GetItemListInTrack('video', tr) or []):
            mpi = it.GetMediaPoolItem()
            props = {}
            if mpi:
                try:
                    props = {k: mpi.GetClipProperty(k) for k in
                             ('File Name', 'Clip Name', 'Start', 'End', 'Frames', 'Start TC', 'File Path')}
                except Exception as exc:
                    props = {'err': str(exc)}
            rec['items'].append({
                'track': tr,
                'name': it.GetName(),
                'rec_start': it.GetStart(),
                'rec_end': it.GetEnd(),
                'dur': it.GetDuration(),
                'src_in': it.GetLeftOffset(),
                'src_out': it.GetRightOffset(),
                'mpi_id': (mpi.GetUniqueId() if mpi else None),
                'props': props,
            })
    out['timelines'].append(rec)
result = out
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch

    res = escape_hatch.run_python(code=CODE)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
