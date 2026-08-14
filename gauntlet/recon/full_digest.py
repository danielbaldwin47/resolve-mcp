"""Print the event digest for one deliverable span of Taurus People. READ-ONLY.

Usage: uv run python gauntlet/recon/full_digest.py <d_from> <d_to>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

RECON = Path(__file__).parent
DOC = json.loads((RECON / "full_events.json").read_text(encoding="utf-8"))


def within(rows: list[dict[str, Any]], a: float, b: float) -> list[dict[str, Any]]:
    return [r for r in rows if a <= r["d"] <= b]


def main() -> None:
    a, b = float(sys.argv[1]), float(sys.argv[2])
    print(f"=== span d{a} .. d{b} ({b - a:.2f}s) ===")

    print("-- SOLO CHANGES (whole song, for context) --")
    for r in DOC["solo_changes"]:
        mark = "*" if a <= r["d"] <= b else " "
        print(
            f" {mark} d{r['d']:8.2f} f{r['frame']} {r['change']:>12} {r.get('from')}->{r.get('to')}"
            f" sig={r.get('signal')} db={r.get('downbeat')}"
        )

    print("-- ENERGY PEAKS --")
    for r in within(DOC["energy_peaks"], a, b):
        print(
            f"   d{r['d']:8.2f} f{r['frame']} prom {r['prominence_db']:+5.2f} "
            f"lufs {r['lufs']:7.2f} ons {r['onsets_per_second']:5.2f}"
        )

    print("-- DRUM FILLS (conf >= 0.45) --")
    for r in within(DOC["drum_fills"], a, b):
        if (r.get("confidence") or 0) < 0.45:
            continue
        print(
            f"   d{r['d']:8.2f} f{r['frame']} dur {r.get('duration'):5.2f} "
            f"hits {r.get('hits'):4} conf {r.get('confidence'):.3f}"
        )

    print("-- PHRASE BOUNDARIES (conf >= 0.60) --")
    for r in within(DOC["phrase_boundaries"], a, b):
        if (r.get("confidence") or 0) < 0.60:
            continue
        print(
            f"   d{r['d']:8.2f} f{r['frame']} conf {r.get('confidence'):.3f} "
            f"db={r.get('downbeat')} rest {r.get('rest_seconds')} held {r.get('held_ratio')}"
        )

    print("-- RANKED TOP 40 IN SPAN --")
    n = 0
    for r in DOC["ranked"]:
        if not (a <= r["d"] <= b):
            continue
        print(
            f"   d{r['d']:8.2f} f{r['frame']} {r['kind']:16} score {r['score']:.3f} "
            f"agrees {r['agrees_with']}"
        )
        n += 1
        if n >= 40:
            break


if __name__ == "__main__":
    main()
