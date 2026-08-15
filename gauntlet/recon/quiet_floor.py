"""What the human does INSIDE the song's quiet sections, against what we did. READ-ONLY.

#190. The capstone won 3-0, but its quiet trough (79-157 s) reads static: five long locked
holds and one orphan 2.5 s flash. The arc-gear table (styles/concert.md sec 3) already sets the
quiet-section *rate*, and R2 hit it. So rate was never the gap.

What the receipt measures is the spread inside a passage, and the spread that survives its
orphans:

  orphan       a shot under ORPHAN_FRACTION x the passage median whose neighbours inside the
               passage are both at or above that median - a flash with nothing around it, as
               against a burst, which is short shots side by side and is a gesture the floor
               is allowed to make
  cv_less      pstdev/mean over the passage with the orphans dropped
  carried_by   cv - cv_less, how much of the raw spread one or two shots were holding up

Every one of those readings is taken by importing the server's own functions rather
than by reimplementing them here (`analysis.rhythm`, which the reading moved to in #215;
`analysis.correlate` before that). That is deliberate: this file is the receipt behind the
corpus row and the style bullet, and a second copy of the arithmetic would let the receipt go
on describing a tool the server no longer has. The `server_check` block at the end is the same
point made end to end - the report's `gears.quiet_floor`, run over the deliverable's own level
curve and our cut file, so "the rule fires on the cut the ticket complains about" is a
recorded result rather than an inference.

Human cuts come from an ffmpeg scene detect over the deliverable at ab_pack's calibrated
threshold 0.10 (the same instrument and threshold as mid_human_cuts.py; human_cuts_taurus.json's
0.27 run is NOT interchangeable). Ours come from the cut file itself, so ours are exact and the
human's are detected - a detector miss shows up as one long shot where two sat, which biases the
human toward reading MORE locked, not less. The gap it finds is therefore a floor on the gap.

Two clocks, and they differ by 0.167 s: the deliverable runs 497.664 s and our cut file 497.497.
Nothing here turns on it - the passage under test sits at 38-195 - but the section boundaries
are the deliverable's, so ours are read against his song, not against their own.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from resolve_mcp.analysis import decode, energy, rhythm

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("quiet_floor.json")
DELIVERABLE = Path(
    r"S:\Deliverables\Ryan Devlin\6-17-26 Zinc Bar\Full Videos"
    r"\6-17 - Zinc Set 2 - Taurus People.mp4"
)
OURS = ROOT / "projects" / "mcp-tests-zinc" / "taurus-people-full-r2.cut.json"

THRESHOLD = 0.10
SCALE_W = 320
FPS = 24000 / 1001.0

# The song's own sections, verbatim from full_gears.py - the boundaries the arc-gear table
# in styles/concert.md is written against.
SECTIONS: list[tuple[str, float, float]] = [
    ("head", 0.00, 36.06),
    ("floor", 36.06, 96.02),
    ("breath", 96.02, 153.52),
    ("trade", 153.52, 232.92),
    ("plateau", 232.92, 294.52),
    ("build", 294.52, 328.02),
    ("fast", 328.02, 381.02),
    ("summit", 381.02, 474.64),
    ("ending", 474.64, 497.664),
]

QUIET = ("floor", "breath")
"""The trough: the two sections the arc-gear table measures at -25.2 and -22.5 LUFS."""


def detect(path: Path) -> list[float]:
    cmd = [
        "ffmpeg", "-v", "info", "-nostats",
        "-i", str(path),
        "-vf", f"scale={SCALE_W}:-2,select='gt(scene,{THRESHOLD})',metadata=print",
        "-an", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-2000:])
    return [round(float(m), 3) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(proc.stderr[-2000:])
    return round(float(proc.stdout.strip()), 3)


def level_curve(path: Path) -> list[tuple[float, float]]:
    """A 1 s RMS curve off the deliverable's own audio, through the server's own reader."""
    with tempfile.TemporaryDirectory() as work:
        wav = Path(work) / "mix.wav"
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(path),
             "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(proc.stderr[-2000:])
        curve = energy.rms_curve(decode.read(wav), rhythm.GEAR_WINDOW_SECONDS)
    return [(level.seconds, level.rms_dbfs) for level in curve]


def shots_from_cuts(cuts: list[float], total: float) -> list[tuple[float, float]]:
    """Shot spans from cut times: [0,c1), [c1,c2), ... [cn,total)."""
    edges = [0.0, *cuts, total]
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


def shots_from_cut_file(path: Path) -> tuple[list[tuple[float, float]], float]:
    """Shot spans out of a cut file. A gap is literal black and is a shot like any other."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    spans: list[tuple[float, float]] = []
    d = 0.0
    for seg in doc["segments"]:
        frames = seg["gap"] if "gap" in seg else seg["out"] - seg["in"]
        spans.append((round(d, 3), round(d + frames / FPS, 3)))
        d += frames / FPS
    return spans, round(d, 3)


