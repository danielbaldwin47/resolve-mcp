"""Extract sample frames from the r1 render at claimed angle-change times."""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "gauntlet" / "renders" / "taurus_opening_r1.mp4"
DST = HERE / "r1_frames"
TIMES = [2.0, 5.5, 12.5, 19.5, 33.0, 37.5, 50.0, 65.0, 73.0, 85.0]


def main() -> None:
    DST.mkdir(exist_ok=True)
    for t in TIMES:
        out = DST / f"t{t:05.1f}.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(SRC),
             "-frames:v", "1", "-vf", "scale=640:-1", str(out)],
            check=True,
        )
        print("wrote", out.name)


if __name__ == "__main__":
    main()
