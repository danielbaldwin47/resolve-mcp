"""What the human does INSIDE the song's quiet sections, against what we did. READ-ONLY.

#190. The capstone won 3-0, but its quiet trough (79-157 s) reads static: five long locked
holds and one orphan 2.5 s flash. The arc-gear table (styles/concert.md sec 3) already sets the
quiet-section *rate*, and R2 hit it. So rate was never the gap.

Nor is within-section CV on its own, which is the instructive part. R2's trough scores a
respectable CV because of the flash: one shot at a fifth of the section median drags the
standard deviation up while every other shot sits inside a narrow band of long holds. Drop that
one shot and the spread collapses. A section can pass the spread floor on a number that a single
orphan is carrying, and still read locked-off, because what a viewer reads is the run of holds.

So this measures the spread that SURVIVES the orphans:

  orphan       a shot under ORPHAN_FRACTION x the section median whose neighbours inside the
               section are both at or above that median - a flash with nothing around it, as
               against a burst, which is short shots next to short shots
  cv_less      pstdev/mean over the section with the orphans dropped
  carried_by   cv - cv_less, how much of the raw spread one or two shots were holding up

Human cuts come from an ffmpeg scene detect over the deliverable at ab_pack's calibrated
threshold 0.10 (the same instrument and threshold as mid_human_cuts.py; human_cuts_taurus.json's
0.27 run is NOT interchangeable). Ours come from the cut file itself, so ours are exact and the
human's are detected - a detector miss shows up as one long shot where two sat, which biases the
human toward reading MORE locked, not less. The gap it finds is therefore a floor on the gap.

The second half is the one the server has to be able to run. correlate's gears block knows no
sections - it labels each 1 s window by loudness rank, and those labels flicker window to window
in a live room. So this also derives quiet stretches the way correlate can: smooth the 1 s RMS
curve with a centred moving median, take the quiet third of the SMOOTHED curve, and keep the
contiguous runs long enough to be a passage. The runs it finds here are what the constants in
correlate are set from, and the shot statistics are reported over both the named sections and
those derived runs so the two readings can be compared instead of assumed equal.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import Any

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

ORPHAN_FRACTION = 0.5
"""Under half the section median is a flash rather than a shorter shot."""

WINDOW_SECONDS = 1.0
"""correlate's own gear window (GEAR_WINDOW_SECONDS)."""

SMOOTHING_WINDOWS = 15
"""Centred moving median, in windows: passages are section-scale, the curve is not."""

MIN_RUN_SECONDS = 20.0
"""Shorter than this is a pocket, not a passage - too few shots to read locked either way."""


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


def orphans_in(lengths: list[float]) -> list[int]:
    """Indices of the lone flashes: short against the median, with no short shot beside them.

    Neighbours are taken inside the section only, so a section's first and last shot are judged
    against the one neighbour they have. Two short shots side by side are a burst - a gesture the
    quiet floor is allowed - and neither is an orphan.
    """
    if len(lengths) < 2:
        return []
    median = statistics.median(lengths)
    found: list[int] = []
    for i, length in enumerate(lengths):
        if length >= ORPHAN_FRACTION * median:
            continue
        near = [lengths[j] for j in (i - 1, i + 1) if 0 <= j < len(lengths)]
        if all(other >= median for other in near):
            found.append(i)
    return found


def cv_of(lengths: list[float]) -> float | None:
    if len(lengths) < 2:
        return None
    mean = statistics.fmean(lengths)
    return round(statistics.pstdev(lengths) / mean, 3) if mean > 0 else None


def section_of(d: float) -> str:
    for name, lo, hi in SECTIONS:
        if lo <= d < hi:
            return name
    return "ending"


def level_curve(path: Path) -> list[tuple[float, float]]:
    """A 1 s RMS curve off the deliverable's own audio, through the server's own reader."""
    from resolve_mcp.analysis import decode, energy

    with tempfile.TemporaryDirectory() as work:
        wav = Path(work) / "mix.wav"
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(path),
             "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(wav)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            raise SystemExit(proc.stderr[-2000:])
        curve = energy.rms_curve(decode.read(wav), WINDOW_SECONDS)
    return [(level.seconds, level.rms_dbfs) for level in curve]


