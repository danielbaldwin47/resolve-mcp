"""Pull the frames the occlusion scan flagged across the two unjudged spans. READ-ONLY.

The ledger (occlusion_verdict_r3.json, occlusion_mid.json) says the SCORE does not separate a
true blocking from the FX6's dark piano lid or the A7IV's own foreground drummer, so every
flagged window is judged on frames: a veto needs a body covering a player the shot is framed on,
and it has to MOVE between the frames either side.

Sheets are 2x2 tiles at 640 wide; the cell order is printed with each sheet.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

OUT = Path(__file__).with_name("occl_full_frames")
FPS = 24000.0 / 1001.0
DELIV_ZERO_SYNC = 171959

FX6_PATH = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF"  # noqa: E501
A7_PATH = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4"  # noqa: E501

ANGLES = {
    "FX6": {"path": FX6_PATH, "offset": 117576},
    "A7IV": {"path": A7_PATH, "offset": 86306},
}

# Four frames per sheet: flagged sample plus its neighbours, so movement is visible.
SHEETS = [
    ("afx6_a", "FX6", [93.4, 100.5, 108.5, 115.4]),
    ("afx6_b", "FX6", [118.4, 122.5, 130.0, 138.0]),
    ("afx6_c", "FX6", [145.0, 152.0, 158.0, 163.0]),
    ("ba7_a", "A7IV", [265.1, 312.0, 313.1, 314.5]),
    ("ba7_b", "A7IV", [335.1, 348.0, 349.5, 350.5]),
    ("ba7_c", "A7IV", [388.1, 393.1, 398.2, 399.5]),
    ("ba7_d", "A7IV", [400.2, 401.5, 405.1, 406.5]),
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for label, angle, times in SHEETS:
        cfg = ANGLES[angle]
        for i, d in enumerate(times):
            src = DELIV_ZERO_SYNC + round(d * FPS) - int(cfg["offset"])
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-y", "-ss", f"{src / FPS:.4f}",
                    "-i", str(cfg["path"]), "-frames:v", "1", "-vf", "scale=640:-1",
                    str(OUT / f"{label}_{i:02d}.jpg"),
                ],
                check=True,
                capture_output=True,
            )
        sheet = OUT / f"sheet_{label}.jpg"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y", "-framerate", "1",
                "-i", str(OUT / f"{label}_%02d.jpg"), "-vf", "tile=2x2",
                "-frames:v", "1", "-q:v", "3", str(sheet),
            ],
            check=True,
            capture_output=True,
        )
        print("wrote", sheet, "cells row-major:", times, flush=True)


if __name__ == "__main__":
    main()
