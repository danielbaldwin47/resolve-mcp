"""Find the densest 90 s window in the MIDDLE THIRD of Taurus People. READ-ONLY.

The opening (first 90 s) and the ending (last 90 s) are already-won pieces, so piece 3 has to
sit between them. This scans every 90 s window whose whole length lies inside the middle third
(mix 3734.37-3900.26) at 0.5 s steps and scores it on what a cutter actually uses: solo/front
changes first, then fills and phrase boundaries, then how much the loudness moves across it.

It decides nothing. It prints the ordering and the constraint checks (does the window contain a
front change, does it contain the song's structural mid-peak) so the pick is made with the
numbers in view.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECON = Path(__file__).parent
ANALYSIS = Path(r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis")
RUN = RECON / "taurus_analysis.json"
ENERGY = ANALYSIS / "Zinc-Set-2-Reaper-v4-0b66b71707de-energy.json"

SPAN = (3568.48, 4066.15)
LENGTH = 90.0
THIRD = (SPAN[0] + (SPAN[1] - SPAN[0]) / 3.0, SPAN[0] + 2 * (SPAN[1] - SPAN[0]) / 3.0)
STEP = 0.5

FPS = 23.976
FRAME_ZERO = 86401


def frame(seconds: float) -> int:
    return FRAME_ZERO + round(seconds * FPS)


def paths_from_run() -> dict[str, Path]:
    run = json.loads(RUN.read_text(encoding="utf-8"))
    jobs = run["jobs"]
    return {
        "solos": Path(jobs["analyze_structure"]["result"]["solos"]["path"]),
        "fills": Path(jobs["detect_drum_fills"]["result"]["path"]),
        "phrases": Path(jobs["detect_phrases"]["result"]["path"]),
    }


def rows(path: Path, field: str) -> list[dict[str, Any]]:
    return list(json.loads(path.read_text(encoding="utf-8")).get(field) or [])


def main() -> None:
    found = paths_from_run()
    solos = rows(found["solos"], "solos")
    fills = rows(found["fills"], "fills")
    phrases = rows(found["phrases"], "phrases")
    all_energy = json.loads(ENERGY.read_text(encoding="utf-8"))["energy"]
    energy = [r for r in all_energy if SPAN[0] <= r["t"] <= SPAN[1]]

    song_solos = [r for r in solos if SPAN[0] <= r["t"] <= SPAN[1]]
    print("SONG front changes:")
    for r in song_solos:
        print(
            f"  t={r['t']:9.2f}  d={r['t'] - SPAN[0]:7.2f}  {r.get('signal'):6s} "
            f"{r.get('from')}->{r.get('to')}  detail={r.get('detail')}"
        )
    print(
        f"MIDDLE THIRD mix {THIRD[0]:.2f}-{THIRD[1]:.2f}  "
        f"deliverable {THIRD[0] - SPAN[0]:.2f}-{THIRD[1] - SPAN[0]:.2f}"
    )

    # The song's structural mid-peak: loudest 3 s energy window in the middle third.
    mid_rows = [r for r in energy if THIRD[0] <= r["t"] <= THIRD[1]]
    mid_peak = max(mid_rows, key=lambda r: r["lufs"])
    song_peak = max(energy, key=lambda r: r["lufs"])
    print(
        f"MID-PEAK  t={mid_peak['t']:.2f} lufs={mid_peak['lufs']}  "
        f"(song peak t={song_peak['t']:.2f} lufs={song_peak['lufs']})"
    )
    busiest = max(mid_rows, key=lambda r: r["onsets_per_second"])
    print(
        f"MID busiest onsets t={busiest['t']:.2f} "
        f"ops={busiest['onsets_per_second']} lufs={busiest['lufs']}"
    )

    cands: list[dict[str, Any]] = []
    start = THIRD[0]
    while start + LENGTH <= THIRD[1] + 1e-9:
        end = start + LENGTH
        s = [r for r in solos if start <= r["t"] <= end]
        f = [r for r in fills if start <= r["start"] <= end]
        p = [r for r in phrases if start <= r["t"] <= end]
        e = [r for r in energy if start <= r["t"] <= end]
        lufs = [r["lufs"] for r in e]
        ops = [r["onsets_per_second"] for r in e]
        strong_f = [r for r in f if (r.get("confidence") or 0) >= 0.60]
        strong_p = [r for r in p if (r.get("confidence") or 0) >= 0.60]
        score = (
            3.0 * len(s)
            + 0.10 * len(strong_f)
            + 0.05 * len(strong_p)
            + 0.5 * ((max(lufs) - min(lufs)) / 6.0)
            + 1.0 * (1.0 if start <= mid_peak["t"] <= end else 0.0)
        )
        cands.append(
            {
                "start": round(start, 2),
                "end": round(end, 2),
                "solo_changes": len(s),
                "fills": len(f),
                "strong_fills": len(strong_f),
                "phrases": len(p),
                "strong_phrases": len(strong_p),
                "lufs_range": round(max(lufs) - min(lufs), 2),
                "mean_lufs": round(sum(lufs) / len(lufs), 2),
                "mean_ops": round(sum(ops) / len(ops), 2),
                "has_mid_peak": start <= mid_peak["t"] <= end,
                "score": round(score, 3),
            }
        )
        start += STEP

    ranked = sorted(cands, key=lambda c: (-c["score"], c["start"]))
    print(f"\n{len(cands)} candidate windows; top 15 by score:")
    for c in ranked[:15]:
        print(
            f"  {c['start']:8.2f}-{c['end']:8.2f} "
            f"(d {c['start'] - SPAN[0]:6.2f}-{c['end'] - SPAN[0]:6.2f}) "
            f"score={c['score']:6.3f} solos={c['solo_changes']} "
            f"fills={c['fills']}/{c['strong_fills']} "
            f"phr={c['phrases']}/{c['strong_phrases']} "
            f"lufs_rng={c['lufs_range']:5.2f} mean={c['mean_lufs']:6.2f} "
            f"ops={c['mean_ops']:5.2f} peak={c['has_mid_peak']}"
        )
    print("\nwith solo change, ordered by fills+phrases:")
    withsolo = [c for c in cands if c["solo_changes"] >= 1]
    for c in sorted(withsolo, key=lambda c: -(c["strong_fills"] + c["strong_phrases"]))[:10]:
        print(
            f"  {c['start']:8.2f}-{c['end']:8.2f} solos={c['solo_changes']} sf={c['strong_fills']} "
            f"sp={c['strong_phrases']} peak={c['has_mid_peak']} score={c['score']:.3f}"
        )
    (RECON / "mid_window_pick.json").write_text(
        json.dumps(
            {
                "third": [round(THIRD[0], 2), round(THIRD[1], 2)],
                "mid_peak": mid_peak,
                "song_peak": song_peak,
                "song_solo_changes": song_solos,
                "candidates": ranked,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print("frames check:", frame(THIRD[0]), frame(THIRD[1]))


if __name__ == "__main__":
    main()