def smoothed(levels: list[float], span: int) -> list[float]:
    """Centred moving median. The edges shrink the window rather than padding it."""
    half = span // 2
    out: list[float] = []
    for i in range(len(levels)):
        lo, hi = max(0, i - half), min(len(levels), i + half + 1)
        out.append(statistics.median(levels[lo:hi]))
    return out


def quiet_runs(curve: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Contiguous stretches whose smoothed level sits in the song's quiet third."""
    if not curve:
        return []
    levels = smoothed([level for _, level in curve], SMOOTHING_WINDOWS)
    order = sorted(range(len(levels)), key=lambda i: (levels[i], curve[i][0]))
    lower = len(order) // 3
    quiet = [False] * len(levels)
    for rank, i in enumerate(order):
        quiet[i] = rank < lower

    runs: list[tuple[float, float]] = []
    start: int | None = None
    for i, flag in enumerate([*quiet, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append((curve[start][0], curve[i - 1][0] + WINDOW_SECONDS))
            start = None
    return [(a, b) for a, b in runs if b - a >= MIN_RUN_SECONDS]


def stats_over(lengths: list[float]) -> dict[str, Any]:
    """The reading a quiet run gets: the spread, and the spread the orphans are not holding up."""
    if not lengths:
        return {"shots": 0, "median_seconds": None, "cv": None, "orphans": 0,
                "cv_less_orphans": None, "carried_by_orphans": None, "lengths": []}
    orphans = orphans_in(lengths)
    kept = [x for i, x in enumerate(lengths) if i not in orphans]
    cv, cv_less = cv_of(lengths), cv_of(kept)
    return {
        "shots": len(lengths),
        "median_seconds": round(statistics.median(lengths), 2),
        "cv": cv,
        "orphans": len(orphans),
        "orphan_seconds": [lengths[i] for i in orphans],
        "cv_less_orphans": cv_less,
        "carried_by_orphans": (
            round(cv - cv_less, 3) if cv is not None and cv_less is not None else None
        ),
        "lengths": lengths,
    }


def over_runs(
    label: str, spans: list[tuple[float, float]], runs: list[tuple[float, float]]
) -> list[dict[str, Any]]:
    """The same reading, over the runs the curve derived rather than the named sections."""
    out: list[dict[str, Any]] = []
    for lo, hi in runs:
        lengths = [round(b - a, 3) for a, b in spans if lo <= a < hi]
        out.append(
            {
                "label": label,
                "d_from": round(lo, 2),
                "d_to": round(hi, 2),
                "seconds": round(hi - lo, 2),
                "cuts_per_min": round(len(lengths) / (hi - lo) * 60, 2),
                **stats_over(lengths),
            }
        )
    return out


def measure(label: str, spans: list[tuple[float, float]], total: float) -> dict[str, Any]:
    lengths_all = [round(b - a, 3) for a, b in spans]
    rows: list[dict[str, Any]] = []
    for name, lo, hi in SECTIONS:
        # A shot belongs to the section its head sits in - full_gears.py's rule, kept.
        inside = [(a, b) for a, b in spans if lo <= a < hi]
        lengths = [round(b - a, 3) for a, b in inside]
        if not lengths:
            continue
        orphans = orphans_in(lengths)
        kept = [x for i, x in enumerate(lengths) if i not in orphans]
        cv = cv_of(lengths)
        cv_less = cv_of(kept)
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
                "orphan_seconds": [lengths[i] for i in orphans],
                "cv_less_orphans": cv_less,
                "carried_by_orphans": (
                    round(cv - cv_less, 3) if cv is not None and cv_less is not None else None
                ),
                "lengths": lengths,
            }
        )
    quiet = [row for row in rows if row["section"] in QUIET]
    quiet_lengths = [x for row in quiet for x in row["lengths"]]
    quiet_orphans = orphans_in(quiet_lengths)
    quiet_kept = [x for i, x in enumerate(quiet_lengths) if i not in quiet_orphans]
    return {
        "label": label,
        "duration_sec": total,
        "shots": len(spans),
        "shot_cv": cv_of(lengths_all),
        "sections": rows,
        "quiet_band": {
            "sections": list(QUIET),
            "shots": len(quiet_lengths),
            "median_seconds": round(statistics.median(quiet_lengths), 2),
            "cv": cv_of(quiet_lengths),
            "orphans": len(quiet_orphans),
            "cv_less_orphans": cv_of(quiet_kept),
            "lengths": quiet_lengths,
        },
        "mean_within_section_cv": round(
            statistics.fmean([r["cv"] for r in rows if r["cv"] is not None]), 3
        ),
        "mean_within_section_cv_less_orphans": round(
            statistics.fmean([r["cv_less_orphans"] for r in rows if r["cv_less_orphans"]]), 3
        ),
    }


def main() -> None:
    total = duration(DELIVERABLE)
    human_cuts = detect(DELIVERABLE)
    human = measure("human", shots_from_cuts(human_cuts, total), total)

    ours_spans, ours_total = shots_from_cut_file(OURS)
    ours = measure("ours (P4R2)", ours_spans, ours_total)

    curve = level_curve(DELIVERABLE)
    runs = quiet_runs(curve)
    derived = {
        "smoothing_windows": SMOOTHING_WINDOWS,
        "window_seconds": WINDOW_SECONDS,
        "min_run_seconds": MIN_RUN_SECONDS,
        "curve_windows": len(curve),
        "runs": [{"d_from": round(a, 2), "d_to": round(b, 2), "seconds": round(b - a, 2)}
                 for a, b in runs],
        "human": over_runs("human", shots_from_cuts(human_cuts, total), runs),
        "ours": over_runs("ours (P4R2)", ours_spans, runs),
    }

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
            "the section its head sits in. Orphan = under "
            f"{ORPHAN_FRACTION} x the section median with both in-section neighbours at or above "
            "that median."
        ),
        "threshold": THRESHOLD,
        "orphan_fraction": ORPHAN_FRACTION,
        "human": human,
        "ours": ours,
        "derived_quiet_runs": derived,
    }
    OUT.write_text(json.dumps(report, indent=1), encoding="utf-8")

    for res in (human, ours):
        print(f"\n=== {res['label']} ===")
        print(
            f"shots={res['shots']} cv={res['shot_cv']} "
            f"within-section CV mean={res['mean_within_section_cv']} "
            f"(less orphans {res['mean_within_section_cv_less_orphans']})"
        )
        print(
            f"{'section':9}{'shots':>6}{'cpm':>7}{'med':>7}{'cv':>7}{'orph':>5}{'cv-less':>8}"
        )
        for r in res["sections"]:
            print(
                f"{r['section']:9}{r['shots']:6d}{r['cuts_per_min']:7.2f}"
                f"{r['median_seconds']:7.2f}{r['cv'] or 0:7.3f}{r['orphans']:5d}"
                f"{r['cv_less_orphans'] or 0:8.3f}"
            )
        band = res["quiet_band"]
        print(
            f"quiet band {QUIET}: shots={band['shots']} median={band['median_seconds']} "
            f"cv={band['cv']} orphans={band['orphans']} cv_less={band['cv_less_orphans']}"
        )
        print(f"  lengths: {band['lengths']}")

    print(f"\n=== derived quiet runs (smoothed {SMOOTHING_WINDOWS} x {WINDOW_SECONDS} s) ===")
    for run in derived["runs"]:
        print(f"  d {run['d_from']:7.2f} -> {run['d_to']:7.2f}  ({run['seconds']:.1f} s)")
    for who in ("human", "ours"):
        print(f"-- {who} --")
        for row in derived[who]:
            print(
                f"  d {row['d_from']:7.2f}-{row['d_to']:7.2f} shots={row['shots']:3d} "
                f"cpm={row['cuts_per_min']:5.2f} med={row['median_seconds']} "
                f"cv={row['cv']} orphans={row['orphans']} cv_less={row['cv_less_orphans']}"
            )
            print(f"    lengths: {row['lengths']}")


if __name__ == "__main__":
    main()
