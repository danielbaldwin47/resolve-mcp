"""Validate + build the WHOLE-SONG Taurus cut, then run the correlate self-review.

One long-lived process: jobs are in-process daemon threads, so the correlate job is polled here
rather than across tool calls. Same shape as taurus_ending_build_p2r3.py. The tail block is
printed in full because the hardened round trip re-reads the LANDED timeline.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-full.cut.json"
OUT = HERE / "full_build.json"
SIDECAR = HERE.parents[1] / "styles" / "angles" / "mcp-tests-zinc.json"
BEATS = r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis\Zinc-Set-2-Reaper-v4-62537c590a14-beats.json"  # noqa: E501
MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
TUNES = r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis\Zinc-Set-2-Reaper-v4-08f33521bdda-tunes.json"  # noqa: E501


def poll(jobs: Any, job_id: str, label: str, timeout: float = 3600.0) -> dict:
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
    print("BUILT_NAME:", name, flush=True)
    print("TAIL BLOCK:", json.dumps(built.get("tail"), indent=1, default=str), flush=True)
    print("PLACED:", json.dumps(built.get("placed"), default=str)[:600], flush=True)
    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")

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
    r = res.get("result") or {}
    keep = {
        k: r.get(k)
        for k in (
            "gated", "stranded", "openings", "outside_grid", "transient_offsets",
            "shot_rhythm", "shot_seconds", "clips", "roles", "visible", "alignment",
        )
    }
    print("SUMMARY:", json.dumps(keep, indent=1, default=str)[:6000], flush=True)
    print("KEYS:", sorted(r), flush=True)


if __name__ == "__main__":
    main()
