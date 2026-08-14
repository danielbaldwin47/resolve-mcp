"""Which camera does the human hold in this window? Match deliverable frames to both angles.

For each human shot in mid_human_cuts.json, grab the deliverable frame at the shot's midpoint
and the two camera frames at the same Zinc SYNC frame, and score each camera by normalised
cross-correlation on a 64x36 gray image. The deliverable is graded and rescaled, so the score
is read as a ranking between the two cameras, never as an absolute. READ-ONLY (writes JSON).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
HUM = json.loads((HERE / "mid_human_cuts.json").read_text(encoding="utf-8"))
OUT = HERE / "r2_human_angles.json"

FPS = 24000.0 / 1001.0
DELIV = r"S:\Deliverables\Ryan Devlin\6-17-26 Zinc Bar\Full Videos\6-17 - Zinc Set 2 - Taurus People.mp4"  # noqa: E501
CAMS = {
    "FX6": (r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF", 117576),  # noqa: E501
    "A7IV": (r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4", 86306),  # noqa: E501
}
MIX_T0, DELIV_T0, SYNC_T0 = 3735.16, 166.68, 175955
W, H = 64, 36


def frame(path: str, t: float) -> np.ndarray:
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", path, "-frames:v", "1",
        "-vf", f"scale={W}:{H}", "-pix_fmt", "gray", "-f", "rawvideo", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw[: W * H], dtype=np.uint8).reshape(H, W).astype(np.float64)


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    return float((a * b).sum() / (np.sqrt((a * a).sum() * (b * b).sum()) + 1e-9))


def main() -> None:
    rows = []
    for shot in HUM["shots"]:
        d0, d1 = shot["from"]["d"], shot["to"]["d"]
        mid_d = (max(d0, DELIV_T0) + min(d1, DELIV_T0 + 90.0)) / 2.0
        sync = SYNC_T0 + round((mid_d - DELIV_T0) * FPS)
        ref = frame(DELIV, mid_d)
        scores = {name: ncc(ref, frame(path, (sync - zero) / FPS))
                  for name, (path, zero) in CAMS.items()}
        pick = max(scores, key=lambda k: scores[k])
        rows.append({
            "window_t": round(mid_d - DELIV_T0, 2), "seconds": shot["seconds"],
            "sync": sync, "scores": {k: round(v, 3) for k, v in scores.items()},
            "angle": pick, "margin": round(scores[pick] - min(scores.values()), 3),
        })
        print(f"  t={rows[-1]['window_t']:7.2f} shot {shot['seconds']:6.2f}s -> {pick:>4} "
              f"FX6 {scores['FX6']:+.3f} A7IV {scores['A7IV']:+.3f}", flush=True)
    secs = {"FX6": 0.0, "A7IV": 0.0}
    for r in rows:
        secs[r["angle"]] += r["seconds"]
    total = sum(secs.values())
    print("human share by matched angle:",
          {k: f"{v:.1f}s {100 * v / total:.0f}%" for k, v in secs.items()}, flush=True)
    OUT.write_text(json.dumps({"rows": rows, "share_seconds": secs}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
