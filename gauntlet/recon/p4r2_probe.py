"""Named events inside the two loud-tercile runs the R2 accents need to move into."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FPS = 24000 / 1001.0
SYNC0 = 171959

# (label, lo, hi, head of the shot the accent is carved from, tail)
WINDOWS = [
    ("finale.jab_a  (carve from 427.59)", 427.7, 429.59, 427.59),
    ("finale.jab_a' (carve from 431.60)", 431.7, 433.59, 431.60),
    ("finale.jab_b  (carve from 456.92)", 459.15, 461.05, 456.92),
]

ev = json.loads((ROOT / "gauntlet" / "recon" / "full_events.json").read_text(encoding="utf-8"))
onsets = json.loads((ROOT / "gauntlet" / "recon" / "full_onsets.json").read_text(encoding="utf-8"))[
    "onsets_d"
]
named: list[tuple[float, str, float]] = []
for e in ev["drum_fills"]:
    named.append((e["d"], "fill_start", e.get("confidence", 0.0)))
for e in ev["phrase_boundaries"]:
    named.append((e["d"], "phrase" + ("*" if e.get("downbeat") else ""), e.get("confidence", 0.0)))
for e in ev["energy_peaks"]:
    named.append((e["d"], "peak", e.get("prominence_db", 0.0)))
named.sort()

for label, lo, hi, head in WINDOWS:
    print(f"\n=== {label}  usable d {lo}-{hi} (shot head {head})")
    for d, kind, conf in named:
        if not lo <= d <= hi:
            continue
        near = sorted(onsets, key=lambda o: abs(o - d))[0]
        f = SYNC0 + int(near * FPS)
        dc = (f - SYNC0) / FPS
        print(
            f"  {kind:10} d={d:8.3f} conf={conf:6.3f} -> onset {near:8.4f} f={f} "
            f"d_cut={dc:8.3f} early={(near - dc) * 1000:4.0f}ms  shot={dc - head:5.2f}s"
        )
