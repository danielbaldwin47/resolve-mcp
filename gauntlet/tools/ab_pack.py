"""Blind A/B comparison pack builder for the gauntlet critic.

Takes two videos (or two spans of the same video), randomly but deterministically
assigns them to labels A and B, and builds a scrubbed evidence pack:

    out/assignment.json          SEALED envelope: label -> real source path
    out/manifest.json            neutral metadata only (no source names)
    out/<label>/clip.mp4         the extracted span, 540p
    out/<label>/cuts.json        cut times + transition type, shots + motion,
                                 stats, loudness
    out/<label>/sheet_N.jpg      contact sheets, 6 per row, timestamp burned in
    out/<label>/cutstrip_N.jpg   cut-boundary filmstrips, one cut per row:
                                 3 outgoing frames then 3 incoming frames

Everything outside assignment.json is free of source paths and file names, so a
critic can read the pack without knowing which cut is which.

Pack v2 adds the three measurements a stills-only judge could not make (gap G5,
round R1b): what the frames either side of a cut actually look like, whether a
shot is locked or moving, and whether a boundary is a hard cut or a mix.

Usage:
    uv run python gauntlet/tools/ab_pack.py \
        --a <video> --b <video> [--a-span S,E] [--b-span S,E] --out <dir>

Dependencies: stdlib + numpy + ffmpeg/ffprobe on PATH.

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

import numpy as np

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

# cut-boundary filmstrips: frame offsets relative to the first frame of the
# incoming shot. Negative = outgoing shot, 0 = the first new frame.
STRIP_OUT_OFFSETS = (-12, -6, -1)
STRIP_IN_OFFSETS = (0, 5, 11)
STRIP_CUTS_PER_SHEET = 6
STRIP_COLS = len(STRIP_OUT_OFFSETS) + len(STRIP_IN_OFFSETS)
MAX_STRIP_CUTS = 36  # 6 sheets' worth; a 90 s span never gets near this

# per-shot motion: decode the whole clip small and grey, estimate the global
# frame-to-frame shift by 1-D cross-correlation of row/column projections
# (technique lifted from gauntlet/recon/taurus_motion.py).
MOTION_W, MOTION_H, MOTION_RATE = 192, 108, 6.0
MOTION_MAXLAG_X = 12
MOTION_MAXLAG_Y = 8
MOTION_MOVE_PX = 1.0  # |shift| at/above this, per 1/6 s at 192 px wide, is motion
MOTION_EDGE_GUARD = 0.09  # ignore samples this close to a shot boundary
MOTION_STATIC_SHARE = 0.15
MOTION_PAN_NET_PX = 4.0  # ~2% of frame width of net displacement
MOTION_DIRECTIONAL = 0.5  # net / path travelled: high = one way, low = jitter
MOTION_JITTER_SHARE = 0.5
MOTION_JITTER_DIRECTIONAL = 0.35
MOTION_MIN_SAMPLES = 3

# transition typing: decode a native-fps window around each boundary
TRANS_W, TRANS_H = 128, 72
# Window is 2*this + 1 frames centred on the cut. Half a second either side:
# a broadcast fade runs 0.5-1 s, and a +/-6-frame window sees its start and
# calls the ramp a dissolve because the black never enters the window.
TRANS_HALF_FRAMES = 12
TRANS_NOISE_FLOOR = 1.5  # mean abs frame delta below this is decode noise
TRANS_RAMP_FRACTION = 0.25  # a pair this fraction of the peak is part of the ramp
TRANS_BLEND_RESIDUAL = 0.35  # normalised residual under this = linear blend
TRANS_BLACK_LUMA = 12.0  # mean luma at/below this reads as black
TRANS_LIT_LUMA = 30.0  # ...and this much above it as picture

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


def probe_fps(path: Path) -> float:
    """Frame rate of the first video stream, as a float (23.976 not 24000/1001)."""
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    raw = proc.stdout.strip()
    try:
        if "/" in raw:
            num, den = raw.split("/", 1)
            return float(num) / float(den)
        return float(raw)
    except (ValueError, ZeroDivisionError):
        sys.exit(f"error: could not read frame rate of {path}\n{proc.stderr[-2000:]}")


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

    Known blind spot, measured: a half-second dissolve spreads its change over
    twelve frame pairs and never trips the per-frame scene score, so a version
    built with mixes reads as having fewer cuts than it has. That is what the
    --expect-N guard is for; the transition typing on the boundaries that *are*
    found says whether this version uses mixes at all.
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
# grey decode (shared by the motion track and transition typing)


def decode_grey(
    clip: Path,
    width: int,
    height: int,
    fps: float | None = None,
    start: float | None = None,
    dur: float | None = None,
) -> np.ndarray:
    """Decode a clip (or a window of it) to an (n, height, width) float array.

    `fps=None` keeps the native frame rate -- transition typing needs every
    frame; the motion track resamples to a coarse grid instead.
    """
    cmd = ["ffmpeg", "-v", "error", "-nostdin"]
    if start is not None:
        cmd += ["-ss", f"{max(start, 0.0):.4f}"]
    if dur is not None:
        cmd += ["-t", f"{dur:.4f}"]
    chain = f"scale={width}:{height}" if fps is None else f"fps={fps},scale={width}:{height}"
    cmd += ["-i", str(clip), "-an", "-vf", chain, "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    proc = subprocess.run(cmd, capture_output=True, check=False)
    if proc.returncode != 0 and not proc.stdout:
        sys.exit(f"error: grey decode failed\n{proc.stderr[-2000:].decode('utf-8', 'replace')}")
    stride = width * height
    n = len(proc.stdout) // stride
    if n == 0:
        return np.zeros((0, height, width), dtype=np.float64)
    return (
        np.frombuffer(proc.stdout[: n * stride], dtype=np.uint8)
        .reshape(n, height, width)
        .astype(np.float64)
    )


# --------------------------------------------------------------------------
# per-shot motion metric


def shift_1d(a: np.ndarray, b: np.ndarray, maxlag: int) -> float:
    """Integer shift (in px) that best aligns 1-D projection `a` onto `b`.

    Normalised cross-correlation over +/- maxlag: the cheap global-motion
    estimate from taurus_motion.py, which needs no optical flow and survives
    the noise of a 192-px-wide grey decode.
    """
    a = a - a.mean()
    b = b - b.mean()
    best, best_lag = -1e18, 0
    for lag in range(-maxlag, maxlag + 1):
        if lag < 0:
            x, y = a[-lag:], b[: len(b) + lag]
        elif lag > 0:
            x, y = a[: len(a) - lag], b[lag:]
        else:
            x, y = a, b
        denom = float(np.sqrt((x * x).sum() * (y * y).sum())) + 1e-9
        score = float((x * y).sum()) / denom
        if score > best:
            best, best_lag = score, lag
    return float(best_lag)


def motion_track(clip: Path) -> dict[str, list[float]]:
    """Frame-to-frame global shift for the whole clip on a 1/MOTION_RATE grid.

    Each sample describes the interval between two decoded frames, so its time
    is the midpoint of that interval.
    """
    arr = decode_grey(clip, MOTION_W, MOTION_H, fps=MOTION_RATE)
    if len(arr) < 2:
        return {"t": [], "dx": [], "dy": []}
    cols = arr.mean(axis=1)  # (n, W) horizontal profile
    rows = arr.mean(axis=2)  # (n, H) vertical profile
    times, dxs, dys = [], [], []
    for i in range(1, len(arr)):
        dxs.append(shift_1d(cols[i - 1], cols[i], MOTION_MAXLAG_X))
        dys.append(shift_1d(rows[i - 1], rows[i], MOTION_MAXLAG_Y))
        times.append((i - 0.5) / MOTION_RATE)
    return {"t": times, "dx": dxs, "dy": dys}


def classify_motion(moving_share: float, net_px: float, directionality: float, n: int) -> str:
    """static | drift | pan | unstable, from the shot's own motion samples."""
    if n < MOTION_MIN_SAMPLES:
        return "unknown"
    if moving_share < MOTION_STATIC_SHARE:
        return "static"
    if net_px >= MOTION_PAN_NET_PX and directionality >= MOTION_DIRECTIONAL:
        return "pan"
    if moving_share >= MOTION_JITTER_SHARE and directionality < MOTION_JITTER_DIRECTIONAL:
        return "unstable"
    if net_px >= MOTION_PAN_NET_PX:
        return "pan"
    return "drift"


