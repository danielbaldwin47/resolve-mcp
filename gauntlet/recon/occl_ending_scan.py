"""analyze_occlusion over the Taurus ENDING window, both angles. READ-ONLY.

Window: Zinc SYNC 181733-183891 (mix 3976.15-4066.15 s, deliverable 407.66-497.66 s).
Source frames come off the SYNC record through each item's own numbering, exactly as
occlusion_probe.py did for the opening (taurus_grabs.json items: FX6 record_in 117576 /
source_in 0, A7IV record_in 117576 / source_in 31270 — both items run past this window).

The round-3 ledger (occlusion_verdict_r3.json) is the reason this writes a scan and stops:
on the FX6 the dark piano lid and a head parked in the bottom-right corner score 0.58-0.68
without anything being in the way, while the two true blockings scored ~1.0 and moved. So
the scan's windows are candidates for the eye, not verdicts. occl_ending_grabs.py pulls the
frames; the verdicts land in occlusion_ending.json.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occl_ending_scan.json")

FPS = 24000.0 / 1001.0
REC_IN = 181733
REC_OUT = 183891
WINDOW_FRAMES = REC_OUT - REC_IN

ANGLES = [
    {
        "label": "A7IV",
        "clip": "20260617_D_A7IV_0006.MP4",
        "bin": "Zinc Bar/Footage/A7IV/Set 2",
        "record_in": 117576,
        "source_in": 31270,
        "source_out": 98676,
    },
    {
        "label": "FX6",
        "clip": "A015C001_2606170J.MXF",
        "bin": "Zinc Bar/Footage/FX6/Set 2",
        "record_in": 117576,
        "source_in": 0,
        "source_out": 70876,
    },
]

report: dict[str, Any] = {
    "kind": "occlusion_ending_scan",
    "window": {
        "sync_in": REC_IN,
        "sync_out": REC_OUT,
        "mix_seconds": [3976.15, 4066.15],
        "deliverable_seconds": [407.66, 497.66],
        "fps": FPS,
    },
    "angles": {},
    "errors": [],
}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def note(where: str, exc: BaseException) -> None:
    report["errors"].append(
        {
            "where": where,
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=6),
        }
    )
    write()


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.jobs import lifecycle, runner
    from resolve_mcp.tools import video as video_tools

    for angle in ANGLES:
        label = str(angle["label"])
        first = int(angle["source_in"]) + (REC_IN - int(angle["record_in"]))
        last = first + WINDOW_FRAMES
        entry: dict[str, Any] = {
            "clip": angle["clip"],
            "bin": angle["bin"],
            "source_range": {"in": first, "out": last},
            "item_source_out": angle["source_out"],
            "covers_window": last <= int(angle["source_out"]),
            "piece_t_to_clip_frame": f"clip = {first} + round(t * 23.976)",
        }
        report["angles"][label] = entry
        write()
        if not entry["covers_window"]:
            entry["skipped"] = "the timeline item does not reach the end of this window"
            write()
            continue

        began = time.time()
        try:
            envelope = video_tools.analyze_occlusion(
                str(angle["clip"]), bin=str(angle["bin"]), start=first, end=last
            )
        except Exception as exc:  # noqa: BLE001 - a probe records the failure rather than dying
            note(f"start {label}", exc)
            continue
        entry["ok"] = envelope.get("ok")
        if not envelope.get("ok"):
            entry["error"] = envelope.get("error")
            write()
            continue

        job_id = envelope["job_id"]
        entry["job_id"] = job_id
        got = runner.wait_for(job_id, timeout=60.0)
        while got.state == lifecycle.RUNNING:
            entry["step"] = got.step
            write()
            got = runner.wait_for(job_id, timeout=60.0)
        payload = got.payload()
        entry["state"] = payload.get("state")
        entry["elapsed_s"] = round(time.time() - began, 1)
        entry["error"] = payload.get("error")
        result = payload.get("result") or {}
        entry["result"] = result
        write()
        if not result:
            continue

        catalog = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        windows = [
            {
                "start_s": round((one["in"]["frames"] - first) / FPS, 2),
                "end_s": round((one["out"]["frames"] - first) / FPS, 2),
                "in_frame": one["in"]["frames"],
                "out_frame": one["out"]["frames"],
                "peak_score": one["peak_score"],
                "mean_score": one["mean_score"],
            }
            for one in catalog["windows"]
        ]
        entry["windows_in_piece"] = windows
        entry["samples_in_piece"] = [
            {
                "at_s": round((one["time"]["frames"] - first) / FPS, 2),
                "frame": one["time"]["frames"],
                "score": one["score"],
                "coverage": one["coverage"],
            }
            for one in catalog["samples"]
        ]
        entry["baseline"] = catalog["baseline"]
        write()
        print(label, "windows:", json.dumps(windows), flush=True)
        print(label, "baseline:", json.dumps(catalog["baseline"]), flush=True)

    write()
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
