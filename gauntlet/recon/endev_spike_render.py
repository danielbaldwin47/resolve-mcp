"""Render the injected scratch timeline and measure the tail: luma ramp + audio RMS."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RENDERS = HERE.parents[1] / "gauntlet" / "renders"
TIMELINE = "SCRATCH endev injected"
NAME = "endev_spike"
OUT = HERE / "endev_spike_measure.json"

SET_HD = f"""
found = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName() == {TIMELINE!r}:
        found = tl
project.SetCurrentTimeline(found)
found.SetSetting('useCustomSettings', '1')
ok_w = found.SetSetting('timelineResolutionWidth', '1920')
ok_h = found.SetSetting('timelineResolutionHeight', '1080')
result = {{'ok_w': ok_w, 'ok_h': ok_h, 'start': found.GetStartFrame(), 'end': found.GetEndFrame()}}
"""


def measure(path: Path) -> dict[str, Any]:
    """Per-second mean luma and RMS over the whole render."""
    luma = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-vf", "fps=4,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    yavg: list[float] = []
    times: list[float] = []
    for line in luma.stderr.splitlines():
        line = line.strip()
        if line.startswith("frame:") and "pts_time:" in line:
            times.append(float(line.split("pts_time:")[1].strip()))
        elif "lavfi.signalstats.YAVG=" in line:
            yavg.append(float(line.split("=")[1]))
    rms = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
            "-af", "astats=metadata=1:reset=12,ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    levels: list[float] = []
    ltimes: list[float] = []
    for line in rms.stderr.splitlines():
        line = line.strip()
        if line.startswith("frame:") and "pts_time:" in line:
            ltimes.append(float(line.split("pts_time:")[1].strip()))
        elif "lavfi.astats.Overall.RMS_level=" in line:
            levels.append(float(line.split("=")[1]))
    return {
        "luma": list(zip(times, yavg, strict=False)),
        "rms": list(zip(ltimes, levels, strict=False)),
    }


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import jobs as job_tools
    from resolve_mcp.tools import render as render_tools

    print("hd:", json.dumps(escape_hatch.run_python(code=SET_HD), default=str)[:400], flush=True)
    job = render_tools.render_timeline(
        preset="YouTube - 1080p",
        timeline=TIMELINE,
        name=NAME,
        target_dir=str(RENDERS),
        refresh=True,
    )["job"]
    result: dict[str, Any] = {}
    while True:
        j = job_tools.get_job(job["job_id"])["job"]
        if j.get("state") in ("completed", "failed"):
            result = j.get("result") or {}
            print("render", j.get("state"), json.dumps(result, default=str)[:600], flush=True)
            break
        time.sleep(4.0)

    files = sorted(RENDERS.glob(f"{NAME}*"), key=lambda p: p.stat().st_mtime)
    if not files:
        OUT.write_text(json.dumps({"error": "no render", "job": result}, indent=1), "utf-8")
        return
    rendered = files[-1]
    print("rendered", rendered, flush=True)
    report = {"file": str(rendered), **measure(rendered)}
    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
