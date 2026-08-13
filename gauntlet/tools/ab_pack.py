"""Blind A/B comparison pack builder for the gauntlet critic.

Takes two videos (or two spans of the same video), randomly but deterministically
assigns them to labels A and B, and builds a scrubbed evidence pack:

    out/assignment.json      SEALED envelope: label -> real source path
    out/manifest.json        neutral metadata only (no source names)
    out/<label>/clip.mp4     the extracted span, 540p
    out/<label>/cuts.json    scene-cut times, shot lengths, stats, loudness
    out/<label>/sheet_N.jpg  contact sheets, 6 per row, timestamp burned in

Everything outside assignment.json is free of source paths and file names, so a
critic can read the pack without knowing which cut is which.

Usage:
    uv run python gauntlet/tools/ab_pack.py \
        --a <video> --b <video> [--a-span S,E] [--b-span S,E] --out <dir>

Dependencies: stdlib + ffmpeg/ffprobe on PATH.

ffmpeg-on-Windows gotcha: a colon inside a filter option value (a font path,
a metadata output file) is parsed as an option separator, so 'C:/...' breaks the
filtergraph. This module never puts a Windows path inside a filter: the font is
copied into the work dir and referenced relatively with cwd set, and metadata is
read from the ffmpeg log instead of metadata=print:file=.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import shutil
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# constants

SCENE_THRESHOLD = 0.10
SCENE_SCALE_W = 320
MAX_SHEET_SHOTS = 60
TILE_COLS = 6
TILE_ROWS = 5
THUMB_W = 320
THUMB_H = 180
CLIP_HEIGHT = 540
MIN_CUT_TIME = 0.5  # drop a "cut" detected at the very head of the clip

FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/consola.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
]

HIST_BUCKETS = [
    ("<2s", 0.0, 2.0),
    ("2-4s", 2.0, 4.0),
    ("4-8s", 4.0, 8.0),
    ("8-15s", 8.0, 15.0),
    ("15-30s", 15.0, 30.0),
    (">30s", 30.0, float("inf")),
]


# --------------------------------------------------------------------------
# process helpers


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command, capturing both streams as text. Never uses a shell."""
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        sys.exit(f"error: {name} not found on PATH")
    return path


def probe_duration(path: Path) -> float:
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    try:
        return float(proc.stdout.strip())
    except ValueError:
        sys.exit(f"error: could not read duration of {path}\n{proc.stderr[-2000:]}")


# --------------------------------------------------------------------------
# sealed-envelope assignment


def content_digest(path: Path, span: tuple[float, float] | None) -> str:
    """Cheap content fingerprint: size + span + head and tail megabyte.

    Full hashing of a multi-GB master buys nothing here -- two candidate cuts
    of the same performance always differ in the first megabyte.
    """
    h = hashlib.sha256()
    size = path.stat().st_size
    h.update(str(size).encode())
    h.update(repr(span).encode())
    with path.open("rb") as fh:
        h.update(fh.read(1 << 20))
        if size > (2 << 20):
            fh.seek(-(1 << 20), 2)
            h.update(fh.read(1 << 20))
    return h.hexdigest()


def assign_labels(
    a: tuple[Path, tuple[float, float] | None],
    b: tuple[Path, tuple[float, float] | None],
) -> dict[str, tuple[Path, tuple[float, float] | None]]:
    """Randomly -- but reproducibly -- map the two inputs onto labels A and B.

    The coin flip is seeded by the two content digests and applied to the
    digest-sorted inputs, so the mapping is a pure function of what was fed in:
    a rerun is stable, and swapping --a for --b does not reshuffle the pack
    under a reviewer who is midway through it.
    """
    ranked = sorted([(content_digest(*a), a), (content_digest(*b), b)], key=lambda p: p[0])
    seed = int(hashlib.sha256("".join(d for d, _ in ranked).encode()).hexdigest()[:16], 16)
    inputs = [item for _, item in ranked]
    if random.Random(seed).random() < 0.5:
        inputs.reverse()
    return {"A": inputs[0], "B": inputs[1]}


# --------------------------------------------------------------------------
# span extraction


