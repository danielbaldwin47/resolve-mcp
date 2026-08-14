import json
from pathlib import Path

d = json.loads(Path(__file__).with_name("live_probe.json").read_text(encoding="utf-8"))
print("ATTACH", d["attach"]["ok"], "| product", d["project"].get("product"))
print("CTX", json.dumps(d["project"].get("context")))
pr = d["project"].get("projects_in_db", {})
print("PROJECTS", json.dumps(pr.get("projects"))[:600])
tl = d["timelines"]
print("TL ok", tl.get("ok"), "count", tl.get("count"), "current", tl.get("current"))
for t in tl.get("timelines", []):
    print(
        "  -",
        repr(t.get("name")),
        "fps",
        t.get("fps"),
        "dur",
        (t.get("duration") or {}).get("timecode"),
        (t.get("duration") or {}).get("frames"),
        "tracks",
        t.get("tracks"),
        "v",
        t.get("version"),
    )
print("LATEST", json.dumps(tl.get("latest_versions")))
for name, e in d["target_timelines"].items():
    print("==", name)
    s = e.get("summary")
    if not s:
        print("   inspect error:", json.dumps(e.get("inspect", {}).get("error"))[:400])
    else:
        print("   ", json.dumps(s))
    print("   markers:", json.dumps(e.get("markers")))
mp = d["media_pool"]
print("POOL root", mp.get("root_name"), "root clips", mp.get("clips_at_root"), "total", mp.get("total_clips"))
for b in mp.get("top_level_bins", []):
    print("  bin", repr(b["name"]), "direct", b["clips_direct"], "rec", b["clips_recursive"], "subs", b["subfolders"])
print("ALL BINS", json.dumps(mp.get("all_bin_paths"))[:1500])
print("ERRORS", len(d["errors"]))