def shot_motion(track: dict[str, list[float]], start: float, end: float) -> dict[str, Any]:
    """Motion summary for one shot, using only samples wholly inside it.

    Samples that straddle a cut are dropped: a cut is a 100%-changed frame
    pair, which the correlator reads as a huge bogus shift.
    """
    lo, hi = start + MOTION_EDGE_GUARD, end - MOTION_EDGE_GUARD
    pairs = [
        (dx, dy)
        for t, dx, dy in zip(track["t"], track["dx"], track["dy"], strict=True)
        if lo <= t <= hi
    ]
    # A sample pinned at the correlator's lag limit is not a measurement of a
    # big move, it is the correlator giving up (a strobe hit, a flash frame).
    # Count them, then leave them out of the totals so one bad sample cannot
    # put 14 px of "travel" on a locked-off shot.
    saturated = sum(
        1 for dx, dy in pairs if abs(dx) >= MOTION_MAXLAG_X or abs(dy) >= MOTION_MAXLAG_Y
    )
    kept = [
        (dx, dy) for dx, dy in pairs if abs(dx) < MOTION_MAXLAG_X and abs(dy) < MOTION_MAXLAG_Y
    ]
    dxs = [dx for dx, _ in kept]
    dys = [dy for _, dy in kept]
    n = len(dxs)
    if n == 0:
        return {
            "samples": 0,
            "saturated_samples": saturated,
            "moving_share": 0.0,
            "net_px": 0.0,
            "net_dx_px": 0.0,
            "net_dy_px": 0.0,
            "path_px": 0.0,
            "directionality": 0.0,
            "class": "unknown",
        }
    moving = sum(1 for dx, dy in zip(dxs, dys, strict=True)
                 if abs(dx) >= MOTION_MOVE_PX or abs(dy) >= MOTION_MOVE_PX)
    net_dx, net_dy = float(sum(dxs)), float(sum(dys))
    net_px = float(np.hypot(net_dx, net_dy))
    path_px = float(sum(float(np.hypot(dx, dy)) for dx, dy in zip(dxs, dys, strict=True)))
    directionality = net_px / path_px if path_px > 0 else 0.0
    moving_share = moving / n
    return {
        "samples": n,
        "saturated_samples": saturated,
        "sample_hz": MOTION_RATE,
        "moving_share": round(moving_share, 3),
        "net_px": round(net_px, 2),
        "net_dx_px": round(net_dx, 1),
        "net_dy_px": round(net_dy, 1),
        "path_px": round(path_px, 1),
        "directionality": round(directionality, 3),
        "class": classify_motion(moving_share, net_px, directionality, n),
    }


