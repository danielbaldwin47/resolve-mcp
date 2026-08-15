"""Live spot-check: does the window class agree with the eye on a Zinc angle? READ-ONLY.

AC3 of #189. The fake tier proves the discriminator against frozen grey; this proves the whole
route — Resolve locates the clip, ffmpeg decodes the range off the card, and the windows come
back classed — on the piece the gauntlet adjudicated hardest, Taurus MID (Zinc SYNC
175955-178113). Both angles, because each carries a different false positive: the A7IV has the
sax player crossing the near field (the one true blocking, and it scored *below* two drummer
false positives) and the FX6 has the 42 s mid-take reframe.

Ranges and expectations come from gauntlet/recon/occlusion_mid.json. ``refresh`` is on: the
cache key is the clip and the range, not the code, so a hit would answer with the scan that
ran before there were any classes at all.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occl_classes_live.json")

FPS = 24000.0 / 1001.0
REC_IN = 175955
REC_OUT = 178113
WINDOW_FRAMES = REC_OUT - REC_IN

ANGLES = [
    {
        "label": "A7IV",
        "clip": "20260617_D_A7IV_0006.MP4",
        "bin": "Zinc Bar/Footage/A7IV/Set 2",
        "first": 89649,
        # occlusion_mid.json: the crossing is the veto, the two drummer flags are not.
        "expected": {"63.98-64.94": "obstruction", "77.99-78.95": "scene", "86.96-87.92": "scene"},
    },
    {
        "label": "FX6",
        "clip": "A015C001_2606170J.MXF",
        "bin": "Zinc Bar/Footage/FX6/Set 2",
        "first": 58379,
        # occlusion_mid.json: 42 s of flags and nothing ever in the way — the reframe.
        "expected": {"0.00-41.96": "scene"},
    },
]

report: dict[str, Any] = {
    "kind": "occlusion_classes_live",
    "piece": "Taurus MID (mix 3735.16-3825.16 s, Zinc SYNC 175955-178113)",
    "adjudication": "gauntlet/recon/occlusion_mid.json",
    "angles": {},
    "errors": [],
}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def note(where: str, exc: BaseException) -> None:
    report["errors"].append(
        {"where": where, "type": type(exc).__name__, "message": str(exc),
         "traceback": traceback.format_exc(limit=6)}
    )
    write()


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.jobs import lifecycle, runner
    from resolve_mcp.tools import video as video_tools

    for angle in ANGLES:
        label = str(angle["label"])
        first = int(angle["first"])
        entry: dict[str, Any] = {
            "clip": angle["clip"],
            "source_range": [first, first + WINDOW_FRAMES],
            "expected": angle["expected"],
        }
        report["angles"][label] = entry
        write()

        began = time.time()
        try:
            envelope = video_tools.analyze_occlusion(
                str(angle["clip"]),
                bin=str(angle["bin"]),
                start=first,
                end=first + WINDOW_FRAMES,
                refresh=True,
            )
        except Exception as exc:  # noqa: BLE001 - a probe records the failure rather than dying
            note(f"start {label}", exc)
            continue
        if not envelope.get("ok"):
            entry["error"] = envelope.get("error")
            write()
            continue

        got = runner.wait_for(envelope["job_id"], timeout=120.0)
        while got.state == lifecycle.RUNNING:
            entry["step"] = got.step
            write()
            got = runner.wait_for(envelope["job_id"], timeout=120.0)
        payload = got.payload()
        entry["state"] = payload.get("state")
        entry["elapsed_s"] = round(time.time() - began, 1)
        entry["error"] = payload.get("error")
        result = payload.get("result") or {}
        entry["result_summary"] = {
            key: result.get(key)
            for key in ("windows", "obstructions", "unusable_seconds", "baseline", "decode")
        }
        write()
        if not result:
            continue

        catalog = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        got_windows = {
            f"{(one['in']['frames'] - first) / FPS:.2f}-{(one['out']['frames'] - first) / FPS:.2f}":
            {
                "kind": one["kind"],
                "peak_score": one["peak_score"],
                "peak_novel": one["peak_novel"],
                "peak_hidden": one["peak_hidden"],
            }
            for one in catalog["windows"]
        }
        entry["windows"] = got_windows
        entry["agrees"] = all(
            got_windows.get(span, {}).get("kind") == kind
            for span, kind in dict(angle["expected"]).items()
        )
        write()
        print(label, json.dumps(got_windows), flush=True)
        print(label, "agrees:", entry["agrees"], flush=True)

    report["agrees"] = all(one.get("agrees") for one in report["angles"].values())
    write()
    print("wrote", OUT, "agrees:", report["agrees"], flush=True)


if __name__ == "__main__":
    main()
