"""Extract the concert-pillar event inputs for the Taurus People opening window.

Reads the four analysis documents (solos changes, drum fills, phrase boundaries, energy) and
writes gauntlet/recon/taurus_events.json: every event inside 3568.48-3658.48 mix-seconds, each
carried in BOTH mix-seconds and Zinc-SYNC frames.

Frame mapping (given): frame = 86401 + round(seconds * 23.976).

Nothing here decides a cut. The ranking at the end is an ordering hint with its own arithmetic
written down beside it, so a director who disagrees can see exactly what it weighed.

READ-ONLY: no Resolve, no jobs — four files in, one file out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECON = Path(__file__).parent
ANALYSIS = Path(r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis")
OUT = RECON / "taurus_events.json"
RUN = RECON / "taurus_analysis.json"

SPAN = (3568.48, 4066.15)
WINDOW = (3568.48, 3658.48)

FPS = 23.976
FRAME_ZERO = 86401
"""Zinc-SYNC frame at mix-second 0."""

ENERGY = ANALYSIS / "Zinc-Set-2-Reaper-v4-0b66b71707de-energy.json"

PEAK_RADIUS = 3.0
"""A window is a peak only if nothing louder sits within this many seconds either side."""
PEAK_PROMINENCE_DB = 1.5
"""How far it has to stand above the quietest window within twice that radius."""
COINCIDENCE = 0.6
"""Two events this close are the same moment heard by two detectors."""

SPACING = 4.0
"""How far apart two picks in `top` have to be. A cluster is one moment, not five cuts."""

BASE = {
    "solo_change:lead": 0.80,
    "solo_change:timbre": 0.60,
}
"""Solo changes carry no confidence of their own, so the kind is the evidence.

A `lead` change is one stem measurably taking the front off another — the cut a director names
out loud. A `timbre` step is a handover *inside* one stem, which is real but weaker: on a
reading taken off `other` it could be the horn giving way to the piano, or the same horn
changing register. Both sit above the median fill (0.47) and below the strongest of them.
"""


def frame(seconds: float) -> int:
    return FRAME_ZERO + round(seconds * FPS)


def timed(seconds: float | None) -> dict[str, Any] | None:
    if seconds is None:
        return None
    return {"t": round(float(seconds), 3), "frame": frame(float(seconds))}


def inside(seconds: float | None, span: tuple[float, float] = WINDOW) -> bool:
    return seconds is not None and span[0] <= float(seconds) <= span[1]


def document(path: Path, field: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = list(raw.get(field) or [])
    header = {k: v for k, v in raw.items() if k != field}
    return header, rows


def paths_from_run() -> dict[str, Path]:
    """Where the three jobs wrote. The run record is the only place that knows."""
    run = json.loads(RUN.read_text(encoding="utf-8"))
    jobs = run["jobs"]
    structure = jobs["analyze_structure"]["result"]
    return {
        "solos": Path(structure["solos"]["path"]),
        "fills": Path(jobs["detect_drum_fills"]["result"]["path"]),
        "phrases": Path(jobs["detect_phrases"]["result"]["path"]),
    }


def energy_peaks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Local loudness maxima inside the window, with the prominence that made each one."""
    reach = 2 * PEAK_RADIUS
    near = [r for r in rows if WINDOW[0] - reach <= r["t"] <= WINDOW[1] + reach]
    peaks: list[dict[str, Any]] = []
    for row in near:
        if not inside(row["t"]):
            continue
        neighbours = [
            other
            for other in near
            if abs(other["t"] - row["t"]) <= PEAK_RADIUS and other is not row
        ]
        wider = [other for other in near if abs(other["t"] - row["t"]) <= reach]
        if not neighbours or not wider:
            continue
        if row["lufs"] < max(one["lufs"] for one in neighbours):
            continue
        prominence = row["lufs"] - min(one["lufs"] for one in wider)
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
    """An ordering hint, not a verdict.

    score = base + 0.20 per other event within COINCIDENCE seconds (capped at 0.4)
                 + 0.15 when the detector snapped it to a downbeat

    base is the detector's own confidence where it has one (fills, phrases), prominence_db / 6
    capped at 1 for an energy peak, and BASE[kind:signal] for a solo change, which has none.
    Two detectors agreeing on a moment is the strongest evidence available here, which is why
    coincidence outweighs the downbeat bonus.
    """
    scored: list[dict[str, Any]] = []
    for one in events:
        base = BASE.get(f"{one['kind']}:{one.get('signal')}", float(one.get("confidence") or 0.0))
        agree = [
            other
            for other in events
            if other is not one and abs(other["t"] - one["t"]) <= COINCIDENCE
        ]
        bonus = min(0.2 * len(agree), 0.4) + (0.15 if one.get("downbeat") else 0.0)
        scored.append(
            {
                **one,
                "base": round(base, 3),
                "agrees_with": sorted({other["kind"] for other in agree}),
                "score": round(base + bonus, 3),
            }
        )
    return sorted(scored, key=lambda one: (-one["score"], one["t"]))


