"""Round-2 recon: FX6 move runs in detail + settled frame grabs on each plateau.

READ-ONLY apart from the jpgs it writes into r2_pictures/.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRABDIR = HERE / "r2_pictures"
FPS = 24000.0 / 1001.0
REC_IN = 181733
FX6_ZERO, A7_ZERO = 117576, 86306

FX6 = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF"
A7 = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4"

FX6_TIMES = [5.0, 18.0, 22.0, 31.0, 35.0, 43.0, 56.0, 66.0, 74.0]
A7_TIMES = [83.4]


def grab(path: str, zero: int, t: float, tag: str) -> None:
    GRABDIR.mkdir(exist_ok=True)
    src = REC_IN + round(t * FPS) - zero
    out = GRABDIR / f"{tag}_{t:06.2f}.jpg"
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-ss", f"{src / FPS:.4f}", "-i", path,
        "-frames:v", "1", "-vf", "scale=640:-1", "-q:v", "4", str(out),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    print("grabbed", out.name, "src_frame", src)


def main() -> None:
    rep = json.loads((HERE / "end_p2_recon.json").read_text(encoding="utf-8"))
    for name in ("FX6", "A7IV"):
        m = rep["angles"][name] if "angles" in rep else rep[name]
        print(f"--- {name} moving_share={m['moving_share']}")
        for r in m["move_runs"]:
            print(f"   {r['start']:7.2f} -> {r['end']:7.2f}  {r['seconds']:5.2f}s  "
                  f"net_dx={r['net_dx']:6.1f} net_dy={r['net_dy']:6.1f}")
    for t in FX6_TIMES:
        grab(FX6, FX6_ZERO, t, "FX6")
    for t in A7_TIMES:
        grab(A7, A7_ZERO, t, "A7IV")


if __name__ == "__main__":
    main()
