"""Live proof of the tail device: build a 20 s cut with the measured Taurus tail, render, measure.

Scratch only — builds 'SCRATCH ending-devices v1' and deletes it at the end. Never touches
'Zinc - Set 2 Main' or the gauntlet cuts.

The bar: the luma over the last 5.923 s has to *ramp* (many distinct intermediate levels),
not step; the audio RMS has to fall progressively to near silence at the end. A hard cut to
black passes neither.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RENDERS = HERE.parents[1] / "gauntlet" / "renders"
CUT = HERE / "ending_devices.cut.json"
OUT = HERE / "ending_devices_proof.json"
NAME = "ending_devices_proof"
BASE = "SCRATCH ending-devices"
TIMELINE = f"{BASE} v1"

FPS = 23.976
DISSOLVE = 142  # 5.923 s — the measured Taurus tail
AUDIO_FADE = 125  # ~5.21 s — the measured fade under it
PICTURE = 480  # 20.02 s of picture
MIX = 520  # the mix outlives the picture, as it does in all five deliverables

DOC: dict[str, Any] = {
    "schema": 1,
    "timeline": {"name": BASE, "fps": FPS},
    "sources": {
        "fx6_wide": {"clip": "A015C001_2606170J.MXF", "bin": "Zinc Bar/Footage/FX6/Set 2"},
        "a7iv_kit": {"clip": "20260617_D_A7IV_0006.MP4", "bin": "Zinc Bar/Footage/A7IV/Set 2"},
        "master_mix": {"clip": "Zinc Set 2 Reaper v4.wav", "bin": "Zinc Bar/Audio"},
    },
    "audio": {"source": "master_mix", "in": 95332, "out": 95332 + MIX},
    "segments": [
        {"id": "s01", "source": "fx6_wide", "in": 64157, "out": 64277, "note": "the wide"},
        {"id": "s02", "source": "a7iv_kit", "in": 95588, "out": 95748, "note": "the kit"},
        {"id": "s03", "source": "fx6_wide", "in": 64400, "out": 64600, "note": "the departure"},
    ],
    "tail": {
        "type": "dissolve_to_black",
        "duration_frames": DISSOLVE,
        "audio_fade_frames": AUDIO_FADE,
    },
}

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

CLEANUP = """
pool = project.GetMediaPool()
doomed = []
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName().startswith('SCRATCH '):
        doomed.append(tl)
keep = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if not tl.GetName().startswith('SCRATCH '):
        keep = tl
        break
moved = project.SetCurrentTimeline(keep) if keep is not None else None
names = [tl.GetName() for tl in doomed]
deleted = pool.DeleteTimelines(doomed) if doomed else None
left = [project.GetTimelineByIndex(i).GetName() for i in range(1, project.GetTimelineCount() + 1)]
result = {'moved': moved, 'asked': names, 'deleted': deleted,
          'scratch_left': [n for n in left if n.startswith('SCRATCH ')]}
