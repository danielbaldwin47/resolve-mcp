"""Pixel check + tail proof on the R2 whole-song render. READ-ONLY.

The instrument is full_pixelcheck.py unchanged -- same ffprobe, same 0.10 scene detection,
same authored-boundary pairing, same luma-ramp and RMS-ladder tail proof. Only the three paths
move, because a second copy of that logic would drift from the one the winning pieces were
checked with and then the two rounds would not be comparable.

The tail constants stay put: R2 keeps R1's tail block frame for frame (dissolve 142, audio fade
125) over the same 11928-frame picture, which validate_cut re-confirmed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent


def load(path: Path) -> ModuleType:
    """gauntlet/recon is a folder of scripts, not a package, so import it by path."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    check = load(HERE / "full_pixelcheck.py")

    check.RENDER = HERE.parents[1] / "gauntlet" / "renders" / "taurus_full_p4r2.mp4"
    check.OUT = HERE / "p4r2_pixelcheck.json"
    check.CUT = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people-full-r2.cut.json"
    # R1's luma and RMS dumps are its receipts; R2 writes its own rather than over them.
    check.LUMA_NAME, check.RMS_NAME = "p4r2_luma.txt", "p4r2_rms.txt"
    check.main()


if __name__ == "__main__":
    main()
