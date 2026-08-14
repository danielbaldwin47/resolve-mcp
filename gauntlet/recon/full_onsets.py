"""Dump the whole-song onset list correlate_timeline measures against. READ-ONLY.

correlate computes its own transients from the mix (`energy.onsets(decode.read(...))`), so a
cut snapped against any other list disagrees with the self-review by up to 40 ms. This writes
every onset inside the song in DELIVERABLE seconds, which is the clock the plan is written in.

Writes gauntlet/recon/full_onsets.json.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "full_onsets.json"
MIX = Path(r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav")
T0 = 3568.48
SPAN = 497.7


def main() -> None:
    from resolve_mcp.analysis import decode, energy

    onsets = [float(s) for s in energy.onsets(decode.read(MIX))]
    rel = sorted(o - T0 for o in onsets if -1.0 <= o - T0 <= SPAN + 1.0)
    OUT.write_text(json.dumps({"t0": T0, "onsets_d": rel}, indent=1), encoding="utf-8")
    print("onsets in song:", len(rel), flush=True)


if __name__ == "__main__":
    main()
