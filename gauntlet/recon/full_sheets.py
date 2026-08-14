"""Contact sheets of both angles across the two unjudged Taurus spans. READ-ONLY.

One tiled JPEG per angle per span, each cell a DELIVERABLE second printed with the sheet, so the
framing map (what each camera is pointing at, and when the operator has reframed) can be read in
four images instead of fifty. ffmpeg only - no Resolve.

SYNC = 171959 + round(d * 23.976); FX6 clip = SYNC - 117576; A7IV clip = SYNC - 86306.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

OUT = Path(__file__).with_name("full_sheets")
FPS = 24000.0 / 1001.0
DELIV_ZERO_SYNC = 171959

FX6_PATH = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\FX6\Card 2\XDROOT\Clip\A015C001_2606170J.MXF"  # noqa: E501
A7_PATH = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Footage\A7IV\M4ROOT\CLIP\20260617_D_A7IV_0006.MP4"  # noqa: E501

ANGLES = {
    "FX6": {"path": FX6_PATH, "offset": 117576},
    "A7IV": {"path": A7_PATH, "offset": 86306},
}

SHEETS = [
    ("A", [90 + 8 * i for i in range(10)]),
    ("B1", [258 + 10 * i for i in range(8)]),
    ("B2", [338 + 10 * i for i in range(8)]),
]


def grab(path: str, offset: int, d: float, dest: Path) -> None:
    src = DELIV_ZERO_SYNC + round(d * FPS) - offset
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-ss", f"{src / FPS:.4f}", "-i", path,
            "-frames:v", "1", "-vf", "scale=480:-1", str(dest),
        ],
        check=True,
        capture_output=True,
    )


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for label, times in SHEETS:
        for name, cfg in ANGLES.items():
            cells = []
            for i, d in enumerate(times):
                dest = OUT / f"{label}_{name}_{i:02d}.jpg"
                grab(str(cfg["path"]), int(cfg["offset"]), float(d), dest)
                cells.append(dest)
            sheet = OUT / f"sheet_{label}_{name}.jpg"
            cols = 2
            rows = (len(cells) + cols - 1) // cols
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-y",
                    "-framerate", "1", "-i", str(OUT / f"{label}_{name}_%02d.jpg"),
                    "-vf", f"tile={cols}x{rows}", "-frames:v", "1", "-q:v", "4", str(sheet),
                ],
                check=True,
                capture_output=True,
            )
            print("wrote", sheet, "cells (row-major, 2 per row):", times, flush=True)


if __name__ == "__main__":
    main()