def rows_of(spans: list[tuple[float, float]]) -> list[dict[str, Any]]:
    """Shot spans as the rows the server's own readers take."""
    return [{"t": round(a, 3), "seconds": round(b - a, 3)} for a, b in spans]


def measure(label: str, spans: list[tuple[float, float]], total: float) -> dict[str, Any]:
    lengths_all = [round(b - a, 3) for a, b in spans]
    rows: list[dict[str, Any]] = []
    for name, lo, hi in SECTIONS:
        # A shot belongs to the section its head sits in - full_gears.py's rule, kept.
        inside = [(a, b) for a, b in spans if lo <= a < hi]
        lengths = [round(b - a, 3) for a, b in inside]
        if not lengths:
            continue
        orphans = rhythm._orphans(lengths)
        kept = [x for i, x in enumerate(lengths) if i not in orphans]
        cv = rhythm._cv(lengths)
        cv_less = rhythm._cv(kept)
        rows.append(
            {
                "section": name,
                "d_from": lo,
                "d_to": hi,
                "seconds": round(hi - lo, 2),
                "shots": len(lengths),
                "cuts_per_min": round(len(lengths) / (hi - lo) * 60, 2),
                "median_seconds": round(statistics.median(lengths), 2),
                "min_seconds": round(min(lengths), 2),
                "max_seconds": round(max(lengths), 2),
                "cv": cv,
                "orphans": len(orphans),
                "orphan_seconds": [lengths[i] for i in sorted(orphans)],
                "cv_less_orphans": cv_less,
                "carried_by_orphans": (
                    round(cv - cv_less, 3) if cv is not None and cv_less is not None else None
                ),
                "lengths": lengths,
            }
        )
    quiet = [row for row in rows if row["section"] in QUIET]
    quiet_lengths = [x for row in quiet for x in row["lengths"]]
    quiet_orphans = rhythm._orphans(quiet_lengths)
    quiet_kept = [x for i, x in enumerate(quiet_lengths) if i not in quiet_orphans]
    # A section holding one shot has no within-section spread to average in. rhythm._cv
    # answers 0.0 there on purpose - for a *passage*, one hold running through is exactly the
    # locked reading - but a mean over sections is a different question, and folding that 0.0
    # in would report a stillness the section never had. His `ending` is one 21 s shot.
    section_cvs = [r["cv"] for r in rows if r["cv"] is not None and r["shots"] >= 2]
    section_cvs_less = [
        r["cv_less_orphans"]
        for r in rows
        if r["cv_less_orphans"] is not None and r["shots"] - r["orphans"] >= 2
    ]
    return {
        "label": label,
        "duration_sec": total,
        "shots": len(spans),
        "shot_cv": rhythm._cv(lengths_all),
        "sections": rows,
        "quiet_band": {
            "sections": list(QUIET),
            "shots": len(quiet_lengths),
            "median_seconds": round(statistics.median(quiet_lengths), 2),
            "pooled_cv": rhythm._cv(quiet_lengths),
            "mean_of_section_cvs": round(
                statistics.fmean([r["cv"] for r in quiet if r["cv"] is not None]), 3
            ),
            "orphans": len(quiet_orphans),
            "pooled_cv_less_orphans": rhythm._cv(quiet_kept),
            "lengths": quiet_lengths,
        },
        "mean_within_section_cv": round(statistics.fmean(section_cvs), 3),
        "sections_it_is_taken_over": len(section_cvs),
        "mean_within_section_cv_less_orphans": round(statistics.fmean(section_cvs_less), 3),
    }


def over_runs(
    spans: list[tuple[float, float]], runs: list[tuple[float, float]]
) -> list[dict[str, Any]]:
    """rhythm's own passage reading, over the runs its own run-finder derived."""
    rows = rows_of(spans)
    return [rhythm._passage(rows, lo, hi) for lo, hi in runs]


