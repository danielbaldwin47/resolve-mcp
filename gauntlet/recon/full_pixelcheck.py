"""Pixel check + tail proof on the whole-song render. READ-ONLY.

Three jobs in one pass, the same instrument the three winning pieces were checked with
(end_p2r3_pixelcheck.py, mid_p3r2_pixelcheck.py):

* the pixel check: ffprobe, scene detection at the 0.10 threshold, blackdetect, and every
  authored boundary paired with the nearest detected cut. Scene detection counts HARD cuts
  only -- the tail dissolve will not read as one, and that is the expected result.
* the tail proof: the dissolve is verified by a luma ramp over the last 5.923 s (many distinct
  intermediate levels rather than one step) and the audio fade by an RMS ladder falling to near
  silence. A hard cut to black passes neither test.
* the final-stretch claim: no detected cut anywhere between the last authored one and the end
  of the file, so the cadence, the applause and the whole dissolve play on one picture.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RENDER = HERE.parents[1] / "gauntlet" / "renders" / "taurus_full_p4r1.mp4"
OUT = HERE / "full_pixelcheck.json"
CUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-full.cut.json"

FPS = 24000.0 / 1001.0
PICTURE, DISSOLVE, MIX, AUDIO_FADE = 11928, 142, 11932, 125
CADENCE, NEAR_GAP = 491.06, 490.38
"""The ending piece's last note and its near-gap, in whole-song deliverable seconds."""

LUMA_NAME, RMS_NAME = "full_luma.txt", "full_rms.txt"
"""Where the ffmpeg metadata dumps land. Named rather than inlined so a later round can point
this instrument at its own render without overwriting the receipts of the round before it."""


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return (p.stdout or "") + (p.stderr or "")


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


def tail_verdict(
    luma: list[tuple[float, float]], rms: list[tuple[float, float]]
) -> dict[str, Any]:
    picture_seconds = PICTURE / FPS
    dissolve_starts = (PICTURE - DISSOLVE) / FPS
    before = [v for at, v in luma if dissolve_starts - 20.0 < at < dissolve_starts - 0.5]
    steady = sum(before) / len(before) if before else 0.0
    ceiling = max(steady - 16.0, 1e-9)
    ramp = [
        (at, (v - 16.0) / ceiling) for at, v in luma if dissolve_starts <= at <= picture_seconds
    ]
    intermediate = sorted(at for at, level in ramp if 0.08 < level < 0.92)
    fade_starts = (MIX - AUDIO_FADE) / FPS
    body_rms = [v for at, v in rms if at < dissolve_starts]
    tail_rms = [v for at, v in rms if at >= (MIX / FPS) - 0.6]
    ladder = []
    for second in range(int(fade_starts), int(MIX / FPS) + 1):
        window = [v for at, v in rms if second <= at < second + 1]
        if window:
            ladder.append((second, round(sum(window) / len(window), 2)))
    monotone = all(b[1] <= a[1] + 0.02 for a, b in zip(ramp, ramp[1:], strict=False))
    passes = {
        "luma_ramps_not_steps": len(intermediate) >= 20,
        "ramp_spans_the_dissolve": bool(intermediate)
        and (max(intermediate) - min(intermediate)) > (DISSOLVE / FPS) * 0.7,
        "ramp_is_monotone": monotone,
        "lands_on_black": bool(ramp) and ramp[-1][1] < 0.02,
        "audio_falls_progressively": len(ladder) > 2
        and all(b[1] < a[1] for a, b in zip(ladder, ladder[1:], strict=False)),
        "audio_ends_near_silence": bool(rms) and rms[-1][1] < -40.0,
    }
    return {
        "passes": passes,
        "all_passed": all(passes.values()),
        "dissolve_starts_s": round(dissolve_starts, 3),
        "picture_ends_s": round(picture_seconds, 3),
        "audio_fade_starts_s": round(fade_starts, 3),
        "steady_luma": round(steady, 3),
        "ramp_samples": len(ramp),
        "intermediate_samples": len(intermediate),
        "intermediate_span_s": (
            round(max(intermediate) - min(intermediate), 3) if intermediate else 0.0
        ),
        "luma_at_last_picture_frame": round(ramp[-1][1], 4) if ramp else None,
        "luma_ramp_yavg": [(round(at, 2), round(v, 1)) for at, v in luma if at >= dissolve_starts],
        "rms_ladder_db": ladder,
        "rms_last_sample_db": round(rms[-1][1], 2) if rms else None,
        "body_rms_mean_db": round(sum(body_rms) / len(body_rms), 2) if body_rms else None,
        "tail_rms_mean_db": round(sum(tail_rms) / len(tail_rms), 2) if tail_rms else None,
    }


