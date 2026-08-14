"""Dump the raw PANNs applause curve for a mix, once, so threshold work is offline.

Tagging a 74-minute set costs ~90 s on the GPU; every candidate rule after that is
arithmetic over the same numbers. So this writes the curve to an .npz and the sweeps read
it back. No Resolve calls. Usage: python board_curve_dump.py <wav> <out.npz>
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np


def main() -> None:
    source = Path(sys.argv[1])
    out = Path(sys.argv[2])
    from resolve_mcp.analysis import applause

    began = time.time()
    curve = applause.tag(source)
    np.savez_compressed(
        out,
        seconds=np.asarray(curve.seconds, dtype=np.float64),
        probability=np.asarray(curve.probability, dtype=np.float32),
    )
    print(
        f"DONE {out} frames={len(curve.seconds)} "
        f"peak={max(curve.probability):.4f} elapsed={time.time() - began:.1f}s"
    )


if __name__ == "__main__":
    main()
