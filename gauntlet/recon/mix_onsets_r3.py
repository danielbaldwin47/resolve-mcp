"""Dump the exact onset list correlate_timeline measures against, for the piece window.

correlate computes its own transients from the mix (`energy.onsets(decode.read(...))`)
rather than reading taurus_window.json, which is why the plan's snapped offsets and the
self-review's offsets disagree by up to 40 ms. Snapping against the same list closes the
gap. Writes mix_onsets_r3.json (window-relative seconds) and checks the round-3 v2 cut
frames against it, so the list is proven to be the one the report used before it is
trusted. READ-ONLY.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "mix_onsets_r3.json"
MIX = Path(r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav")
T0 = 3568.4815
SPAN = 90.1
FPS = 24000.0 / 1001.0

# (correlate cut number, its t rel, the offset the v2 report gave) - the proof rows.
CHECK = [
    (3, 10.39, 0.001),
    (4, 20.483, 0.036),
    (5, 25.113, 0.004),
    (8, 36.082, 0.040),
    (14, 75.83, 0.044),
]


def main() -> None:
    from resolve_mcp.analysis import decode, energy

    onsets = [float(s) for s in energy.onsets(decode.read(MIX))]
    rel = sorted(o - T0 for o in onsets if -1.0 <= o - T0 <= SPAN + 1.0)
    OUT.write_text(json.dumps({"t0": T0, "onsets_rel": rel}, indent=1), encoding="utf-8")
    print("onsets in window:", len(rel), flush=True)
    for cut, t, expect in CHECK:
        near = min(rel, key=lambda x: abs(x - t))
        print(f"  cut {cut:2d} t={t:7.3f} nearest={near:7.3f} off={1000 * (t - near):+6.1f} ms"
              f"  report said {1000 * expect:+6.1f} ms", flush=True)


if __name__ == "__main__":
    main()
