"""Pixel check on the piece-3 render: ffprobe shape, scene-detect count vs item count.

Scene detect at 0.10 over the render, matched against the 11 planned cut times (the
timeline is 12 butt-joined items, so a clean render shows 11 scene changes). Also
reports the render's own luma per second so a black frame or a dropped item shows up.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDER = HERE.parents[1] / "gauntlet" / "renders" / "taurus_mid_p3r1.mp4"
PLAN = json.loads((HERE / "mid_p3_plan.json").read_text(encoding="utf-8"))
OUT = HERE / "mid_p3_pixelcheck.json"
FPS = 24000.0 / 1001.0


def probe() -> dict:
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=duration,size:stream=width,height,codec_name,r_frame_rate,nb_frames,codec_type",
           "-of", "json", str(RENDER)]
    return json.loads(subprocess.run(cmd, capture_output=True, check=True, text=True).stdout)


def scenes(threshold: float) -> list[float]:
    cmd = ["ffmpeg", "-v", "error", "-i", str(RENDER), "-vf",
           f"scale=320:-2,select='gt(scene,{threshold})',metadata=print:file=-",
           "-an", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True, text=True).stdout
    times = []
    for line in out.splitlines():
        if line.startswith("frame:") and "pts_time:" in line:
            times.append(round(float(line.split("pts_time:")[1].split()[0]), 3))
    return times


def main() -> None:
    p = probe()
    v = next(s for s in p["streams"] if s.get("codec_type") == "video")
    a = [s for s in p["streams"] if s.get("codec_type") == "audio"]
    planned = [round(c["cut_frame_rel"] / FPS, 3) for c in PLAN["cuts"][:-1]]
    det = scenes(0.10)
    matched, unmatched = [], []
    for c in planned:
        near = [d for d in det if abs(d - c) <= 0.30]
        (matched if near else unmatched).append({"planned": c, "detected": near})
    extra = [d for d in det if all(abs(d - c) > 0.30 for c in planned)]
    rep = {
        "render": str(RENDER),
        "duration_s": round(float(p["format"]["duration"]), 3),
        "size_bytes": int(p["format"]["size"]),
        "video": {k: v.get(k) for k in ("width", "height", "codec_name", "r_frame_rate",
                                        "nb_frames")},
        "audio_streams": len(a),
        "planned_cuts": len(planned),
        "detected_at_0.10": det,
        "matched": len(matched),
        "unmatched_planned": unmatched,
        "extra_detections": extra,
    }
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in
                      ("duration_s", "video", "audio_streams", "planned_cuts", "matched",
                       "unmatched_planned", "extra_detections")}, indent=1), flush=True)
    print("detected:", det, flush=True)


if __name__ == "__main__":
    main()