def extract_span(src: Path, span: tuple[float, float] | None, dest: Path) -> float:
    """Cut the span out of src into a 540p mp4. Returns the clip duration."""
    cmd = ["ffmpeg", "-hide_banner", "-y"]
    if span:
        start, end = span
        cmd += ["-ss", f"{start:.3f}", "-t", f"{max(end - start, 0.0):.3f}"]
    cmd += [
        "-i",
        str(src),
        "-vf",
        f"scale=-2:{CLIP_HEIGHT}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        str(dest),
    ]
    proc = run(cmd)
    if proc.returncode != 0 or not dest.exists():
        sys.exit(f"error: span extraction failed\n{proc.stderr[-3000:]}")
    return probe_duration(dest)


# --------------------------------------------------------------------------
# scene-cut detection

PTS_RE = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")


def detect_cuts(clip: Path) -> list[float]:
    """Scene-cut times in seconds, relative to the clip.

    Reads pts_time out of the ffmpeg log rather than metadata=print:file=<path>:
    on Windows the drive colon in that path is eaten by the filtergraph parser.
    """
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(clip),
            "-an",
            "-filter:v",
            f"scale={SCENE_SCALE_W}:-2,select='gt(scene,{SCENE_THRESHOLD})',metadata=print",
            "-f",
            "null",
            "-",
        ]
    )
    blob = proc.stdout + "\n" + proc.stderr
    cuts = [float(m) for m in PTS_RE.findall(blob)]
    cuts = sorted({round(c, 3) for c in cuts if c >= MIN_CUT_TIME})
    if not cuts and proc.returncode != 0:
        sys.exit(f"error: scene detection failed\n{proc.stderr[-3000:]}")
    return cuts


def shots_from_cuts(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    bounds = [0.0] + [c for c in cuts if c < duration] + [duration]
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1) if bounds[i + 1] > bounds[i]]


def shot_stats(shots: list[tuple[float, float]], duration: float, n_cuts: int) -> dict[str, Any]:
    lengths = [round(e - s, 3) for s, e in shots]
    hist = {name: 0 for name, _, _ in HIST_BUCKETS}
    for length in lengths:
        for name, lo, hi in HIST_BUCKETS:
            if lo <= length < hi:
                hist[name] += 1
                break
    return {
        "total_cuts": n_cuts,
        "total_shots": len(shots),
        "cuts_per_minute": round(n_cuts / (duration / 60.0), 3) if duration else 0.0,
        "shot_length_sec": {
            "mean": round(statistics.fmean(lengths), 3) if lengths else 0.0,
            "median": round(statistics.median(lengths), 3) if lengths else 0.0,
            "min": min(lengths) if lengths else 0.0,
            "max": max(lengths) if lengths else 0.0,
        },
        "shot_length_histogram": hist,
    }


# --------------------------------------------------------------------------
# loudness curve

RMS_RE = re.compile(r"pts_time:([0-9]+\.?[0-9]*)\s*\n[^\n]*RMS_level=(-?[0-9]+\.?[0-9]*|-inf)")


def loudness_curve(clip: Path) -> list[dict[str, float]] | None:
    """1-second-window RMS level in dBFS. Returns None if the clip has no audio."""
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(clip),
            "-vn",
            "-af",
            "aresample=8000,asetnsamples=8000,astats=metadata=1:reset=1,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f",
            "null",
            "-",
        ]
    )
    blob = proc.stdout + "\n" + proc.stderr
    points: list[dict[str, float]] = []
    for t_str, rms_str in RMS_RE.findall(blob):
        rms = -100.0 if rms_str == "-inf" else float(rms_str)
        points.append({"t": round(float(t_str), 2), "rms_db": round(rms, 2)})
    return points or None


# --------------------------------------------------------------------------
# contact sheets


def find_font() -> Path | None:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def grab_thumb(
    clip: Path, t: float, dest: Path, caption: str, font: Path | None, cwd: Path
) -> bool:
    """Grab one frame at t, scaled to THUMB_W x THUMB_H, timestamp burned in.

    `font` is a bare file name resolved against `cwd` -- never an absolute
    Windows path, which the filtergraph parser would split on the drive colon.
    """
    chain = f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease," \
            f"pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    if font is not None:
        chain += (
            f",drawtext=fontfile={font.name}:text='{caption}':"
            "x=6:y=h-th-6:fontsize=14:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4"
        )
    proc = run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-y",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(clip),
            "-frames:v",
            "1",
            "-vf",
            chain,
            "-q:v",
            "3",
            str(dest),
        ],
        cwd=cwd,
    )
    return proc.returncode == 0 and dest.exists()


