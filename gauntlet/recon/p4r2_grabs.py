"""Contact sheet over the windows where R2 flipped which camera is on screen. READ-ONLY.

The three carried occlusion vetoes are checked arithmetically in r2_revise.py, but the scan
that cleared the two angles only covers d90-166.68 and d256.68-407.66. R2's boundary edits
swap the angle across roughly d407-474 as well -- territory the whole-song scan never read --
so those seconds get looked at rather than assumed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
RENDER = HERE.parents[1] / "gauntlet" / "renders" / "taurus_full_p4r2.mp4"
OUT = HERE / "p4r2_grabs"

# Mid-shot samples inside each flipped span, in deliverable seconds.
SAMPLES = [
    (77.0, "floor a7iv (was fx6)"),
    (151.0, "breath a7iv (was fx6)"),
    (283.0, "plateau fx6 (was a7iv)"),
    (321.5, "build fx6 accent (was a7iv)"),
    (364.0, "fast a7iv (was fx6)"),
    (410.0, "summit a7iv (was fx6)"),
    (423.0, "summit a7iv (was fx6)"),
    (429.5, "summit fx6 (was a7iv)"),
    (438.5, "summit a7iv (was fx6)"),
    (447.5, "summit fx6 (was a7iv)"),
    (453.5, "summit a7iv (was fx6)"),
    (460.4, "summit a7iv accent (was fx6)"),
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    tiles = []
    for at, label in SAMPLES:
        dest = OUT / f"d{at:07.2f}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", str(at), "-i", str(RENDER),
             "-frames:v", "1", "-vf", "scale=640:-1", str(dest)],
            check=True,
        )
        tiles.append(str(dest))
        print(f"{dest.name}  {label}", flush=True)
    # tile= reads ONE stream, so the grabs are concatenated into a strip first; feeding it
    # twelve -i inputs silently tiles input 0 alone and the sheet reads as one frame plus black.
    sheet = OUT / "sheet.jpg"
    chain = "".join(f"[{i}:v]" for i in range(len(tiles)))
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *sum((["-i", t] for t in tiles), []),
         "-filter_complex",
         f"{chain}concat=n={len(tiles)}:v=1:a=0[s];"
         f"[s]tile=3x{(len(tiles) + 2) // 3}:margin=6:padding=6",
         "-frames:v", "1", str(sheet)],
        check=True,
    )
    print("sheet:", sheet, flush=True)


if __name__ == "__main__":
    main()
