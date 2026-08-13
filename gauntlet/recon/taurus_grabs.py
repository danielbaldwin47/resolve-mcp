"""Verify the Zinc SYNC record->source mapping over the Taurus window, then grab
frames on both cameras at the moments the audio analysis nominated. READ-ONLY."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("taurus_grabs.json")
FPS = 24000.0 / 1001.0
REC_IN = 171959  # deliverable span start on Zinc SYNC

MOMENTS = [1.0, 2.6, 6.0, 12.0, 20.0, 28.0, 36.0, 40.0, 48.0, 58.0, 70.0, 84.0]


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import timeline as timeline_tools
    from resolve_mcp.tools import video as video_tools

    report: dict[str, Any] = {}
    insp = timeline_tools.inspect_timeline(
        timeline="Zinc SYNC", detail="clips", start=REC_IN, end=REC_IN + 2158
    )
    report["raw_inspect"] = insp
    items = []
    for track in insp.get("tracks", []) if isinstance(insp, dict) else []:
        for it in track.get("clips", []) or track.get("items", []) or []:
            rec = it.get("record") or {}
            src = it.get("source") or {}

            def fr(block: dict, *keys: str) -> Any:
                for k in keys:
                    v = block.get(k)
                    if isinstance(v, dict):
                        return v.get("frames")
                    if v is not None:
                        return v
                return None

            items.append(
                {
                    "track": track.get("name") or track.get("index"),
                    "name": it.get("name"),
                    "record_in": fr(rec, "start", "in"),
                    "record_out": fr(rec, "end", "out"),
                    "source_in": fr(src, "start", "in"),
                    "source_out": fr(src, "end", "out"),
                    "sync_offset": (it.get("sync_offset") or {}).get("frames")
                    if isinstance(it.get("sync_offset"), dict)
                    else it.get("sync_offset"),
                }
            )
    report["items"] = items
    print(json.dumps(items, indent=1, default=str)[:3000])
    report["inspect_keys"] = list(insp) if isinstance(insp, dict) else None

    # mapping: pick the item covering REC_IN on each track
    mapping = {}
    for it in items:
        try:
            ri, ro = int(it["record_in"]), int(it["record_out"])
        except (TypeError, ValueError):
            continue
        if ri <= REC_IN < ro:
            mapping[str(it["track"])] = {
                "clip": it["name"],
                "zero_frame": ri - int(it["source_in"]),
            }
    report["mapping"] = mapping
    print(json.dumps(mapping, indent=1))

    plan = {
        "A015C001_2606170J.MXF": ("Zinc Bar/Footage/FX6/Set 2", None),
        "20260617_D_A7IV_0006.MP4": ("Zinc Bar/Footage/A7IV/Set 2", None),
    }
    zero = {
        "A015C001_2606170J.MXF": None,
        "20260617_D_A7IV_0006.MP4": None,
    }
    for m in mapping.values():
        if m["clip"] in zero:
            zero[m["clip"]] = m["zero_frame"]

    report["grabs"] = {}
    for clip, (binpath, _) in plan.items():
        z = zero[clip]
        if z is None:
            report["grabs"][clip] = {"skipped": "no mapping"}
            continue
        times = [REC_IN + round(t * FPS) - z for t in MOMENTS]
        res = video_tools.grab_frames(clip=clip, bin=binpath, times=times)
        report["grabs"][clip] = {"zero_frame": z, "times": times, "result": res}
        print(clip, "zero", z, res.get("ok"))
        for rel, f in zip(MOMENTS, res.get("frames") or [], strict=False):
            print(f"  +{rel:5.1f}s {f.get('path')}")

    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT)


if __name__ == "__main__":
    main()