def build_sheets(
    clip: Path, shots: list[tuple[float, float]], label_dir: Path, work: Path
) -> list[str]:
    """Midpoint thumbnails tiled into 6-per-row JPEG grids. Returns file names."""
    font_src = find_font()
    font: Path | None = None
    if font_src is not None:
        font = work / "sheetfont.ttf"
        shutil.copyfile(font_src, font)

    selected = shots[:MAX_SHEET_SHOTS]
    per_sheet = TILE_COLS * TILE_ROWS
    sheets: list[str] = []

    for sheet_idx in range(0, len(selected), per_sheet):
        chunk = selected[sheet_idx : sheet_idx + per_sheet]
        n = sheet_idx // per_sheet + 1
        frames_dir = work / f"sheet_{n}"
        frames_dir.mkdir(parents=True, exist_ok=True)

        written = 0
        for offset, (start, end) in enumerate(chunk):
            shot_no = sheet_idx + offset + 1
            mid = start + (end - start) / 2.0
            caption = f"{shot_no:02d}  {mid:.1f}s  len {end - start:.1f}s"
            dest = frames_dir / f"f{written + 1:04d}.jpg"
            if grab_thumb(clip, mid, dest, caption, font, work):
                written += 1

        if written == 0:
            continue

        sheet_name = f"sheet_{n}.jpg"
        rows = max(1, min(TILE_ROWS, -(-written // TILE_COLS)))
        proc = run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-y",
                "-framerate",
                "1",
                "-start_number",
                "1",
                "-i",
                "f%04d.jpg",
                "-vf",
                f"tile={TILE_COLS}x{rows}:padding=4:margin=6:color=0x101010",
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(label_dir / sheet_name),
            ],
            cwd=frames_dir,
        )
        if proc.returncode == 0 and (label_dir / sheet_name).exists():
            sheets.append(sheet_name)
        else:
            print(f"warn: sheet {n} tiling failed\n{proc.stderr[-1500:]}", file=sys.stderr)

    return sheets


# --------------------------------------------------------------------------
# per-label pipeline


def build_label(
    label: str,
    src: Path,
    span: tuple[float, float] | None,
    out: Path,
    keep_work: bool,
) -> dict[str, Any]:
    label_dir = out / label
    label_dir.mkdir(parents=True, exist_ok=True)
    work = label_dir / "_work"
    work.mkdir(parents=True, exist_ok=True)

    clip = work / "clip.mp4"
    duration = extract_span(src, span, clip)

    cuts = detect_cuts(clip)
    shots = shots_from_cuts(cuts, duration)
    stats = shot_stats(shots, duration, len(cuts))

    cuts_doc: dict[str, Any] = {
        "label": label,
        "clip_duration_sec": round(duration, 3),
        "scene_threshold": SCENE_THRESHOLD,
        "scene_detect_scale_width": SCENE_SCALE_W,
        **stats,
        "cut_times_sec": cuts,
        "shots": [
            {"index": i + 1, "start": round(s, 3), "end": round(e, 3), "length": round(e - s, 3)}
            for i, (s, e) in enumerate(shots)
        ],
    }
    curve = loudness_curve(clip)
    if curve:
        cuts_doc["loudness_1s_rms_db"] = curve
        levels = [p["rms_db"] for p in curve]
        cuts_doc["loudness_summary"] = {
            "mean_rms_db": round(statistics.fmean(levels), 2),
            "min_rms_db": min(levels),
            "max_rms_db": max(levels),
            "windows": len(levels),
        }

    (label_dir / "cuts.json").write_text(json.dumps(cuts_doc, indent=2), encoding="utf-8")

    sheets = build_sheets(clip, shots, label_dir, work)

    final_clip = label_dir / "clip.mp4"
    shutil.move(str(clip), str(final_clip))
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)

    return {
        "label": label,
        "duration_sec": round(duration, 3),
        "total_cuts": stats["total_cuts"],
        "total_shots": stats["total_shots"],
        "cuts_per_minute": stats["cuts_per_minute"],
        "shots_on_sheets": min(len(shots), MAX_SHEET_SHOTS),
        "files": ["clip.mp4", "cuts.json", *sheets],
    }


# --------------------------------------------------------------------------
# cli


