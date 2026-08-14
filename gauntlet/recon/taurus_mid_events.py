"""Event inputs for PIECE 3 — the MID window of Taurus People. READ-ONLY.

The opening (first 90 s) and the ending (last 90 s) are already-won pieces, so piece 3 is a new
90 s window inside the song's middle third (mix 3734.37-3900.26). `mid_window_pick.py` scanned
every 90 s window that fits wholly inside that third at 0.5 s steps; this file writes the pick
and everything inside it.

WINDOW: mix 3735.16-3825.16 s / deliverable 166.68-256.68 s / Zinc SYNC 175955-178113.

Why this one, in the numbers the picker printed (gauntlet/recon/mid_window_pick.json):

  * It is the only 90 s window in the middle third that holds THREE front changes — 3737.18
    drums->bass, 3749.96 bass->drums, 3801.40 drums->other. The first two are the trading
    itself; the third is the band coming back in. The next-best windows hold two.
  * It contains the song's structural peak: 3802.0 s at -6.9 LUFS is the loudest 3 s window
    anywhere in Taurus People, and it sits 0.6 s after the 3801.40 front change — the return
    IS the peak.
  * Its loudness range is 16.26 dB, from the quietest passage of the song (~-23 LUFS under the
    bass/drum trading) to that peak. A window is only worth cutting if it goes somewhere.

The window's end is snapped to the phrase boundary at 3825.16 (confidence 0.655) so the piece
stops on a musical seam rather than mid-bar; the start follows from the fixed 90 s length. The
start is NOT snapped: the phrase detector reads the `other` stem and the horn is out through the
trading, so it finds no boundary anywhere between 3730 and 3746 — there is nothing there to snap
to. See GAPS.md G2 for why the beat grid is not used as a bar map on this corpus.

Three time bases on every event, because three are in use: `t` mix-seconds (the analysis and the
Reaper mix), `frame` Zinc-SYNC (the timeline the builder cuts on), `d` deliverable-seconds (the
human's own cut of this song, which is what the piece is judged against).

Nothing here decides a cut. The ranking is an ordering hint with its arithmetic written beside
it. Four files in, one file out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RECON = Path(__file__).parent
ANALYSIS = Path(r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis")
OUT = RECON / "taurus_mid_events.json"
RUN = RECON / "taurus_analysis.json"

SPAN = (3568.48, 4066.15)
WINDOW = (3735.16, 3825.16)
THIRD = (SPAN[0] + (SPAN[1] - SPAN[0]) / 3.0, SPAN[0] + 2 * (SPAN[1] - SPAN[0]) / 3.0)

FPS = 23.976
FRAME_ZERO = 86401
"""Zinc-SYNC frame at mix-second 0."""

ENERGY = ANALYSIS / "Zinc-Set-2-Reaper-v4-0b66b71707de-energy.json"

PEAK_RADIUS = 3.0
PEAK_PROMINENCE_DB = 1.5
COINCIDENCE = 0.6
SPACING = 4.0

BASE = {
    "solo_change:lead": 0.80,
    "solo_change:timbre": 0.60,
}
"""Same weights the opening piece used: a `lead` change is one stem measurably taking the front
off another; a `timbre` step is a handover inside one stem, real but weaker."""

SHAPE_SMOOTH = 5
"""Energy rows per side of the moving average. Rows are 0.5 s apart, so +-2.5 s."""
SHAPE_MIN_DB = 2.0
"""A run of the smoothed curve is a phase only if it moves this far; smaller is a plateau."""
SHAPE_PLATEAU_S = 5.0
"""A flatter run than that is still a phase once it lasts this long — holding is a shape."""


def frame(seconds: float) -> int:
    return FRAME_ZERO + round(seconds * FPS)


def deliverable(seconds: float) -> float:
    return round(float(seconds) - SPAN[0], 3)


def timed(seconds: float | None) -> dict[str, Any] | None:
    if seconds is None:
        return None
    value = float(seconds)
    return {"t": round(value, 3), "frame": frame(value), "d": deliverable(value)}


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


def shape(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the window builds, where it decays, and how fast.

    The raw 3 s LUFS curve wobbles by a dB between adjacent hops, so a naive slope sign flips
    constantly. This smooths +-2.5 s, walks the smoothed curve turning-point to turning-point,
    and keeps a leg only when it moves SHAPE_MIN_DB or more — everything else is a plateau. The
    rate is dB per second across the leg, which is the number that says whether a build is a
    swell to ride or a hit to cut on.
    """
    if not rows:
        return {}
    smooth: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        lo = max(0, i - SHAPE_SMOOTH)
        hi = min(len(rows), i + SHAPE_SMOOTH + 1)
        window = rows[lo:hi]
        smooth.append(
            {
                "t": row["t"],
                "lufs": sum(one["lufs"] for one in window) / len(window),
                "ops": sum(one["onsets_per_second"] for one in window) / len(window),
            }
        )

    # Turning points of the smoothed curve.
    turns = [0]
    for i in range(1, len(smooth) - 1):
        before = smooth[i]["lufs"] - smooth[i - 1]["lufs"]
        after = smooth[i + 1]["lufs"] - smooth[i]["lufs"]
        if before > 0 >= after or before < 0 <= after:
            turns.append(i)
    turns.append(len(smooth) - 1)

    legs: list[dict[str, Any]] = []
    start = 0
    for index in turns[1:]:
        move = smooth[index]["lufs"] - smooth[start]["lufs"]
        seconds = smooth[index]["t"] - smooth[start]["t"]
        if seconds <= 0:
            continue
        small = abs(move) < SHAPE_MIN_DB and seconds < SHAPE_PLATEAU_S
        if small and legs and index != turns[-1]:
            continue  # a wobble inside the leg we are already in
        if small and legs:
            # The tail is too small to name on its own; the leg before it runs to the end
            # instead, so the shape always covers the whole window.
            last = legs[-1]
            seconds = smooth[index]["t"] - float(last["from"]["t"])
            move = smooth[index]["lufs"] - last["lufs_from"]
            legs[-1] = {
                **last,
                "to": timed(smooth[index]["t"]),
                "seconds": round(seconds, 2),
                "lufs_to": round(smooth[index]["lufs"], 2),
                "change_db": round(move, 2),
                "db_per_second": round(move / seconds, 3),
                "onsets_to": round(smooth[index]["ops"], 2),
            }
            start = index
            continue
        legs.append(
            {
                "from": timed(smooth[start]["t"]),
                "to": timed(smooth[index]["t"]),
                "seconds": round(seconds, 2),
                "lufs_from": round(smooth[start]["lufs"], 2),
                "lufs_to": round(smooth[index]["lufs"], 2),
                "change_db": round(move, 2),
                "db_per_second": round(move / seconds, 3),
                "onsets_from": round(smooth[start]["ops"], 2),
                "onsets_to": round(smooth[index]["ops"], 2),
                "kind": (
                    "build"
                    if move >= SHAPE_MIN_DB
                    else "decay"
                    if move <= -SHAPE_MIN_DB
                    else "plateau"
                ),
            }
        )
        start = index

    merged: list[dict[str, Any]] = []
    for leg in legs:
        if merged and merged[-1]["kind"] == leg["kind"]:
            last = merged[-1]
            seconds = round(last["seconds"] + leg["seconds"], 2)
            move = round(leg["lufs_to"] - last["lufs_from"], 2)
            merged[-1] = {
                **last,
                "to": leg["to"],
                "seconds": seconds,
                "lufs_to": leg["lufs_to"],
                "change_db": move,
                "db_per_second": round(move / seconds, 3),
                "onsets_to": leg["onsets_to"],
            }
        else:
            merged.append(leg)
    return {
        "smoothing_seconds": SHAPE_SMOOTH * 0.5,
        "min_leg_db": SHAPE_MIN_DB,
        "legs": merged,
    }


