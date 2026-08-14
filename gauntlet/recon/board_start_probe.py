"""Between the applause and the downbeat: what the mix does while the band is announced.

The applause curve puts a burst near every human tune start on the board mix, but the start
itself lands 2-66 s later — the gap is talking, tuning, a count-in. This asks which already
measured track shows that gap: the loudness/onset curve `analyze_music` writes, or the beat
grid it writes beside it. Prints a table per boundary. Usage:

    python board_start_probe.py <energy.json> <beats.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

TRUTH = (107.4405, 1373.8725, 1920.1265, 2725.014, 3568.4815)
APPLAUSE = (105.66, 1307.35, 1905.31, 2675.47, 3512.89)
"""Where the tallest applause excursion before each boundary sits (board_curve_probe.py)."""


def main() -> None:
    energy = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["energy"]
    beats = [row["t"] for row in json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))["beats"]]

    for burst, truth in zip(APPLAUSE, TRUTH, strict=True):
        print(f"\n== applause {burst:.1f} -> human start {truth:.1f} (gap {truth - burst:.1f}s) ==")
        print(f"{'t':>8} {'lufs':>8} {'rms':>8} {'onsets/s':>9} {'beats/s':>8}")
        start = burst - 20.0
        end = truth + 40.0
        step = 5.0
        time = start
        while time < end:
            rows = [one for one in energy if time <= one["t"] < time + step]
            if not rows:
                time += step
                continue
            lufs = max(one["lufs"] for one in rows)
            rms = max(one["rms_dbfs"] for one in rows)
            onsets = sum(one["onsets_per_second"] for one in rows) / len(rows)
            pulse = sum(1 for one in beats if time <= one < time + step) / step
            mark = " <-- human start" if time <= truth < time + step else ""
            print(f"{time:8.1f} {lufs:8.2f} {rms:8.2f} {onsets:9.2f} {pulse:8.2f}{mark}")
            time += step


if __name__ == "__main__":
    main()