def spaced(ranked: list[dict[str, Any]], keep: int = 8) -> list[dict[str, Any]]:
    """The best events no two of which are the same moment.

    Straight top-N off `ranked` returns a cluster — a phrase ending, the fill under it and the
    energy peak beside it are one place to cut, not three — so a pick suppresses everything
    within SPACING of it. The full ordering stays in `ranked`; this is only the shortlist.
    """
    picked: list[dict[str, Any]] = []
    for one in ranked:
        if any(abs(one["t"] - other["t"]) < SPACING for other in picked):
            continue
        picked.append(one)
        if len(picked) == keep:
            break
    return picked


def main() -> None:
    found = paths_from_run()
    solos_header, solos_rows = document(found["solos"], "solos")
    fills_header, fills_rows = document(found["fills"], "fills")
    phrases_header, phrases_rows = document(found["phrases"], "phrases")
    energy_raw = json.loads(ENERGY.read_text(encoding="utf-8"))

    # --- solo / front changes -------------------------------------------------------
    changes = []
    for row in solos_rows:
        if not (inside(row.get("t")) or inside(row.get("measured_t"))):
            continue
        changes.append(
            {
                "kind": "solo_change",
                "change": row.get("change"),
                **timed(row["t"]),  # type: ignore[dict-item]
                "measured": timed(row.get("measured_t")),
                "downbeat": row.get("downbeat"),
                "signal": row.get("signal"),
                "from": row.get("from"),
                "to": row.get("to"),
                "detail": row.get("detail"),
            }
        )
    before = [row for row in solos_rows if float(row["t"]) < WINDOW[0]]
    front_at_open = (
        {
            "since": timed(before[-1]["t"]),
            "stem": before[-1].get("to"),
            "signal": before[-1].get("signal"),
        }
        if before
        else None
    )

    # --- drum fills -----------------------------------------------------------------
    fills = []
    for row in fills_rows:
        if not inside(row.get("start")):
            continue
        fills.append(
            {
                "kind": "drum_fill",
                **timed(row["start"]),  # type: ignore[dict-item]
                "end": timed(row.get("end")),
                # A bar NUMBER, not a time — the bar the fill lands into. Timing it would put a
                # frame four times the length of the concert into a document about 90 seconds.
                "resolves_into_bar": row.get("resolves_into_bar"),
                "duration": row.get("duration"),
                "bar": row.get("bar"),
                "beat": row.get("beat"),
                "in_bar": row.get("in_bar"),
                "hits": row.get("hits"),
                "density_ratio": row.get("density_ratio"),
                "confidence": row.get("confidence"),
                "counts": {
                    name: row.get(name)
                    for name in ("kick", "snare", "toms", "ride", "crash")
                    if name in row
                },
                "factors": row.get("factors"),
            }
        )

    # --- phrase boundaries ----------------------------------------------------------
    boundaries = []
    for row in phrases_rows:
        if not (inside(row.get("t")) or inside(row.get("measured_t"))):
            continue
        boundaries.append(
            {
                "kind": "phrase_boundary",
                **timed(row["t"]),  # type: ignore[dict-item]
                "measured": timed(row.get("measured_t")),
                "resumes": timed(row.get("resumes_t")),
                "snapped": row.get("snapped"),
                "downbeat": row.get("downbeat"),
                "bar": row.get("bar"),
                "in_bar": row.get("in_bar"),
                "rest_seconds": row.get("rest_seconds"),
                "held_ratio": row.get("held_ratio"),
                "interval_semitones": row.get("interval_semitones"),
                "confidence": row.get("confidence"),
                "factors": row.get("factors"),
            }
        )

    # --- energy ---------------------------------------------------------------------
    rows = list(energy_raw["energy"])
    peaks = energy_peaks(rows)
    in_window = [r for r in rows if inside(r["t"])]
    loudest = max(in_window, key=lambda one: one["lufs"]) if in_window else None
    quietest = min(in_window, key=lambda one: one["lufs"]) if in_window else None

    ranked_input = [
        *changes,
        *fills,
        *boundaries,
        *[
            {
                "kind": "energy_peak",
                **{k: v for k, v in one.items() if k != "prominence_db"},
                "prominence_db": one["prominence_db"],
                "confidence": min(one["prominence_db"] / 6.0, 1.0),
            }
            for one in peaks
        ],
    ]
    ranked = rank(ranked_input)
    shortlist = spaced(ranked)

    # Density of the rest of the piece, so a reader knows what the window is a sample of.
    rest = {
        "solo_changes": sum(1 for row in solos_rows if inside(row.get("t"), SPAN)),
        "drum_fills": sum(1 for row in fills_rows if inside(row.get("start"), SPAN)),
        "phrase_boundaries": sum(1 for row in phrases_rows if inside(row.get("t"), SPAN)),
    }

    report = {
        "kind": "taurus_events",
        "song": "Taurus People",
        "span": {
            "mix_seconds": list(SPAN),
            "frames": [frame(SPAN[0]), frame(SPAN[1])],
        },
        "window": {
            "label": "opening piece",
            "mix_seconds": list(WINDOW),
            "frames": [frame(WINDOW[0]), frame(WINDOW[1])],
            "seconds": round(WINDOW[1] - WINDOW[0], 3),
        },
        "frame_mapping": {
            "formula": "frame = 86401 + round(seconds * 23.976)",
            "fps": FPS,
            "frame_at_mix_zero": FRAME_ZERO,
        },
        "sources": {name: str(path) for name, path in found.items()} | {"energy": str(ENERGY)},
        "reading": {
            "voices": solos_header.get("voices"),
            "timbre_stem": solos_header.get("timbre_stem"),
            "phrase_stem": phrases_header.get("stem"),
            "fill_stems": fills_header.get("stems"),
            "solo_changes_total": solos_header.get("count"),
            "fills_total": fills_header.get("count"),
            "phrases_total": phrases_header.get("count"),
        },
        "counts": {
            "solo_changes": len(changes),
            "drum_fills": len(fills),
            "phrase_boundaries": len(boundaries),
            "energy_peaks": len(peaks),
            "total": len(ranked_input),
        },
        "counts_whole_span": rest,
        "front_at_window_open": front_at_open,
        "solo_changes": changes,
        "drum_fills": fills,
        "phrase_boundaries": boundaries,
        "energy": {
            "window_seconds": energy_raw.get("window_seconds"),
            "hop_seconds": energy_raw.get("hop_seconds"),
            "concert_integrated_lufs": energy_raw.get("integrated_lufs"),
            "loudest_in_window": (
                {**timed(loudest["t"]), "lufs": loudest["lufs"]} if loudest else None
            ),
            "quietest_in_window": (
                {**timed(quietest["t"]), "lufs": quietest["lufs"]} if quietest else None
            ),
            "peaks": peaks,
        },
        "shortlist": shortlist,
        "ranked": ranked,
        "ranking_rule": (
            "score = base + 0.20 per other event within 0.6 s (max 0.4) + 0.15 when the "
            "detector snapped it to a downbeat. base is the detector's confidence for fills "
            "and phrases, prominence_db / 6 (capped at 1) for an energy peak, and "
            f"{BASE} for a solo change, which carries none. `shortlist` is `ranked` with "
            f"everything within {SPACING} s of a better event suppressed, because a cluster "
            "is one moment rather than several cuts."
        ),
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["counts"], indent=2))
    for one in shortlist[:5]:
        print(
            one["kind"],
            one["t"],
            one["frame"],
            one["score"],
            one.get("signal") or one.get("confidence"),
            one.get("agrees_with"),
        )


if __name__ == "__main__":
    main()