"""


def series(path: Path, args: list[str], key: str, out: Path) -> list[tuple[float, float]]:
    # Run from the directory the metadata file goes in: an ffmpeg filter option splits on
    # ':', so a Windows absolute path cannot be handed to file= at all.
    out.unlink(missing_ok=True)
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), *args, "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(out.parent),
    )
    found: list[tuple[float, float]] = []
    at = 0.0
    for line in out.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("frame:") and "pts_time:" in line:
            at = float(line.split("pts_time:")[1])
        elif key in line:
            found.append((at, float(line.split("=")[1])))
    return found


def measure(rendered: Path) -> dict[str, Any]:
    luma_file = HERE / "ending_devices_luma.txt"
    rms_file = HERE / "ending_devices_rms.txt"
    luma = series(
        rendered,
        [
            "-vf",
            f"fps=8,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file={luma_file.name}",
        ],
        "lavfi.signalstats.YAVG=",
        luma_file,
    )
    rms = series(
        rendered,
        [
            "-af",
            "astats=metadata=1:reset=24,ametadata=print:"
            f"key=lavfi.astats.Overall.RMS_level:file={rms_file.name}",
        ],
        "lavfi.astats.Overall.RMS_level=",
        rms_file,
    )
    return {"luma": luma, "rms": rms}


def verdict(luma: list[tuple[float, float]], rms: list[tuple[float, float]]) -> dict[str, Any]:
    """Does this look like a dissolve and a fade, or like a hard cut?"""
    picture_seconds = PICTURE / FPS
    dissolve_starts = (PICTURE - DISSOLVE) / FPS
    # Studio-range black sits at 16, so signal above black is what a dissolve actually ramps.
    before = [value for at, value in luma if at < dissolve_starts - 0.5]
    steady = sum(before) / len(before) if before else 0.0
    ceiling = max(steady - 16.0, 1e-9)
    ramp = [
        (at, (value - 16.0) / ceiling)
        for at, value in luma
        if dissolve_starts <= at <= picture_seconds
    ]
    intermediate = [at for at, level in ramp if 0.08 < level < 0.92]
    fade_starts = (MIX - AUDIO_FADE) / FPS
    tail_rms = [value for at, value in rms if at >= (MIX / FPS) - 0.6]
    body_rms = [value for at, value in rms if at < dissolve_starts]
    ladder = []
    for second in range(int(fade_starts), int(MIX / FPS) + 1):
        window = [value for at, value in rms if second <= at < second + 1]
        if window:
            ladder.append((second, round(sum(window) / len(window), 2)))
    intermediate = sorted(intermediate)
    passes = {
        # A hard cut to black scores 0 here: one frame at full, the next at black.
        "luma_ramps_not_steps": len(intermediate) >= 20,
        "ramp_spans_the_dissolve": bool(intermediate)
        and (max(intermediate) - min(intermediate)) > (DISSOLVE / FPS) * 0.7,
        "ramp_is_monotone": all(b[1] <= a[1] + 0.02 for a, b in zip(ramp, ramp[1:], strict=False)),
        "lands_on_black": bool(ramp) and ramp[-1][1] < 0.02,
        "audio_falls_progressively": len(ladder) > 2
        and all(b[1] < a[1] for a, b in zip(ladder, ladder[1:], strict=False)),
        "audio_ends_near_silence": bool(rms) and rms[-1][1] < -40.0,
    }
    return {
        "passes": passes,
        "all_passed": all(passes.values()),
        "audio_fade_starts_s": round(fade_starts, 3),
        "rms_ladder_db": ladder,
        "rms_last_sample_db": round(rms[-1][1], 2) if rms else None,
        "dissolve_starts_s": round(dissolve_starts, 3),
        "picture_ends_s": round(picture_seconds, 3),
        "steady_luma": round(steady, 3),
        "ramp_samples": len(ramp),
        "intermediate_samples": len(intermediate),
        "intermediate_span_s": (
            round(max(intermediate) - min(intermediate), 3) if intermediate else 0.0
        ),
        "ramp_is_monotone": all(
            b[1] <= a[1] + 0.02 for a, b in zip(ramp, ramp[1:], strict=False)
        ),
        "luma_at_last_picture_frame": round(ramp[-1][1], 4) if ramp else None,
        "body_rms_mean_db": round(sum(body_rms) / len(body_rms), 2) if body_rms else None,
        "tail_rms_mean_db": round(sum(tail_rms) / len(tail_rms), 2) if tail_rms else None,
        "rms_fell_db": (
            round(sum(body_rms) / len(body_rms) - sum(tail_rms) / len(tail_rms), 2)
            if body_rms and tail_rms
            else None
        ),
    }


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import cut as cut_tools
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import jobs as job_tools
    from resolve_mcp.tools import render as render_tools

    report: dict[str, Any] = {"cut": DOC, "resolve": None}
    CUT.write_text(json.dumps(DOC, indent=1), encoding="utf-8")

    swept = escape_hatch.run_python(code=CLEANUP)
    report["pre_cleanup"] = swept.get("result")
    print("pre-cleanup", json.dumps(swept.get("result"), default=str)[:300], flush=True)

    validated = cut_tools.validate_cut(cut_file=str(CUT))
    report["validate"] = {
        "ok": validated.get("ok"),
        "errors": validated.get("errors"),
        "warnings": validated.get("warnings"),
    }
    print("validate", validated.get("ok"), validated.get("errors"), flush=True)

    build = cut_tools.build_timeline(cut_file=str(CUT))
    report["build"] = {
        "ok": build.get("ok"),
        "error": build.get("error"),
        "timeline": (build.get("timeline") or {}).get("name"),
        "tail": build.get("tail"),
        "placed": build.get("placed"),
        "content_hash": build.get("content_hash"),
    }
    print("build", build.get("ok"), report["build"]["timeline"], flush=True)
    print("tail", json.dumps(report["build"]["tail"], default=str), flush=True)
    if not build.get("ok"):
        OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        return

    hd = escape_hatch.run_python(code=SET_HD)
    report["timeline_settings"] = hd.get("result")
    print("hd", json.dumps(hd.get("result"), default=str)[:200], flush=True)

    job = render_tools.render_timeline(
        preset="YouTube - 1080p",
        timeline=TIMELINE,
        name=NAME,
        target_dir=str(RENDERS),
        refresh=True,
    )["job"]
    while True:
        current = job_tools.get_job(job["job_id"])["job"]
        if current.get("state") in ("completed", "failed"):
            report["render"] = {"state": current.get("state"), "result": current.get("result")}
            print("render", current.get("state"), flush=True)
            break
        time.sleep(4.0)

    rendered = sorted(RENDERS.glob(f"{NAME}*"), key=lambda path: path.stat().st_mtime)
    if not rendered:
        report["error"] = "nothing rendered"
        OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        return
    measured = measure(rendered[-1])
    report["rendered"] = str(rendered[-1])
    report["measured"] = measured
    report["verdict"] = verdict(measured["luma"], measured["rms"])
    print("verdict", json.dumps(report["verdict"], default=str), flush=True)

    cleaned = escape_hatch.run_python(code=CLEANUP)
    report["cleanup"] = cleaned.get("result")
    print("cleanup", json.dumps(cleaned.get("result"), default=str)[:400], flush=True)

    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
