"""Validate + build the round-2 Taurus opening cut, then run the correlate self-review.

One long-lived process: jobs are in-process daemon threads, so the correlate job is
polled here rather than across tool calls.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-opening.cut.json"
OUT = HERE / "taurus_build_r2.json"
SIDECAR = HERE.parents[1] / "styles" / "angles" / "mcp-tests-zinc.json"
BEATS = r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis\Zinc-Set-2-Reaper-v4-62537c590a14-beats.json"
MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
TUNES = r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis\Zinc-Set-2-Reaper-v4-08f33521bdda-tunes.json"


def poll(jobs: Any, job_id: str, label: str, timeout: float = 1800.0) -> dict:
    start = time.time()
    last = ""
    while time.time() - start < timeout:
        j = jobs.get_job(job_id)["job"]
        state = j.get("state")
        step = f"{j.get('step')} {j.get('progress')}"
        if step != last:
            print(f"  [{label}] {state} {step}", flush=True)
            last = step
        if state in ("completed", "failed"):
            return j
        time.sleep(3.0)
    return {"state": "timeout"}


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import analysis as analysis_tools
    from resolve_mcp.tools import cut as cut_tools
    from resolve_mcp.tools import jobs as job_tools

    report: dict[str, Any] = {}

    val = cut_tools.validate_cut(cut_file=str(CUT))
    report["validate"] = val
    errs = val.get("errors") or []
    warns = val.get("warnings") or []
    print("validate ok:", val.get("ok"), "errors:", len(errs), "warnings:", len(warns), flush=True)
    for e in errs:
        print("  E", e, flush=True)
    for w in warns:
        print("  W", w, flush=True)
    if errs:
        OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        return

    built = cut_tools.build_timeline(cut_file=str(CUT))
    report["build"] = built
    tl = built.get("timeline")
    name = tl.get("name") if isinstance(tl, dict) else tl
    print("built:", name, flush=True)
    print("build:", json.dumps({k: v for k, v in built.items() if k != "items"}, default=str)[:1200], flush=True)

    sidecar = json.loads(SIDECAR.read_text(encoding="utf-8"))
    angles = {
        clip: {k: v for k, v in spec.items() if k in ("role", "subject", "character")}
        for clip, spec in sidecar["angles"].items()
    }

    job = analysis_tools.correlate_timeline(
        beats=BEATS, timeline=name, audio=MIX, tunes=TUNES, angles=angles
    )["job"]
    res = poll(job_tools, job["job_id"], "correlate")
    report["correlate"] = res
    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("correlate state:", res.get("state"), flush=True)
    print(json.dumps(res.get("result"), indent=1, default=str)[:8000], flush=True)


if __name__ == "__main__":
    main()
