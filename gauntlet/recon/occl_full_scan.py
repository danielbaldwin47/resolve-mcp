"""analyze_occlusion over the two UNJUDGED Taurus spans, both angles. READ-ONLY.

Span A: Zinc SYNC 174117-175955 (deliverable  90.00-166.68 s)
Span B: Zinc SYNC 178113-181733 (deliverable 256.68-407.66 s)

Same source arithmetic as occl_mid_scan.py (FX6 record_in 117576 / source_in 0,
A7IV record_in 117576 / source_in 31270), so clip = SYNC - 117576 and SYNC - 86306.

The tuning ledger (occlusion_verdict_r3.json, occlusion_mid.json, occlusion_ending.json) says
the score does NOT separate true blockings from the FX6's dark piano lid, a head parked in the
bottom-right corner, or the A7IV's own foreground drummer. So this writes a scan and stops:
the windows are candidates for the eye, and a window is a veto only when a body covers a player
the shot is framed on, and MOVES across the frames either side.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occl_full_scan.json")

FPS = 24000.0 / 1001.0
DELIV_ZERO_SYNC = 171959

SPANS = [
    {"label": "A", "sync_in": 174117, "sync_out": 175955, "deliverable": [90.00, 166.68]},
    {"label": "B", "sync_in": 178113, "sync_out": 181733, "deliverable": [256.68, 407.66]},
]

ANGLES = [
    {
        "label": "A7IV",
        "clip": "20260617_D_A7IV_0006.MP4",
        "bin": "Zinc Bar/Footage/A7IV/Set 2",
        "offset": 86306,
        "source_out": 98676,
    },
    {
        "label": "FX6",
        "clip": "A015C001_2606170J.MXF",
        "bin": "Zinc Bar/Footage/FX6/Set 2",
        "offset": 117576,
        "source_out": 70876,
    },
]

report: dict[str, Any] = {
    "kind": "occlusion_full_scan",
    "spans": SPANS,
    "fps": FPS,
    "results": {},
    "errors": [],
}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")


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
    from resolve_mcp.jobs import runner, store
    from resolve_mcp.tools import video as video_tools

    for span in SPANS:
        for angle in ANGLES:
            key = f"{span['label']}:{angle['label']}"
            first = int(span["sync_in"]) - int(angle["offset"])
            last = int(span["sync_out"]) - int(angle["offset"])
            entry: dict[str, Any] = {
                "clip": angle["clip"],
                "source_range": {"in": first, "out": last},
                "covers": last <= int(angle["source_out"]),
                "t_to_clip": f"clip = {first} + round(d_rel * 23.976)",
                "deliverable": span["deliverable"],
            }
            report["results"][key] = entry
            write()
            if not entry["covers"]:
                entry["skipped"] = "item does not reach the end of this span"
                write()
                continue

            began = time.time()
            try:
                envelope = video_tools.analyze_occlusion(
                    str(angle["clip"]), bin=str(angle["bin"]), start=first, end=last
                )
            except Exception as exc:  # noqa: BLE001 - a probe records the failure
                note(f"start {key}", exc)
                continue
            entry["ok"] = envelope.get("ok")
            if not envelope.get("ok"):
                entry["error"] = envelope.get("error")
                write()
                continue

            job_id = envelope["job_id"]
            entry["job_id"] = job_id
            got = runner.wait_for(job_id, timeout=60.0)
            while got.state == store.RUNNING:
                entry["step"] = got.step
                write()
                print(f"  [{key}] {got.step}", flush=True)
                got = runner.wait_for(job_id, timeout=60.0)
            payload = got.payload()
            entry["state"] = payload.get("state")
            entry["elapsed_s"] = round(time.time() - began, 1)
            entry["error"] = payload.get("error")
            result = payload.get("result") or {}
            write()
            if not result:
                continue

            catalog = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
            base_d = float(span["deliverable"][0])
            windows = [
                {
                    "d_in": round(base_d + (one["in"]["frames"] - first) / FPS, 2),
                    "d_out": round(base_d + (one["out"]["frames"] - first) / FPS, 2),
                    "clip_in": one["in"]["frames"],
                    "clip_out": one["out"]["frames"],
                    "peak_score": one["peak_score"],
                    "mean_score": one["mean_score"],
                }
                for one in catalog["windows"]
            ]
            entry["windows"] = windows
            entry["baseline"] = catalog["baseline"]
            entry["top_samples"] = sorted(
                (
                    {
                        "d": round(base_d + (one["time"]["frames"] - first) / FPS, 2),
                        "clip": one["time"]["frames"],
                        "score": one["score"],
                        "coverage": one["coverage"],
                    }
                    for one in catalog["samples"]
                ),
                key=lambda s: -float(s["score"]),
            )[:25]
            write()
            print(key, "WINDOWS:", json.dumps(windows), flush=True)
            print(key, "baseline:", json.dumps(catalog["baseline"]), flush=True)

    write()
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
