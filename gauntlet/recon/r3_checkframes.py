"""Pull check frames out of the finished R3 render, at the moments round 2 lost on."""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "gauntlet" / "renders" / "taurus_opening_r3.mp4"
OUT = HERE / "r3_check"
TIMES = [1.2, 16.0, 22.0, 42.5, 47.0, 62.0, 80.5, 86.0]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for t in TIMES:
        dest = OUT / f"t{t:05.1f}.jpg"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(SRC),
                "-frames:v", "1", "-q:v", "3", "-vf", "scale=960:-2", "-y", str(dest),
            ],
            check=True,
        )
        print(dest, flush=True)


if __name__ == "__main__":
    main()
