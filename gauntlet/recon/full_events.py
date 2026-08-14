"""Whole-song event + energy-arc extraction for Taurus People (mix 3568.48-4066.15).

Reads the four cached analysis documents and writes gauntlet/recon/full_events.json:
every solo change, drum fill, phrase boundary and energy peak in the WHOLE song, each
carried in mix seconds, deliverable seconds and Zinc-SYNC frames -- plus an ENERGY ARC
map: the smoothed loudness curve reduced to legs (rise / fall / plateau) with their
prominences, which is the axis this round is judged on.

Frame mapping: SYNC = 86401 + round(mix_seconds * 23.976); deliverable_t = mix - 3568.48.

READ-ONLY: four files in, one file out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECON = Path(__file__).parent
ANALYSIS = Path(r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis")
OUT = RECON / "full_events.json"
RUN = RECON / "taurus_analysis.json"
ENERGY = ANALYSIS / "Zinc-Set-2-Reaper-v4-0b66b71707de-energy.json"

FPS = 24000.0 / 1001.0
FRAME_ZERO = 86401
SONG = (3568.48, 4066.15)
DELIV_ZERO = 3568.48

# The two unjudged spans, in deliverable seconds.
NEW_A = (90.0, 166.68)
NEW_B = (256.68, 407.68)

PEAK_RADIUS = 3.0
PEAK_PROMINENCE_DB = 1.5
COINCIDENCE = 0.6
BASE = {"solo_change:lead": 0.80, "solo_change:timbre": 0.60}


def frame(mix_t: float) -> int:
    return FRAME_ZERO + round(mix_t * FPS)


def dt(mix_t: float) -> float:
    return round(mix_t - DELIV_ZERO, 3)


def timed(mix_t: float | None) -> dict[str, Any] | None:
    if mix_t is None:
        return None
    m = float(mix_t)
    return {"t": round(m, 3), "d": dt(m), "frame": frame(m)}


def inside(mix_t: float | None, span: tuple[float, float] = SONG) -> bool:
    return mix_t is not None and span[0] <= float(mix_t) <= span[1]


def document(path: Path, field: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = list(raw.get(field) or [])
    return {k: v for k, v in raw.items() if k != field}, rows


def paths_from_run() -> dict[str, Path]:
    run = json.loads(RUN.read_text(encoding="utf-8"))
    jobs = run["jobs"]
    return {
        "solos": Path(jobs["analyze_structure"]["result"]["solos"]["path"]),
        "fills": Path(jobs["detect_drum_fills"]["result"]["path"]),
        "phrases": Path(jobs["detect_phrases"]["result"]["path"]),
    }


def smooth(rows: list[dict[str, Any]], width: float) -> list[dict[str, float]]:
    """Boxcar over the LUFS column, in seconds, so a leg is a shape and not a hop."""
    out: list[dict[str, float]] = []
    n = len(rows)
    for i, row in enumerate(rows):
        lo, hi = i, i
        while lo > 0 and rows[i]["t"] - rows[lo - 1]["t"] <= width / 2:
            lo -= 1
        while hi < n - 1 and rows[hi + 1]["t"] - rows[i]["t"] <= width / 2:
            hi += 1
        vals = [r["lufs"] for r in rows[lo : hi + 1]]
        out.append({"t": row["t"], "lufs": sum(vals) / len(vals)})
    return out


def legs(curve: list[dict[str, float]], step_db: float = 1.2) -> list[dict[str, Any]]:
    """Reduce the smoothed curve to monotone legs.

    A leg runs while the curve keeps going the same way; it ends when the curve turns
    back by more than `step_db` (a zig-zag threshold, so a wobble inside a build does
    not split it). Each leg carries its start/end loudness and its slope in dB/s.
    """
    if not curve:
        return []
    out: list[dict[str, Any]] = []
    start = curve[0]
    extreme = curve[0]
    direction = 0  # +1 rising, -1 falling, 0 unknown
    for pt in curve[1:]:
        if direction >= 0 and pt["lufs"] >= extreme["lufs"]:
            extreme = pt
            direction = 1 if pt["lufs"] > start["lufs"] else direction
            continue
        if direction <= 0 and pt["lufs"] <= extreme["lufs"]:
            extreme = pt
            direction = -1 if pt["lufs"] < start["lufs"] else direction
            continue
        if abs(pt["lufs"] - extreme["lufs"]) >= step_db:
            span = extreme["t"] - start["t"]
            out.append(
                {
                    "from": timed(start["t"]),
                    "to": timed(extreme["t"]),
                    "seconds": round(span, 2),
                    "lufs_from": round(start["lufs"], 2),
                    "lufs_to": round(extreme["lufs"], 2),
                    "delta_db": round(extreme["lufs"] - start["lufs"], 2),
                    "slope_db_per_s": round(
                        (extreme["lufs"] - start["lufs"]) / span if span else 0.0, 3
                    ),
                }
            )
            start = extreme
            extreme = pt
            direction = 1 if pt["lufs"] > start["lufs"] else -1
    span = extreme["t"] - start["t"]
    out.append(
        {
            "from": timed(start["t"]),
            "to": timed(extreme["t"]),
            "seconds": round(span, 2),
            "lufs_from": round(start["lufs"], 2),
            "lufs_to": round(extreme["lufs"], 2),
            "delta_db": round(extreme["lufs"] - start["lufs"], 2),
            "slope_db_per_s": round((extreme["lufs"] - start["lufs"]) / span if span else 0.0, 3),
        }
    )
    return out


def energy_peaks(rows: list[dict[str, Any]], span: tuple[float, float]) -> list[dict[str, Any]]:
    reach = 2 * PEAK_RADIUS
    near = [r for r in rows if span[0] - reach <= r["t"] <= span[1] + reach]
    peaks: list[dict[str, Any]] = []
    for row in near:
        if not inside(row["t"], span):
            continue
        neighbours = [o for o in near if abs(o["t"] - row["t"]) <= PEAK_RADIUS and o is not row]
        wider = [o for o in near if abs(o["t"] - row["t"]) <= reach]
        if not neighbours or not wider:
            continue
        if row["lufs"] < max(o["lufs"] for o in neighbours):
            continue
        prominence = row["lufs"] - min(o["lufs"] for o in wider)
        if prominence < PEAK_PROMINENCE_DB:
            continue
        if peaks and row["t"] - peaks[-1]["t"] <= PEAK_RADIUS:
            if row["lufs"] <= peaks[-1]["lufs"]:
                continue
            peaks.pop()
        peaks.append(
            {
                **timed(row["t"]),  # type: ignore[dict-item]
                "lufs": row["lufs"],
                "rms_dbfs": row["rms_dbfs"],
                "onsets_per_second": row["onsets_per_second"],
                "prominence_db": round(prominence, 2),
            }
        )
    return peaks


def rank(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for one in events:
        base = BASE.get(f"{one['kind']}:{one.get('signal')}", float(one.get("confidence") or 0.0))
        agree = [
            o for o in events if o is not one and abs(o["t"] - one["t"]) <= COINCIDENCE
        ]
        bonus = min(0.2 * len(agree), 0.4) + (0.15 if one.get("downbeat") else 0.0)
        scored.append(
            {
                **one,
                "base": round(base, 3),
                "agrees_with": sorted({o["kind"] for o in agree}),
                "score": round(base + bonus, 3),
            }
        )
    return sorted(scored, key=lambda one: (-one["score"], one["t"]))


def main() -> None:
    found = paths_from_run()
    _, solos_rows = document(found["solos"], "solos")
    _, fills_rows = document(found["fills"], "fills")
    _, phrases_rows = document(found["phrases"], "phrases")
    energy_raw = json.loads(ENERGY.read_text(encoding="utf-8"))
    rows = [r for r in energy_raw["energy"] if inside(r["t"])]

    changes = [
        {
            "kind": "solo_change",
            "change": r.get("change"),
            **timed(r["t"]),  # type: ignore[dict-item]
            "downbeat": r.get("downbeat"),
            "signal": r.get("signal"),
            "from": r.get("from"),
            "to": r.get("to"),
        }
        for r in solos_rows
        if inside(r.get("t"))
    ]
    fills = [
        {
            "kind": "drum_fill",
            **timed(r["start"]),  # type: ignore[dict-item]
            "end": timed(r.get("end")),
            "duration": r.get("duration"),
            "hits": r.get("hits"),
            "confidence": r.get("confidence"),
        }
        for r in fills_rows
        if inside(r.get("start"))
    ]
    boundaries = [
        {
            "kind": "phrase_boundary",
            **timed(r["t"]),  # type: ignore[dict-item]
            "downbeat": r.get("downbeat"),
            "rest_seconds": r.get("rest_seconds"),
            "held_ratio": r.get("held_ratio"),
            "confidence": r.get("confidence"),
        }
        for r in phrases_rows
        if inside(r.get("t"))
    ]
    peaks = energy_peaks(energy_raw["energy"], SONG)

    ranked = rank(
        [
            *changes,
            *fills,
            *boundaries,
            *[
                {
                    "kind": "energy_peak",
                    **{k: v for k, v in p.items() if k != "prominence_db"},
                    "prominence_db": p["prominence_db"],
                    "confidence": min(p["prominence_db"] / 6.0, 1.0),
                }
                for p in peaks
            ],
        ]
    )

    curve = smooth(rows, 4.0)
    arc = legs(curve, 1.2)
    loudest = sorted(rows, key=lambda r: -r["lufs"])[:20]
    quietest = sorted(rows, key=lambda r: r["lufs"])[:20]

    # A per-10 s energy profile so the arc is readable as a table, not only as legs.
    profile: list[dict[str, Any]] = []
    t = SONG[0]
    while t < SONG[1]:
        chunk = [r for r in rows if t <= r["t"] < t + 10.0]
        if chunk:
            profile.append(
                {
                    "d": round(t - DELIV_ZERO, 1),
                    "mean_lufs": round(sum(c["lufs"] for c in chunk) / len(chunk), 2),
                    "max_lufs": round(max(c["lufs"] for c in chunk), 2),
                    "min_lufs": round(min(c["lufs"] for c in chunk), 2),
                    "onsets": round(
                        sum(c["onsets_per_second"] for c in chunk) / len(chunk), 2
                    ),
                }
            )
        t += 10.0

    report = {
        "kind": "taurus_full_events",
        "song": "Taurus People",
        "span": {"mix_seconds": list(SONG), "frames": [frame(SONG[0]), frame(SONG[1])]},
        "new_spans_deliverable": {"A": list(NEW_A), "B": list(NEW_B)},
        "frame_mapping": {
            "formula": "SYNC = 86401 + round(mix_seconds * 23.976)",
            "deliverable_t": "mix_seconds - 3568.48",
            "fx6_clip": "SYNC - 117576",
            "a7iv_clip": "SYNC - 86306",
            "mix_clip": "SYNC - 86401",
        },
        "counts": {
            "solo_changes": len(changes),
            "drum_fills": len(fills),
            "phrase_boundaries": len(boundaries),
            "energy_peaks": len(peaks),
        },
        "energy_profile_10s": profile,
        "arc_legs": arc,
        "loudest_windows": [{**timed(r["t"]), "lufs": r["lufs"]} for r in loudest],
        "quietest_windows": [{**timed(r["t"]), "lufs": r["lufs"]} for r in quietest],
        "energy_peaks": peaks,
        "solo_changes": changes,
        "drum_fills": fills,
        "phrase_boundaries": boundaries,
        "ranked": ranked,
    }
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("COUNTS", json.dumps(report["counts"]))
    print("LEGS", len(arc))
    for leg in arc:
        print(
            f"  leg d{leg['from']['d']:7.2f} -> {leg['to']['d']:7.2f} "
            f"({leg['seconds']:6.2f}s) {leg['lufs_from']:7.2f} -> {leg['lufs_to']:7.2f} "
            f"delta {leg['delta_db']:+6.2f} slope {leg['slope_db_per_s']:+.3f}"
        )
    print("LOUDEST")
    for r in report["loudest_windows"][:10]:
        print(f"  d{r['d']:7.2f} {r['lufs']:.2f}")
    print("QUIETEST")
    for r in report["quietest_windows"][:10]:
        print(f"  d{r['d']:7.2f} {r['lufs']:.2f}")
    print("PROFILE")
    for p in profile:
        print(f"  d{p['d']:7.1f} mean {p['mean_lufs']:7.2f} max {p['max_lufs']:7.2f} "
              f"min {p['min_lufs']:7.2f} onsets {p['onsets']:5.2f}")


if __name__ == "__main__":
    main()
