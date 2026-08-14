"""Measure per-section cut gears for the P4R1 pack (human A vs ours B).

Sections are the song's own structure: energy legs from full_events.json
crossed with the solo_changes that mark the real handovers.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "gauntlet" / "packs" / "taurus_full_p4r1"
EVENTS = ROOT / "gauntlet" / "recon" / "full_events.json"

# (name, d_start, d_end) -- boundaries are energy-leg edges or solo handovers.
SECTIONS: list[tuple[str, float, float]] = [
    ("head", 0.00, 36.06),  # opening statement -> first timbre change
    ("floor", 36.06, 96.02),  # -15 dB decay into the quietest sustained music
    ("breath", 96.02, 153.52),  # slow rise under the bass solo
    ("trade", 153.52, 232.92),  # bass/drums trading
    ("plateau", 232.92, 294.52),  # band returns +12 dB, loud and level
    ("build", 294.52, 328.02),  # the 33.5 s climb, summit at 328.02
    ("fast", 328.02, 381.02),  # the fast zone
    ("summit", 381.02, 474.64),  # second climb + sustained finale
    ("ending", 474.64, 497.664),  # decay out
]


def lufs_for(span: tuple[float, float], profile: list[dict[str, Any]]) -> float:
    lo, hi = span
    vals = [w["mean_lufs"] for w in profile if lo <= w["d"] < hi]
    if not vals:
        vals = [w["mean_lufs"] for w in profile if lo - 10 <= w["d"] < hi + 10]
    return round(statistics.fmean(vals), 2)


def shots_from(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    """Shot spans from cut times: [0,c1), [c1,c2), ... [cn,duration)."""
    edges = [0.0, *cuts, duration]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def analyse(label: str) -> dict[str, Any]:
    data = json.loads((PACK / label / "cuts.json").read_text(encoding="utf-8"))
    duration = float(data["clip_duration_sec"])
    cuts = [float(t) for t in data["cut_times_sec"]]
    shots = shots_from(cuts, duration)
    lengths = [b - a for a, b in shots]

    profile = json.loads(EVENTS.read_text(encoding="utf-8"))["energy_profile_10s"]

    rows: list[dict[str, Any]] = []
    for name, lo, hi in SECTIONS:
        secs = hi - lo
        in_sec = [c for c in cuts if lo <= c < hi]
        # a shot belongs to the section its head sits in
        sec_shots = [(a, b) for a, b in shots if lo <= a < hi]
        sec_lens = [b - a for a, b in sec_shots]
        rows.append(
            {
                "section": name,
                "d_from": lo,
                "d_to": hi,
                "seconds": round(secs, 2),
                "mean_lufs": lufs_for((lo, hi), profile),
                "cuts": len(in_sec),
                "cuts_per_min": round(len(in_sec) / secs * 60, 2),
                "shots": len(sec_shots),
                "mean_shot_sec": round(statistics.fmean(sec_lens), 2) if sec_lens else None,
                "median_shot_sec": round(statistics.median(sec_lens), 2) if sec_lens else None,
                "min_shot_sec": round(min(sec_lens), 2) if sec_lens else None,
                "max_shot_sec": round(max(sec_lens), 2) if sec_lens else None,
                "sub2s": sum(1 for x in sec_lens if x < 2.0),
                "sub3s": sum(1 for x in sec_lens if x < 3.0),
            }
        )

    song_cpm = len(cuts) / duration * 60
    for r in rows:
        r["gear_vs_song"] = round(r["cuts_per_min"] / song_cpm, 2)

    sub2 = [(round(a, 2), round(b - a, 2)) for a, b in shots if (b - a) < 2.0]

    return {
        "label": label,
        "duration_sec": round(duration, 3),
        "cuts": len(cuts),
        "shots": len(shots),
        "song_cuts_per_min": round(song_cpm, 2),
        "shot_mean_sec": round(statistics.fmean(lengths), 2),
        "shot_median_sec": round(statistics.median(lengths), 2),
        "shot_sd_sec": round(statistics.pstdev(lengths), 2),
        "shot_cv": round(statistics.pstdev(lengths) / statistics.fmean(lengths), 3),
        "sub2s_total": len(sub2),
        "sub2s_shots": sub2,
        "sections": rows,
    }


def main() -> None:
    out: dict[str, Any] = {}
    for label in ("A", "B"):
        out[label] = analyse(label)

    a, b = out["A"], out["B"]
    quiet = ["head", "floor", "breath", "trade"]
    loud = ["plateau", "build", "fast", "summit"]

    def band(res: dict[str, Any], names: list[str]) -> dict[str, float]:
        rs = [r for r in res["sections"] if r["section"] in names]
        secs = sum(r["seconds"] for r in rs)
        cuts = sum(r["cuts"] for r in rs)
        return {
            "seconds": round(secs, 2),
            "cuts": cuts,
            "cuts_per_min": round(cuts / secs * 60, 2),
            "gear": round((cuts / secs * 60) / res["song_cuts_per_min"], 2),
        }

    for res in (a, b):
        res["quiet_band"] = band(res, quiet)
        res["loud_band"] = band(res, loud)
        qmin = min(r["cuts_per_min"] for r in res["sections"] if r["section"] in quiet)
        lmax = max(r["cuts_per_min"] for r in res["sections"] if r["section"] in loud)
        res["quietest_to_loudest_section_ratio"] = round(lmax / qmin, 2)
        res["band_ratio_loud_over_quiet"] = round(
            res["loud_band"]["cuts_per_min"] / res["quiet_band"]["cuts_per_min"], 2
        )

    dest = Path(__file__).with_suffix(".json")
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")

    for label in ("A", "B"):
        res = out[label]
        who = "HUMAN" if label == "A" else "OURS"
        print(f"\n=== {label} ({who}) ===")
        print(
            f"cuts={res['cuts']} shots={res['shots']} cpm={res['song_cuts_per_min']} "
            f"mean={res['shot_mean_sec']} median={res['shot_median_sec']} "
            f"CV={res['shot_cv']} sub2s={res['sub2s_total']}"
        )
        print(
            f"{'section':9} {'span':>15} {'LUFS':>7} {'cuts':>5} {'cpm':>6} "
            f"{'gear':>5} {'mean':>6} {'med':>6} {'min':>6} {'<2s':>4} {'<3s':>4}"
        )
        for r in res["sections"]:
            print(
                f"{r['section']:9} {r['d_from']:6.1f}-{r['d_to']:6.1f} "
                f"{r['mean_lufs']:7.2f} {r['cuts']:5d} {r['cuts_per_min']:6.2f} "
                f"{r['gear_vs_song']:5.2f} {r['mean_shot_sec'] or 0:6.2f} "
                f"{r['median_shot_sec'] or 0:6.2f} {r['min_shot_sec'] or 0:6.2f} "
                f"{r['sub2s']:4d} {r['sub3s']:4d}"
            )
        print(f"quiet band (head..trade): {res['quiet_band']}")
        print(f"loud  band (plateau..summit): {res['loud_band']}")
        print(f"band ratio loud/quiet: {res['band_ratio_loud_over_quiet']}")
        print(f"quietest section -> loudest section: {res['quietest_to_loudest_section_ratio']}")
        print(f"sub-2s shots (start, len): {res['sub2s_shots']}")


if __name__ == "__main__":
    main()