def parse_span(raw: str | None) -> tuple[float, float] | None:
    if raw is None:
        return None
    parts = raw.split(",")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("span must be START,END in seconds")
    start, end = float(parts[0]), float(parts[1])
    if end <= start:
        raise argparse.ArgumentTypeError("span END must be greater than START")
    return start, end


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a blind A/B comparison pack from two videos or two spans."
    )
    parser.add_argument("--a", required=True, help="first input video")
    parser.add_argument("--b", required=True, help="second input video")
    parser.add_argument("--a-span", default=None, help="START,END seconds of --a")
    parser.add_argument("--b-span", default=None, help="START,END seconds of --b")
    parser.add_argument("--out", required=True, help="output pack directory")
    parser.add_argument("--keep-work", action="store_true", help="keep intermediate frames")
    parser.add_argument(
        "--expect-a",
        type=int,
        default=None,
        help="expected cut count for --a; abort before sealing if detected < 80%% of this",
    )
    parser.add_argument(
        "--expect-b",
        type=int,
        default=None,
        help="expected cut count for --b; abort before sealing if detected < 80%% of this",
    )
    args = parser.parse_args(argv)

    require_tool("ffmpeg")
    require_tool("ffprobe")

    a_path = Path(args.a).resolve()
    b_path = Path(args.b).resolve()
    for path in (a_path, b_path):
        if not path.exists():
            sys.exit(f"error: input not found: {path}")

    a_span = parse_span(args.a_span)
    b_span = parse_span(args.b_span)

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    labels = assign_labels((a_path, a_span), (b_path, b_span))

    # Match each label back to the flag it came from, consuming as we go so
    # that two byte-identical inputs still get one "--a" and one "--b".
    unclaimed = [("--a", (a_path, a_span)), ("--b", (b_path, b_span))]
    flag_of: dict[str, str] = {}
    for label in ("A", "B"):
        for i, (flag, item) in enumerate(unclaimed):
            if item == labels[label]:
                flag_of[label] = flag
                unclaimed.pop(i)
                break

    label_entries = []
    for label in ("A", "B"):
        src, span = labels[label]
        print(f"[{label}] building...", file=sys.stderr)
        label_entries.append(build_label(label, src, span, out, args.keep_work))

    # Refuse to seal a pack whose detector couldn't see the cuts it was told
    # to expect -- a confident manifest built on a blind detector is worse
    # than no manifest at all. Checked before any sealed/summary file is
    # written.
    expect_by_flag = {"--a": args.expect_a, "--b": args.expect_b}
    for entry in label_entries:
        flag = flag_of.get(entry["label"])
        expected = expect_by_flag.get(flag)
        if expected is None:
            continue
        detected = entry["total_cuts"]
        threshold = 0.8 * expected
        if detected < threshold:
            sys.exit(
                f"error: {flag} ({entry['label']}) detected {detected} cuts, "
                f"expected {expected} -- below 80% ({threshold:.1f}) of expected. "
                "Refusing to write manifest.json; the detector cannot see this cut."
            )

    # SEALED ENVELOPE -- the only file in the pack that names a source.
    assignment = {
        "sealed": True,
        "note": "Do not open until the blind comparison is recorded.",
        "labels": {
            label: {
                "source_path": str(src),
                "span_sec": list(span) if span else None,
                "input_flag": flag_of.get(label),
            }
            for label, (src, span) in labels.items()
        },
    }
    (out / "assignment.json").write_text(json.dumps(assignment, indent=2), encoding="utf-8")

    manifest = {
        "pack_version": 1,
        "kind": "blind_ab_comparison",
        "scene_threshold": SCENE_THRESHOLD,
        "contact_sheet": {
            "columns": TILE_COLS,
            "rows_per_sheet": TILE_ROWS,
            "thumb_width_px": THUMB_W,
            "max_shots_per_label": MAX_SHEET_SHOTS,
            "frame_position": "shot midpoint",
        },
        "labels": label_entries,
        "sealed_envelope": "assignment.json",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for entry in label_entries:
        print(
            f"[{entry['label']}] {entry['duration_sec']}s  "
            f"{entry['total_cuts']} cuts  {entry['cuts_per_minute']}/min  "
            f"{len(entry['files']) - 2} sheet(s)",
            file=sys.stderr,
        )
    print(f"pack written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
