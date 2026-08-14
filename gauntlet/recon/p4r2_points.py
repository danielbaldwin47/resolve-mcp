"""Find motivated, onset-snapped cut points for the R2 gearbox revision.

For each target d the revision wants a cut near, list the named events (drum fill starts,
phrase boundaries, energy peaks) inside a window and the onset each would snap to, so the
chosen point is a struck accent rather than a stopwatch mark.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVENTS = ROOT / "gauntlet" / "recon" / "full_events.json"
ONSETS = ROOT / "gauntlet" / "recon" / "full_onsets.json"
FPS = 24000 / 1001.0
SYNC0 = 171959

TARGETS = [
    ("floor.release", 77.5, 2.5),
    ("plateau.jab", 269.2, 2.0),
    ("plateau.turn", 290.6, 2.5),
    ("build.accent_a", 320.85, 1.2),
    ("build.accent_b", 324.20, 1.2),
    ("fast.accent", 361.70, 1.5),
    ("summit.split", 406.00, 2.5),
    ("summit.accent_a", 435.70, 1.2),
    ("summit.accent_b", 437.40, 1.2),
    ("summit.accent_c", 440.40, 1.2),
    ("summit.accent_d", 442.10, 1.2),
    ("summit.accent_e", 462.90, 1.5),
]


def snap(onset_d: float) -> tuple[int, float]:
    """Largest SYNC frame whose d is at or before the onset -- the cut lands just early."""
    frame = SYNC0 + int(onset_d * FPS)
    return frame, round((onset_d - (frame - SYNC0) / FPS) * 1000, 1)


def main() -> None:
    ev = json.loads(EVENTS.read_text(encoding="utf-8"))
    onsets = json.loads(ONSETS.read_text(encoding="utf-8"))["onsets_d"]
    named: list[tuple[float, str, float]] = []
    for e in ev["drum_fills"]:
        named.append((e["d"], "fill_start", e.get("confidence", 0.0)))
    for e in ev["phrase_boundaries"]:
        kind = "phrase" + ("*" if e.get("downbeat") else "")
        named.append((e["d"], kind, e.get("confidence", 0.0)))
    for e in ev["energy_peaks"]:
        named.append((e["d"], "peak", e.get("prominence_db", 0.0)))
    named.sort()

    for label, target, half in TARGETS:
        print(f"\n=== {label}  target d={target}  window +-{half}")
        near = [n for n in named if abs(n[0] - target) <= half]
        for d, kind, conf in near:
            cand = [o for o in onsets if abs(o - d) <= 0.09]
            best = min(cand, key=lambda o: abs(o - d)) if cand else d
            frame, ms = snap(best)
            print(
                f"  {kind:9} d={d:8.3f} conf={conf:6.3f} -> onset {best:8.4f} "
                f"frame={frame} d_cut={(frame - SYNC0) / FPS:8.3f} early={ms:5.1f}ms"
            )
        if not near:
            print("  (no named event) nearest onsets:")
            cand = sorted(onsets, key=lambda o: abs(o - target))[:5]
            for o in sorted(cand):
                frame, ms = snap(o)
                print(
                    f"  onset     d={o:8.4f} frame={frame} "
                    f"d_cut={(frame - SYNC0) / FPS:8.3f} early={ms:5.1f}ms"
                )


if __name__ == "__main__":
    main()