# --------------------------------------------------------------------------
# transition typing


def blend_residual(pre: np.ndarray, post: np.ndarray, mid: np.ndarray) -> float:
    """How well `mid` is explained as a linear mix of `pre` and `post`.

    Returns the least-squares residual normalised by the pre->post difference:
    ~0 for a real dissolve frame, ~1 for a frame that is simply something else.
    """
    base = post - pre
    scale = float(np.sqrt((base * base).sum()))
    if scale < 1e-6:
        return 1.0
    target = mid - pre
    alpha = float((target * base).sum() / (base * base).sum())
    residual = target - alpha * base
    return float(np.sqrt((residual * residual).sum())) / scale


def classify_transition(clip: Path, cut: float, fps: float, duration: float) -> dict[str, Any]:
    """hard | dissolve | fade for one boundary, from a native-fps window.

    Five-plus frames across the boundary are enough to separate the three: a
    hard cut puts all the change in one frame pair; a dissolve spreads it over
    several pairs whose intermediate frames are linear blends of the endpoints;
    a fade runs the luma down to (or up from) black.
    """
    half = TRANS_HALF_FRAMES / fps
    start = max(cut - half, 0.0)
    dur = max(min(cut + half, duration) - start, 0.0)
    if dur <= 0.0:
        return {"type": "unknown", "frames_sampled": 0}
    arr = decode_grey(clip, TRANS_W, TRANS_H, start=start, dur=dur)
    if len(arr) < 3:
        return {"type": "unknown", "frames_sampled": int(len(arr))}

    luma = arr.mean(axis=(1, 2))
    diffs = [float(np.abs(arr[i] - arr[i - 1]).mean()) for i in range(1, len(arr))]
    peak = max(diffs)
    peak_i = diffs.index(peak)
    min_luma = float(luma.min())
    max_luma = float(luma.max())

    doc: dict[str, Any] = {
        "frames_sampled": int(len(arr)),
        "window_start_sec": round(start, 3),
        "peak_frame_delta": round(peak, 2),
        "min_luma": round(min_luma, 1),
        "max_luma": round(max_luma, 1),
    }

    if peak < TRANS_NOISE_FLOOR:
        doc["type"] = "none"
        doc["ramp_frames"] = 0
        return doc

    # A boundary that touches black is either a fade or a cut against black,
    # and the difference is the whole of the note "too short to read as a fade":
    # a fade walks through intermediate luma, a cut snaps from black to picture
    # in one frame. Only the walk gets called a fade.
    dark = [i for i, v in enumerate(luma) if v <= TRANS_BLACK_LUMA]
    if dark and max_luma >= TRANS_LIT_LUMA:
        mid_band = [v for v in luma if TRANS_BLACK_LUMA < v < 0.8 * max_luma]
        doc["black_frames"] = len(dark)
        doc["against_black"] = True
        if len(mid_band) >= 2:
            doc["type"] = "fade"
            doc["ramp_frames"] = len(mid_band)
            return doc

    # widen from the peak pair over every neighbouring pair that carries a
    # meaningful share of the change: that run is the transition's ramp
    floor = max(TRANS_RAMP_FRACTION * peak, TRANS_NOISE_FLOOR)
    i0 = peak_i
    while i0 > 0 and diffs[i0 - 1] >= floor:
        i0 -= 1
    i1 = peak_i
    while i1 + 1 < len(diffs) and diffs[i1 + 1] >= floor:
        i1 += 1
    ramp_pairs = i1 - i0 + 1
    doc["ramp_frames"] = ramp_pairs

    if ramp_pairs >= 2:
        pre, post = arr[i0], arr[i1 + 1]
        residuals = [blend_residual(pre, post, arr[j]) for j in range(i0 + 1, i1 + 1)]
        worst = max(residuals) if residuals else 1.0
        doc["blend_residual"] = round(worst, 3)
        doc["type"] = "dissolve" if worst <= TRANS_BLEND_RESIDUAL else "hard"
        return doc

    doc["type"] = "hard"
    return doc


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
    clip: Path,
    t: float,
    dest: Path,
    caption: str,
    font: Path | None,
    cwd: Path,
    text_color: str = "white",
    box_color: str = "black@0.6",
) -> bool:
    """Grab one frame at t, scaled to THUMB_W x THUMB_H, timestamp burned in.

    `font` is a bare file name resolved against `cwd` -- never an absolute
    Windows path, which the filtergraph parser would split on the drive colon.
    The caption colours carry meaning on cut strips: outgoing frames keep the
    default white-on-black, incoming frames get a loud box, so the boundary in
    a filmstrip row is visible without counting tiles.
    """
    chain = f"scale={THUMB_W}:{THUMB_H}:force_original_aspect_ratio=decrease," \
            f"pad={THUMB_W}:{THUMB_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    if font is not None:
        chain += (
            f",drawtext=fontfile={font.name}:text='{caption}':"
            f"x=6:y=h-th-6:fontsize=14:fontcolor={text_color}:box=1:"
            f"boxcolor={box_color}:boxborderw=4"
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
# cut-boundary filmstrips


def placeholder_tile(dest: Path, caption: str, font: Path | None, cwd: Path) -> bool:
    """A black tile standing in for a frame that could not be grabbed.

    Filmstrip rows are read positionally -- six tiles, three out then three in
    -- so a missing frame must still occupy its cell or every later row lies.
    """
    chain = "null" if font is None else (
        f"drawtext=fontfile={font.name}:text='{caption}':"
        "x=6:y=h-th-6:fontsize=14:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=4"
    )
    proc = run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-y",
            "-f", "lavfi", "-i", f"color=c=0x181818:s={THUMB_W}x{THUMB_H}",
            "-frames:v", "1", "-vf", chain, "-q:v", "3", str(dest),
        ],
        cwd=cwd,
    )
    return proc.returncode == 0 and dest.exists()