def main() -> None:
    rep: dict[str, Any] = {"render": str(RENDER)}

    probe = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height,"
        "r_frame_rate,channels,sample_rate,nb_frames", "-of", "json", str(RENDER),
    ])
    rep["ffprobe"] = json.loads(probe)
    fmt = rep["ffprobe"]["format"]
    print("duration", fmt["duration"], "s   size", fmt["size"], flush=True)
    for s in rep["ffprobe"]["streams"]:
        print("  stream", s.get("index"), s.get("codec_type"), s.get("codec_name"),
              s.get("width"), s.get("height"), s.get("r_frame_rate"), s.get("channels"),
              flush=True)

    scenes = run(["ffmpeg", "-v", "info", "-i", str(RENDER), "-vf",
                  "select='gt(scene,0.10)',showinfo", "-an", "-f", "null", "-"])
    times = [round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", scenes)]
    rep["scene_cuts_010"] = times
    print("scene cuts at 0.10:", len(times), flush=True)

    black = run(["ffmpeg", "-v", "info", "-i", str(RENDER), "-vf",
                 "blackdetect=d=0.10:pic_th=0.98:pix_th=0.06", "-an", "-f", "null", "-"])
    rep["blackdetect"] = re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", black)
    print("black runs:", rep["blackdetect"], flush=True)

    doc = json.loads(CUT.read_text(encoding="utf-8"))
    items = doc["segments"]
    starts, acc = [], 0
    for s in items:
        starts.append(round(acc / FPS, 3))
        acc += s.get("gap") or (s["out"] - s["in"])
    rep["authored_item_starts"] = starts
    rep["items"] = len(items)
    rep["authored_hard_cuts"] = len(items) - 1
    print("authored items:", len(items), "hard cuts:", len(items) - 1, flush=True)

    pairs = []
    for b in starts[1:]:
        near = min(times, key=lambda x: abs(x - b)) if times else None
        pairs.append({"authored": b, "detected": near,
                      "delta_ms": None if near is None else round((near - b) * 1000, 1)})
    rep["boundary_pairs"] = pairs
    worst = 0.0
    missed = []
    for p in pairs:
        d = abs(p["delta_ms"] or 0.0)
        worst = max(worst, d)
        if p["delta_ms"] is None or d > 60:
            missed.append(p)
    rep["worst_boundary_delta_ms"] = worst
    rep["missed"] = missed
    rep["every_cut_detected"] = not missed
    matched = {
        p["detected"] for p in pairs if p["delta_ms"] is not None and abs(p["delta_ms"]) <= 60
    }
    extras = [t for t in times if t not in matched]
    rep["extra_scene_cuts"] = extras
    rep["no_extra_scene_cuts"] = not extras
    print("every authored cut detected:", rep["every_cut_detected"],
          " worst delta ms:", worst, " extras:", extras, flush=True)
    for p in missed:
        print("  MISSED", p, flush=True)

    last_cut = starts[-1]
    after = [t for t in times if t > last_cut + 0.20]
    rep["final_stretch"] = {
        "last_authored_cut_s": last_cut,
        "detected_cuts_after_it": after,
        "seconds_of_one_picture_to_end": round(MIX / FPS - last_cut, 3),
        "lead_before_near_gap_s": round(NEAR_GAP - last_cut, 3),
        "lead_before_cadence_s": round(CADENCE - last_cut, 3),
        "one_picture_carries_the_ending": not after,
    }
    print("FINAL STRETCH:", json.dumps(rep["final_stretch"]), flush=True)

    luma = series(
        RENDER,
        ["-vf", "fps=8,signalstats,metadata=print:key=lavfi.signalstats.YAVG:"
                f"file={LUMA_NAME}"],
        "lavfi.signalstats.YAVG=",
        HERE / LUMA_NAME,
    )
    rms = series(
        RENDER,
        ["-af", "astats=metadata=1:reset=24,ametadata=print:"
                f"key=lavfi.astats.Overall.RMS_level:file={RMS_NAME}"],
        "lavfi.astats.Overall.RMS_level=",
        HERE / RMS_NAME,
    )
    rep["tail"] = tail_verdict(luma, rms)
    print("TAIL VERDICT:", json.dumps(rep["tail"]["passes"], indent=1), flush=True)
    print("all_passed:", rep["tail"]["all_passed"], flush=True)
    print("dissolve_starts_s", rep["tail"]["dissolve_starts_s"],
          "picture_ends_s", rep["tail"]["picture_ends_s"],
          "steady_luma", rep["tail"]["steady_luma"],
          "intermediate", rep["tail"]["intermediate_samples"],
          "span_s", rep["tail"]["intermediate_span_s"],
          "last_level", rep["tail"]["luma_at_last_picture_frame"], flush=True)
    print("rms ladder:", rep["tail"]["rms_ladder_db"],
          "last", rep["tail"]["rms_last_sample_db"], flush=True)

    OUT.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
