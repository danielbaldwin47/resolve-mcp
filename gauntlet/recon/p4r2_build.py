"""Validate + build the R2 whole-song Taurus cut, apply its titles, then correlate self-review.

Same shape as full_build.py (one long-lived process, jobs polled in-process). The correlate
call is the R2 gate: transient offsets band, reads_metronomic, and the gears block that landed
after R1 was measured -- one_speed, rate_ratio, and where the sub-2 s shots sit.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-full-r2.cut.json"
TITLES = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people.titles.json"
OUT = HERE / "p4r2_build.json"
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


DROP_STALE = """
gone = []
for i in range(project.GetTimelineCount(), 0, -1):
    tl = project.GetTimelineByIndex(i)
    nm = tl.GetName()
    if nm.startswith('Taurus People Full P4 R2'):
        gone.append(nm)
        media_pool.DeleteTimelines([tl])
result = {'deleted': gone,
          'current': project.GetCurrentTimeline().GetName()}
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import analysis as analysis_tools
    from resolve_mcp.tools import cut as cut_tools
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import jobs as job_tools
    from resolve_mcp.tools import titles as title_tools

    report: dict[str, Any] = {}
    # A rerun must not leave half-measured R2 timelines behind for the render step to pick
    # the wrong one out of. Only this build's own name prefix is touched.
    dropped = escape_hatch.run_python(code=DROP_STALE)
    report["dropped"] = dropped
    print("DROPPED:", json.dumps(dropped, default=str)[:400], flush=True)

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

    # Correlate BEFORE the titles land. apply_titles owns a second video track, and the
    # measurement reads every enabled track: with titles on, the personnel card counts as a
    # shot and its overlap as a 4-frame sliver, which is a reading of the title pass rather
    # than of the cut. R1 was measured this way too, so the two rounds compare.
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
            "shot_rhythm", "clips", "roles", "visible", "alignment",
        )
    }
    print("SUMMARY:", json.dumps(keep, indent=1, default=str)[:5000], flush=True)
    gears = r.get("shot_rhythm", {}).get("gears")
    print("GEARS:", json.dumps(gears, indent=1, default=str), flush=True)
    print("KEYS:", sorted(r), flush=True)

    # Titles are positioned off a blue marker named for the song key, and a fresh build has
    # none: set the anchor on frame 0 of the cut before applying them (full_markers.py).
    from resolve_mcp.tools import timeline as timeline_tools

    marked = timeline_tools.set_markers(
        timeline=name,
        markers=[
            {
                "frame": 86400,
                "color": "Blue",
                "name": "taurus-people",
                "note": "Song anchor for taurus-people.titles.json "
                        "(card at +0, personnel at +495).",
            }
        ],
    )
    report["markers"] = marked
    print("MARKERS:", json.dumps(marked, default=str)[:500], flush=True)

    doc = json.loads(TITLES.read_text(encoding="utf-8"))
    doc["timeline"] = name
    TITLES.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    tval = title_tools.validate_titles(str(TITLES))
    report["titles_validate"] = tval
    print("titles validate:", json.dumps(tval, default=str)[:400], flush=True)
    tgot = title_tools.apply_titles(str(TITLES))
    report["titles"] = tgot
    print("titles applied:", json.dumps(tgot, default=str)[:600], flush=True)
    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("FINAL_NAME:", name, flush=True)


if __name__ == "__main__":
    main()