def rank(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """An ordering hint, not a verdict.

    score = base + 0.20 per other event within COINCIDENCE seconds (capped at 0.4)
                 + 0.15 when the detector snapped it to a downbeat
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


def spaced(ranked: list[dict[str, Any]], keep: int = 10) -> list[dict[str, Any]]:
    """The best events no two of which are the same moment."""
    picked: list[dict[str, Any]] = []
    for one in ranked:
        if any(abs(one["t"] - other["t"]) < SPACING for other in picked):
            continue
        picked.append(one)
        if len(picked) == keep:
            break
    return picked


def fronts(solos_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Who has the front, as segments clipped to the window.

    A change list alone does not say what is on screen at second 0 of the piece; the segment
    that straddles the window open is the answer, and it is the one a first shot has to serve.
    """
    changes = [row for row in solos_rows if float(row["t"]) <= WINDOW[1]]
    if not changes:
        return []
    segments: list[dict[str, Any]] = []
    for i, row in enumerate(changes):
        began = float(row["t"])
        ends = float(changes[i + 1]["t"]) if i + 1 < len(changes) else SPAN[1]
        if ends <= WINDOW[0] or began >= WINDOW[1]:
            continue
        visible = (max(began, WINDOW[0]), min(ends, WINDOW[1]))
        segments.append(
            {
                "stem": row.get("to"),
                "since": timed(began),
                "until": timed(ends),
                "in_window": {
                    "from": timed(visible[0]),
                    "to": timed(visible[1]),
                    "seconds": round(visible[1] - visible[0], 2),
                },
                "began_before_window": began < WINDOW[0],
                "signal": row.get("signal"),
                "detail": row.get("detail"),
            }
        )
    return segments


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
            "held_seconds_at_open": round(WINDOW[0] - float(before[-1]["t"]), 2),
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
                # A bar NUMBER, not a time.
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
    loudest = max(in_window, key=lambda one: one["lufs"])
    quietest = min(in_window, key=lambda one: one["lufs"])
    song_rows = [r for r in rows if SPAN[0] <= r["t"] <= SPAN[1]]
    song_peak = max(song_rows, key=lambda one: one["lufs"])

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

    rest = {
        "solo_changes": sum(1 for row in solos_rows if inside(row.get("t"), SPAN)),
        "drum_fills": sum(1 for row in fills_rows if inside(row.get("start"), SPAN)),
        "phrase_boundaries": sum(1 for row in phrases_rows if inside(row.get("t"), SPAN)),
    }

    report = {
        "kind": "taurus_mid_events",
        "song": "Taurus People",
        "piece": "PIECE 3 — mid-song cut timing + angle choice",
        "span": {
            "mix_seconds": list(SPAN),
            "frames": [frame(SPAN[0]), frame(SPAN[1])],
            "deliverable_seconds": [deliverable(SPAN[0]), deliverable(SPAN[1])],
        },
        "middle_third": {
            "mix_seconds": [round(THIRD[0], 2), round(THIRD[1], 2)],
            "deliverable_seconds": [deliverable(THIRD[0]), deliverable(THIRD[1])],
        },
        "window": {
            "label": "mid piece — bass/drums trading into the band's return",
            "mix_seconds": list(WINDOW),
            "frames": [frame(WINDOW[0]), frame(WINDOW[1])],
            "deliverable_seconds": [deliverable(WINDOW[0]), deliverable(WINDOW[1])],
            "seconds": round(WINDOW[1] - WINDOW[0], 3),
            "clear_of_opening_piece_by_s": round(WINDOW[0] - (SPAN[0] + 90.0), 2),
            "clear_of_ending_piece_by_s": round((SPAN[1] - 90.0) - WINDOW[1], 2),
            "why": [
                "Only 90 s window wholly inside the middle third holding three front changes: "
                "3737.18 drums->bass, 3749.96 bass->drums, 3801.40 drums->other.",
                "Holds the song's structural peak — 3802.0 s at -6.9 LUFS is the loudest 3 s "
                "anywhere in Taurus People, and it lands 0.6 s after the third front change.",
                "16.26 dB of loudness range: it starts in the quietest passage of the song "
                "(the trading, ~-23 LUFS) and ends in its loudest sustained one (~-10 LUFS).",
                "End snapped to the phrase boundary at 3825.16 (confidence 0.655); the start "
                "follows from the fixed 90 s length. No boundary exists near the start to snap "
                "to — the phrase detector reads `other` and the horn is out through the trading.",
            ],
        },
        "frame_mapping": {
            "sync_formula": "frame = 86401 + round(seconds * 23.976)",
            "deliverable_formula": "deliverable_t = mix_t - 3568.48",
            "fps": FPS,
            "frame_at_mix_zero": FRAME_ZERO,
            "time_bases": "every event carries t (mix s), frame (Zinc SYNC), d (deliverable s)",
        },
        "sources": {name: str(path) for name, path in found.items()} | {"energy": str(ENERGY)},
        "picker": "gauntlet/recon/mid_window_pick.py -> mid_window_pick.json",
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
        "front_segments": fronts(solos_rows),
        "solo_changes": changes,
        "drum_fills": fills,
        "phrase_boundaries": boundaries,
        "energy": {
            "window_seconds": energy_raw.get("window_seconds"),
            "hop_seconds": energy_raw.get("hop_seconds"),
            "concert_integrated_lufs": energy_raw.get("integrated_lufs"),
            "loudest_in_window": {**timed(loudest["t"]), "lufs": loudest["lufs"]},  # type: ignore[dict-item]
            "quietest_in_window": {**timed(quietest["t"]), "lufs": quietest["lufs"]},  # type: ignore[dict-item]
            "range_db": round(loudest["lufs"] - quietest["lufs"], 2),
            "song_peak": {**timed(song_peak["t"]), "lufs": song_peak["lufs"]},  # type: ignore[dict-item]
            "song_peak_in_window": inside(song_peak["t"]),
            "peaks": peaks,
        },
        "shape": shape(in_window),
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
    print(
        "window frames", report["window"]["frames"],
        "deliverable", report["window"]["deliverable_seconds"],
    )
    for leg in report["shape"]["legs"]:
        print(
            f"  {leg['kind']:8s} {leg['from']['t']:8.2f}->{leg['to']['t']:8.2f} "
            f"{leg['change_db']:+6.2f} dB over {leg['seconds']:5.1f} s "
            f"({leg['db_per_second']:+.3f} dB/s)  "
            f"onsets {leg['onsets_from']:.1f}->{leg['onsets_to']:.1f}"
        )
    for seg in report["front_segments"]:
        print(
            f"  FRONT {seg['stem']:7s} {seg['in_window']['from']['t']:8.2f}->"
            f"{seg['in_window']['to']['t']:8.2f} ({seg['in_window']['seconds']:5.1f} s)"
        )


if __name__ == "__main__":
    main()