def build_cut_strips(
    clip: Path,
    cuts: list[float],
    duration: float,
    fps: float,
    label_dir: Path,
    work: Path,
) -> tuple[list[str], list[dict[str, Any]]]:
    """One filmstrip row per cut: the last frames out, the first frames in.

    Contact-sheet midpoints cannot answer the two questions a cut is judged on
    -- does it land on the hit, and is the return to a framing a jump cut --
    because both live in the frames immediately either side of the boundary.
    Returns the sheet file names and, per cut, where its row landed.
    """
    if not cuts:
        return [], []

    font_src = find_font()
    font: Path | None = None
    if font_src is not None:
        font = work / "stripfont.ttf"
        if not font.exists():
            shutil.copyfile(font_src, font)

    selected = cuts[:MAX_STRIP_CUTS]
    offsets = [(k, "OUT") for k in STRIP_OUT_OFFSETS] + [(k, "IN") for k in STRIP_IN_OFFSETS]
    sheets: list[str] = []
    placement: list[dict[str, Any]] = []
    last_t = max(duration - 0.75 / fps, 0.0)

    for sheet_idx in range(0, len(selected), STRIP_CUTS_PER_SHEET):
        chunk = selected[sheet_idx : sheet_idx + STRIP_CUTS_PER_SHEET]
        n = sheet_idx // STRIP_CUTS_PER_SHEET + 1
        frames_dir = work / f"cutstrip_{n}"
        frames_dir.mkdir(parents=True, exist_ok=True)

        tile_no = 0
        for row, cut in enumerate(chunk):
            cut_no = sheet_idx + row + 1
            for k, side in offsets:
                # ffmpeg's input -ss returns the first frame whose pts is >= the
                # request, so aim a quarter-frame BEFORE the wanted frame. Aiming
                # at its middle lands on the frame after it -- which on a cut
                # strip means showing the incoming shot in an "outgoing" tile.
                pts = cut + k / fps
                t = min(max(pts - 0.25 / fps, 0.0), last_t)
                tile_no += 1
                caption = f"c{cut_no:02d} {side} {k:+d}f  {max(pts, 0.0):.3f}s"
                dest = frames_dir / f"f{tile_no:04d}.jpg"
                colors = (
                    ("white", "black@0.6") if side == "OUT" else ("black", "0x00D7FF@0.9")
                )
                ok = grab_thumb(clip, t, dest, caption, font, work, *colors)
                if not ok:
                    placeholder_tile(dest, f"{caption}  (no frame)", font, work)
            placement.append({"cut_index": cut_no, "sheet": f"cutstrip_{n}.jpg", "row": row + 1})

        sheet_name = f"cutstrip_{n}.jpg"
        proc = run(
            [
                "ffmpeg", "-hide_banner", "-nostats", "-y",
                "-framerate", "1", "-start_number", "1", "-i", "f%04d.jpg",
                "-vf", f"tile={STRIP_COLS}x{len(chunk)}:padding=4:margin=6:color=0x101010",
                "-frames:v", "1", "-q:v", "3",
                str(label_dir / sheet_name),
            ],
            cwd=frames_dir,
        )
        if proc.returncode == 0 and (label_dir / sheet_name).exists():
            sheets.append(sheet_name)
        else:
            print(f"warn: cut strip {n} tiling failed\n{proc.stderr[-1500:]}", file=sys.stderr)

    return sheets, placement


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
    fps = probe_fps(clip)

    cuts = detect_cuts(clip)
    shots = shots_from_cuts(cuts, duration)
    stats = shot_stats(shots, duration, len(cuts))

    sheets = build_sheets(clip, shots, label_dir, work)
    strips, placement = build_cut_strips(clip, cuts, duration, fps, label_dir, work)
    row_of = {p["cut_index"]: p for p in placement}

    track = motion_track(clip)
    transitions = [classify_transition(clip, cut, fps, duration) for cut in cuts]
    motion_classes: dict[str, int] = {}
    shot_docs = []
    for i, (s, e) in enumerate(shots):
        motion = shot_motion(track, s, e)
        motion_classes[motion["class"]] = motion_classes.get(motion["class"], 0) + 1
        shot_docs.append(
            {
                "index": i + 1,
                "start": round(s, 3),
                "end": round(e, 3),
                "length": round(e - s, 3),
                "motion": motion,
            }
        )

    cuts_doc: dict[str, Any] = {
        "label": label,
        "clip_duration_sec": round(duration, 3),
        "clip_fps": round(fps, 4),
        "scene_threshold": SCENE_THRESHOLD,
        "scene_detect_scale_width": SCENE_SCALE_W,
        **stats,
        "transition_types": {
            kind: sum(1 for t in transitions if t.get("type") == kind)
            for kind in sorted({str(t.get("type")) for t in transitions})
        },
        "motion_classes": motion_classes,
        "cut_times_sec": cuts,
        "cuts": [
            {
                "index": i + 1,
                "t": cut,
                "transition": transitions[i],
                "strip_sheet": row_of.get(i + 1, {}).get("sheet"),
                "strip_row": row_of.get(i + 1, {}).get("row"),
            }
            for i, cut in enumerate(cuts)
        ],
        "shots": shot_docs,
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

    final_clip = label_dir / "clip.mp4"
    shutil.move(str(clip), str(final_clip))
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)

    return {
        "label": label,
        "duration_sec": round(duration, 3),
        "fps": round(fps, 4),
        "total_cuts": stats["total_cuts"],
        "total_shots": stats["total_shots"],
        "cuts_per_minute": stats["cuts_per_minute"],
        "shots_on_sheets": min(len(shots), MAX_SHEET_SHOTS),
        "cuts_on_strips": min(len(cuts), MAX_STRIP_CUTS),
        "sheets": len(sheets),
        "cut_strips": len(strips),
        "transition_types": cuts_doc["transition_types"],
        "motion_classes": motion_classes,
        "files": ["clip.mp4", "cuts.json", *sheets, *strips],
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
        "pack_version": 2,
        "kind": "blind_ab_comparison",
        "scene_threshold": SCENE_THRESHOLD,
        "contact_sheet": {
            "columns": TILE_COLS,
            "rows_per_sheet": TILE_ROWS,
            "thumb_width_px": THUMB_W,
            "max_shots_per_label": MAX_SHEET_SHOTS,
            "frame_position": "shot midpoint",
        },
        "cut_strip": {
            "file_pattern": "cutstrip_N.jpg",
            "one_row_per": "cut",
            "cuts_per_sheet": STRIP_CUTS_PER_SHEET,
            "columns": STRIP_COLS,
            "outgoing_frame_offsets": list(STRIP_OUT_OFFSETS),
            "incoming_frame_offsets": list(STRIP_IN_OFFSETS),
            "offsets_relative_to": "first frame of the incoming shot",
            "caption_legend": "OUT = last frames of the old shot (white caption); "
            "IN = first frames of the new shot (cyan caption)",
        },
        "shot_motion": {
            "method": "1-D cross-correlation of row/column projections",
            "decode": f"{MOTION_W}x{MOTION_H} grey at {MOTION_RATE} fps",
            "units": "pixels at 192 px frame width, per 1/6 s sample",
            "classes": {
                "static": f"moving_share < {MOTION_STATIC_SHARE}",
                "pan": f"net_px >= {MOTION_PAN_NET_PX} with directionality "
                f">= {MOTION_DIRECTIONAL}",
                "unstable": f"moving_share >= {MOTION_JITTER_SHARE} with directionality "
                f"< {MOTION_JITTER_DIRECTIONAL} (motion that goes nowhere)",
                "drift": "moving but neither sustained nor directional",
                "unknown": f"fewer than {MOTION_MIN_SAMPLES} samples inside the shot",
            },
        },
        "transition_typing": {
            "window_frames": 2 * TRANS_HALF_FRAMES + 1,
            "decode": f"{TRANS_W}x{TRANS_H} grey at native fps",
            "classes": {
                "hard": "all the change in one frame pair",
                "dissolve": "change spread over >=2 pairs whose intermediate frames "
                f"are linear blends (residual <= {TRANS_BLEND_RESIDUAL})",
                "fade": f"boundary passes through black (luma <= {TRANS_BLACK_LUMA})",
                "none": "no frame-pair change above the decode noise floor",
            },
        },
        "labels": label_entries,
        "sealed_envelope": "assignment.json",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for entry in label_entries:
        print(
            f"[{entry['label']}] {entry['duration_sec']}s  "
            f"{entry['total_cuts']} cuts  {entry['cuts_per_minute']}/min  "
            f"{entry['sheets']} sheet(s)  {entry['cut_strips']} strip(s)  "
            f"transitions {entry['transition_types']}  motion {entry['motion_classes']}",
            file=sys.stderr,
        )
    print(f"pack written to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
