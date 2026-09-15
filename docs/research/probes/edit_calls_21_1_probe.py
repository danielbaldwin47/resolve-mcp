"""PROTOTYPE — throwaway probe for wayfinder ticket #268. Not server code; never imported.

Probes the Resolve 21.1 edit calls (AddTransition, Get/SetFades, SetSpeed, the selection
getters) on scratch duplicates of "Taurus People Opening v1" in project mcp-tests-zinc.

Run: paste into the native "DaVinci Resolve" MCP server's `run_script_unsafe` (it needs
file access for the OTIO export; `resolve` and `project` are pre-injected), timeout 60.
Refuses to run against any project but mcp-tests-zinc. Findings: edit-calls-21-1.md.
Creates timelines "PROBE 268 edit calls", "PROBE 268 tail dissolve", "PROBE 268 tail fader";
delete them by hand (switch away first, delete in a later pass).
"""

import json
import os
import tempfile

assert project.GetName() == "mcp-tests-zinc", "scratch project only"  # noqa: F821
SOURCE = "Taurus People Opening v1"
OUT = tempfile.gettempdir()


def find(name):
    for i in range(int(project.GetTimelineCount())):  # noqa: F821
        t = project.GetTimelineByIndex(i + 1)  # noqa: F821
        if t.GetName() == name:
            return t
    return None


def clips(tl):
    return [x for x in tl.GetItemListInTrack("video", 1) if x.GetType() != "transition"]


def at(tl, start):
    return next(x for x in clips(tl) if x.GetStart() == start)


def dissolve(item, position, alignment, duration=12, kind="Cross Dissolve", category="simple"):
    opts = {"type": kind, "category": category, "position": position, "duration": duration}
    if alignment:
        opts["alignment"] = alignment
    r = item.AddTransition(opts)
    return (r.GetName(), r.GetType(), r.GetStart(), r.GetEnd()) if r else None


def fresh(name):
    assert find(name) is None, f"{name} exists — delete it by hand first"
    return find(SOURCE).DuplicateTimeline(name)


report = {}

# 1. Selection getters: GetTimelineClipSelection is not an API name (attr resolves to None).
kitchen = fresh("PROBE 268 edit calls")
project.SetCurrentTimeline(kitchen)  # noqa: F821
report["selection"] = {
    "GetTimelineClipSelection_is_None": getattr(kitchen, "GetTimelineClipSelection") is None,
    "Timeline.GetSelectedClips": repr(kitchen.GetSelectedClips()),
    "MediaPool.GetSelectedClips": repr(project.GetMediaPool().GetSelectedClips()),  # noqa: F821
}

# 2. AddTransition at a hard cut: no alignment -> None; with alignment -> lands, centred.
report["cut_no_alignment"] = dissolve(at(kitchen, 86678), "end", None)
report["cut_center"] = dissolve(at(kitchen, 86678), "end", "center")
# 3. Gap edge (12 f leading gap) and the timeline tail.
report["gap_center"] = dissolve(at(kitchen, 86412), "start", "center")
report["tail_left"] = dissolve(at(kitchen, 88223), "end", "left")
# 4. Fades on the mix audio and a video item.
aud = kitchen.GetItemListInTrack("audio", 1)[0]
report["fades"] = {
    "audio": (aud.SetFades({"FadeOut": 12}), aud.GetFades()),
    "video": (at(kitchen, 86519).SetFades({"FadeIn": 6}), at(kitchen, 86519).GetFades()),
}
# 5. Speed without and with ripple.
end0 = kitchen.GetEndFrame()
report["speed_50_noripple"] = (at(kitchen, 86950).SetSpeed({"Percentage": 50.0}), end0, kitchen.GetEndFrame())
report["speed_200_ripple"] = (
    at(kitchen, 87674).SetSpeed({"Percentage": 200.0, "RippleTimeline": True}),
    end0,
    kitchen.GetEndFrame(),
)
# 6. The ripple above cuts the mix track too: record the split, then fade the last piece.
mix = kitchen.GetItemListInTrack("audio", 1)
report["mix_after_ripple"] = [(x.GetStart(), x.GetEnd(), x.GetLeftOffset()) for x in mix]
report["audio_xfade"] = dissolve(mix[-1], "end", "left", kind="Cross Fade 0 dB", category="audio")
report["audio_simple_dissolve"] = dissolve(mix[0], "start", "right")  # None: wrong category

# 7. The two tail routes, clean, for the eye in the UI and the OTIO comparison.
a = fresh("PROBE 268 tail dissolve")
report["A_tail_dissolve"] = (dissolve(clips(a)[-1], "end", "left"), a.GetItemListInTrack("audio", 1)[0].SetFades({"FadeOut": 12}))
b = fresh("PROBE 268 tail fader")
report["B_tail_fader"] = (clips(b)[-1].SetFades({"FadeOut": 12}), b.GetItemListInTrack("audio", 1)[0].SetFades({"FadeOut": 12}))

# 8. OTIO export: does native output match what cut/otio.inject writes?
exports = {}
for tl in (kitchen, a, b):
    path = os.path.join(OUT, tl.GetName().replace(" ", "_") + ".otio")
    tl.Export(path, resolve.EXPORT_OTIO)  # noqa: F821
    doc = json.load(open(path, encoding="utf-8"))
    exports[tl.GetName()] = [
        (
            tr.get("kind"),
            [
                (k.get("name"), k.get("transition_type"), k["in_offset"]["value"], k["out_offset"]["value"])
                for k in tr.get("children", [])
                if "Transition" in k.get("OTIO_SCHEMA", "")
            ],
        )
        for tr in doc["tracks"]["children"]
    ]
report["otio"] = exports
result = report
