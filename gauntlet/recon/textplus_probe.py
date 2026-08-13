"""Can a Text+ template be put in the media pool from the API at all?

The titles schema wants "GUI-authored Text+ templates, already in the media pool"
(T5: every template resolves to exactly one media-pool clip) and this project's pool
holds none. Try the only API route that makes a Text+ anywhere - inserting a Fusion
title onto a timeline - and see whether it produces a media-pool clip we could then
name as a template. Cleans up after itself.
"""

from __future__ import annotations

import json

CODE = """
res = {}
tl = None
for i in range(1, project.GetTimelineCount() + 1):
    t = project.GetTimelineByIndex(i)
    if t.GetName() == 'Taurus People Opening R2 v2':
        tl = t
project.SetCurrentTimeline(tl)
res['timeline'] = tl.GetName()
res['titles_available'] = None
try:
    res['titles_available'] = resolve.GetMediaStorage() is not None
except Exception as exc:
    res['titles_available'] = 'err: %s' % exc

mp = project.GetMediaPool()
before = len(mp.GetRootFolder().GetClipList())
tl.SetCurrentTimecode('01:00:40:00')
inserted = None
try:
    inserted = tl.InsertFusionTitleIntoTimeline('Text+')
except Exception as exc:
    inserted = 'err: %s' % exc
res['inserted'] = str(inserted)

item = None
if inserted and not isinstance(inserted, str):
    item = inserted
    res['item_name'] = item.GetName()
    mpi = item.GetMediaPoolItem()
    res['media_pool_item'] = None if mpi is None else mpi.GetName()
    try:
        comp = item.GetFusionCompByIndex(1)
        res['fusion_comp'] = None if comp is None else str(comp.GetToolList())
    except Exception as exc:
        res['fusion_comp'] = 'err: %s' % exc
    # remove it again
    try:
        res['deleted'] = tl.DeleteClips([item])
    except Exception as exc:
        res['deleted'] = 'err: %s' % exc
res['root_clips_before'] = before
res['root_clips_after'] = len(mp.GetRootFolder().GetClipList())
result = res
"""

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

    print(json.dumps(escape_hatch.run_python(code=CODE), default=str, indent=1)[:3000], flush=True)
    print(json.dumps(escape_hatch.run_python(code=BACK), default=str)[:200], flush=True)


if __name__ == "__main__":
    main()
