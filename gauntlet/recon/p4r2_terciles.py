"""Which loudness tercile each shot landed in -- the read behind ``sub2s_in_loud``.

The gears block reports the fraction and not the places, and the R2 gate is about the places:
sub-2 s accents belong in the loud third. This re-runs correlate's own tercile split over the
same level curve so a shot can be moved to a window that actually ranks loud.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
SONG_T0 = 3568.485


def main() -> None:
    from resolve_mcp.analysis import correlate as C

    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    rows = report["cuts"] if isinstance(report.get("cuts"), list) else report["shots"]
    levels = C._levels(Path(MIX), None)
    assert levels is not None

    span_start = min(float(r["t"]) for r in rows)
    span_end = max(float(r["t"]) + float(r["seconds"]) for r in rows)
    windows = [
        (s, lv) for s, lv in levels
        if s < span_end and s + C.GEAR_WINDOW_SECONDS > span_start
    ]
    placed = C._terciles(windows)
    starts = [s for s, _ in windows]

    print(f"windows={len(windows)}  span={span_start:.2f}..{span_end:.2f}")
    counts = {C.QUIET: 0, C.MID: 0, C.LOUD: 0}
    for r in rows:
        gear = placed[C._window_at(starts, float(r["t"]))]
        counts[gear] += 1
        d = float(r["t"]) - SONG_T0
        secs = float(r["seconds"])
        flag = "  <<< SUB-2" if secs < 2.0 else ""
        print(f"d={d:7.2f} {secs:6.2f}s {gear:5}{flag}")
    print(counts)

    # Where the loud windows are, in song time -- the map for placing accents.
    loud = [starts[i] - SONG_T0 for i, g in enumerate(placed) if g == C.LOUD]
    runs = []
    run = [loud[0], loud[0]]
    for d in loud[1:]:
        if d - run[1] <= 1.5:
            run[1] = d
        else:
            runs.append(tuple(run))
            run = [d, d]
    runs.append(tuple(run))
    print("\nLOUD-tercile runs (song d), >=3 s only:")
    for a, b in runs:
        if b - a >= 3.0:
            print(f"  {a:7.2f} - {b:7.2f}  ({b - a:5.1f}s)")


if __name__ == "__main__":
    main()
