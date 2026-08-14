"""Pixel check on the ending render: does the picture agree with the timeline?

correlate proves the timeline; scene detection proves the pixels, and disagreement is the
alarm (GAPS G3). Three reads: ffprobe for duration/resolution/streams, a scene-detect pass
at the 0.10 threshold G3 settled on, and blackdetect for where the picture actually leaves.
READ-ONLY.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDER = HERE.parents[1] / "gauntlet" / "renders" / "taurus_ending_p2r1.mp4"
OUT = HERE / "end_p2_pixelcheck.json"
CUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-ending.cut.json"
FPS = 24000.0 / 1001.0


def run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return (p.stdout or "") + (p.stderr or "")


def main() -> None:
    rep: dict = {"render": str(RENDER)}

    probe = run(["ffprobe", "-v", "error", "-show_entries",
                 "format=duration,size:stream=index,codec_type,codec_name,width,height,"
                 "r_frame_rate,channels,sample_rate,nb_frames", "-of", "json", str(RENDER)])
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
    print("  ", times, flush=True)

    black = run(["ffmpeg", "-v", "info", "-i", str(RENDER), "-vf",
                 "blackdetect=d=0.20:pic_th=0.98:pix_th=0.06", "-an", "-f", "null", "-"])
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
    print("authored items:", len(items), "starts:", starts, flush=True)

    # pair every authored boundary (after the first item) with the nearest detected cut
    pairs = []
    for b in starts[1:]:
        near = min(times, key=lambda x: abs(x - b)) if times else None
        pairs.append({"authored": b, "detected": near,
                      "delta_ms": None if near is None else round((near - b) * 1000, 1)})
    rep["boundary_pairs"] = pairs
    for p in pairs:
        print(f"  boundary {p['authored']:8.3f} -> detected {p['detected']}  "
              f"{p['delta_ms']:+.1f} ms", flush=True)
    OUT.write_text(json.dumps(rep, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
