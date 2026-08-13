"""READ-ONLY probe of 'Zinc SYNC' video items for angle labelling.

No project switch, no timeline switch (make_current=False), no writes to Resolve.
Writes findings to angle_probe.json next to this file.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("angle_probe.json")
TIMELINE = "Zinc SYNC"

report: dict[str, Any] = {"errors": [], "items": [], "clips": {}, "raw_items": []}


def note(where: str, exc: BaseException) -> None:
    report["errors"].append(
        {"where": where, "type": type(exc).__name__, "message": str(exc),
         "traceback": traceback.format_exc(limit=6)}
    )


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()

    from resolve_mcp.resolve.connection import get_connection
    from resolve_mcp.tools import media as media_tools
    from resolve_mcp.tools import timeline as timeline_tools

    inspected = timeline_tools.inspect_timeline(
        timeline=TIMELINE, detail="clips", limit=200, make_current=False
    )
    report["inspect_ok"] = inspected.get("ok")
    report["items"] = inspected.get("items") or inspected.get("clips") or []
    report["inspect_keys"] = sorted(inspected.keys())
    report["spilled_to"] = inspected.get("spilled_to")

    # raw walk: multicam detection needs the media pool item behind each timeline item
    try:
        connection = get_connection()
        resolve = connection.handle()
        pm = resolve.GetProjectManager()
        proj = pm.GetCurrentProject()
        tl = None
        for i in range(1, int(proj.GetTimelineCount()) + 1):
            cand = proj.GetTimelineByIndex(i)
            if cand and cand.GetName() == TIMELINE:
                tl = cand
                break
        if tl is None:
            raise RuntimeError("timeline not found")
        for track in range(1, int(tl.GetTrackCount("video")) + 1):
            for item in tl.GetItemListInTrack("video", track) or []:
                mpi = item.GetMediaPoolItem()
                entry: dict[str, Any] = {
                    "track": track,
                    "track_name": tl.GetTrackName("video", track),
                    "item_name": item.GetName(),
                    "record_start": item.GetStart(),
                    "record_end": item.GetEnd(),
                    "duration": item.GetDuration(),
                    "left_offset": item.GetLeftOffset(),
                    "source_start": item.GetSourceStartFrame(),
                    "source_end": item.GetSourceEndFrame(),
                }
                try:
                    entry["is_multicam_flag"] = item.GetProperty("MultiCam")
                except Exception:  # noqa: BLE001
                    entry["is_multicam_flag"] = "n/a"
                if mpi:
                    props = {}
                    for key in (
                        "File Path", "Clip Name", "Type", "Format", "Resolution",
                        "FPS", "Frames", "Angle", "Clip Color", "Camera #",
                        "Start TC", "End TC", "Duration",
                    ):
                        try:
                            props[key] = mpi.GetClipProperty(key)
                        except Exception:  # noqa: BLE001
                            props[key] = None
                    entry["media_pool_item"] = {"name": mpi.GetName(), "props": props}
                    # multicam clips expose their angle list via GetClipProperty("Angle")
                    # or via the full property dict; dump the full dict for the first item
                    if len(report["raw_items"]) < 4:
                        try:
                            full = mpi.GetClipProperty()
                            entry["all_props"] = {
                                k: v for k, v in dict(full).items() if v not in ("", None)
                            }
                        except Exception as exc:  # noqa: BLE001
                            note("GetClipProperty() full", exc)
                else:
                    entry["media_pool_item"] = None
                report["raw_items"].append(entry)
    except Exception as exc:  # noqa: BLE001
        note("raw walk", exc)

    # media listing for the two footage bins
    for binpath in ("Zinc Bar/Footage/FX6/Set 2", "Zinc Bar/Footage/A7IV/Set 2",
                    "Zinc Bar/Footage", "Zinc Bar/Timelines"):
        try:
            report["clips"][binpath] = media_tools.list_media(bin=binpath, limit=100,
                                                              recursive=True)
        except Exception as exc:  # noqa: BLE001
            note(f"list_media({binpath})", exc)

    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}; errors={len(report['errors'])}")


if __name__ == "__main__":
    main()