def main() -> None:
    total = duration(DELIVERABLE)
    human_cuts = detect(DELIVERABLE)
    human_spans = shots_from_cuts(human_cuts, total)
    human = measure("human", human_spans, total)

    ours_spans, ours_total = shots_from_cut_file(OURS)
    ours = measure("ours (P4R2)", ours_spans, ours_total)

    curve = level_curve(DELIVERABLE)
    runs = rhythm._quiet_runs(curve)
    derived = {
        "smoothing_windows": rhythm.QUIET_SMOOTHING_WINDOWS,
        "window_seconds": rhythm.GEAR_WINDOW_SECONDS,
        "min_run_seconds": rhythm.QUIET_FLOOR_SECONDS,
        "orphan_fraction": rhythm.ORPHAN_FRACTION,
        "cv_floor": rhythm.FLOOR_CV_FLOOR,
        "curve_windows": len(curve),
        "runs": [{"d_from": round(a, 2), "d_to": round(b, 2), "seconds": round(b - a, 2)}
                 for a, b in runs],
        "human": over_runs(human_spans, runs),
        "ours": over_runs(ours_spans, runs),
    }

    # The whole block as the report would carry it on our cut: the ticket's own cut, the
    # server's own code, so the claim that the rule fires on it is recorded rather than argued.
    server_check = rhythm._quiet_floor(rows_of(ours_spans), curve)

    report = {
        "kind": "quiet_floor",
        "ticket": 190,
        "question": "what does the human do inside the quiet sections that R2's trough does not",
        "human_source": str(DELIVERABLE),
        "ours_source": str(OURS.relative_to(ROOT)).replace("\\", "/"),
        "method": (
            f"human: ffmpeg scale={SCALE_W}:-2, select gt(scene,{THRESHOLD}), metadata=print over "
            "the whole deliverable, shots between consecutive detected cuts; ours: the cut file's "
            "own segment lengths at 23.976 fps. Sections are full_gears.py's, a shot belongs to "
            "the section its head sits in. Every spread, orphan and passage reading is taken by "
            "analysis.rhythm's own functions, not by a copy of them living here."
        ),
        "threshold": THRESHOLD,
        "human": human,
        "ours": ours,
        "derived_quiet_runs": derived,
        "server_check": {
            "what": "analysis.rhythm gears.quiet_floor over our P4R2 cut file",
            "reads_locked": server_check["reads_locked"],
            "runs": server_check["runs"],
        },
    }
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")

    for res in (human, ours):
        print(f"\n=== {res['label']} ===")
        print(
            f"shots={res['shots']} cv={res['shot_cv']} "
            f"within-section CV mean={res['mean_within_section_cv']} "
            f"over {res['sections_it_is_taken_over']} sections "
            f"(less orphans {res['mean_within_section_cv_less_orphans']})"
        )
        print(f"{'section':9}{'shots':>6}{'cpm':>7}{'med':>7}{'cv':>7}{'orph':>5}{'cv-less':>8}")
        for r in res["sections"]:
            print(
                f"{r['section']:9}{r['shots']:6d}{r['cuts_per_min']:7.2f}"
                f"{r['median_seconds']:7.2f}{r['cv'] if r['cv'] is not None else -1:7.3f}"
                f"{r['orphans']:5d}"
                f"{r['cv_less_orphans'] if r['cv_less_orphans'] is not None else -1:8.3f}"
            )
        band = res["quiet_band"]
        print(
            f"quiet band {QUIET}: shots={band['shots']} median={band['median_seconds']} "
            f"pooled cv={band['pooled_cv']} (mean of the two section CVs "
            f"{band['mean_of_section_cvs']}) orphans={band['orphans']} "
            f"pooled cv_less={band['pooled_cv_less_orphans']}"
        )
        print(f"  lengths: {band['lengths']}")

    print(
        f"\n=== derived quiet runs "
        f"(smoothed {rhythm.QUIET_SMOOTHING_WINDOWS} x "
        f"{rhythm.GEAR_WINDOW_SECONDS} s) ==="
    )
    for run in derived["runs"]:
        print(f"  d {run['d_from']:7.2f} -> {run['d_to']:7.2f}  ({run['seconds']:.1f} s)")
    for who in ("human", "ours"):
        print(f"-- {who} --")
        for row in derived[who]:
            print(f"  {row}")
    print(f"\nserver_check reads_locked: {server_check['reads_locked']}")


if __name__ == "__main__":
    main()
