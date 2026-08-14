"""Blind A/B comparison pack builder for the gauntlet critic.

Takes two videos (or two spans of the same video), randomly but deterministically
assigns them to labels A and B, and builds a scrubbed evidence pack:

    out/assignment.json          SEALED envelope: label -> real source path
    out/manifest.json            neutral metadata only (no source names)
    out/<label>/clip.mp4         the extracted span, 540p
    out/<label>/cuts.json        cut times + transition type, shots + motion,
                                 stats, loudness, the 1-s audio class track,
                                 the ending type, slow-transition events and
                                 the on-soloist track where one was given
    out/<label>/sheet_N.jpg      contact sheets, 6 per row, timestamp burned in
    out/<label>/cutstrip_N.jpg   cut-boundary filmstrips, one cut per row:
                                 3 outgoing frames then 3 incoming frames

Everything outside assignment.json is free of source paths and file names, so a
critic can read the pack without knowing which cut is which.

Pack v2 adds the three measurements a stills-only judge could not make (gap G5,
round R1b): what the frames either side of a cut actually look like, whether a
shot is locked or moving, and whether a boundary is a hard cut or a mix.

Pack v3 closes the two blind spots the P2 R1 ending exposed (gap G16). (a) An
audio class track: applause, music or silence per second, so a judge reading a
-34 dB tail can tell a crowd from a dead room. (b) A slow-transition pass: the
+/-12-frame transition window is half a second wide and typed the human's
visible 5.9 s tail dissolve as "hard", so the ending and every weak boundary
are refitted against a multi-second luma ramp, and mid-shot double images are
reported as ghosting events.

Pack v4 answers the core concert question (gap G5 item 6, #181): given each
label's own correlate_timeline reading, the pack carries an on-soloist track --
per shot, what it is framed on and whether that is the player out front, and per
label, what share of the solo-window screen time went to the soloist. It is
authored rather than detected: no pixel here knows a drummer from a horn player.
Both labels carry one or neither does.

Usage:
    uv run python gauntlet/tools/ab_pack.py \
        --a <video> --b <video> [--a-span S,E] [--b-span S,E] --out <dir> \
        [--a-subjects <cuts.json> --b-subjects <cuts.json>]

Dependencies: stdlib + numpy + ffmpeg/ffprobe on PATH, and the repo's own
resolve_mcp package for the shot-length bin labels -- run it with `uv run` so
the editable install is importable. The bins are shared rather than copied on
purpose: a pack histogram is meant to be read against a correlate_timeline
reading, and two spellings of the same six bins compare as zero overlap.

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
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from resolve_mcp.analysis import subject as subject_module
from resolve_mcp.analysis.correlate import RHYTHM_BINS
from resolve_mcp.video import framing, supers

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

# pack v3 -- slow-transition pass. The +/-12-frame window above is half a
# second wide by construction, so a multi-second dissolve enters and leaves it
# looking like ordinary content drift and the boundary reads "hard" (G16b: the
# human's visible 5.9 s tail dissolve was typed hard). This pass works on a
# coarse whole-clip luma track instead, where a several-second ramp is the
# obvious shape.
SLOW_FPS = 20.0
SLOW_W, SLOW_H = 128, 72
SLOW_TAIL_SEC = 8.0  # trailing window fitted per candidate shot
SLOW_ENDING_SEC = 20.0  # ...and for the clip's own ending, which the tail
# convention owns: scene detection usually calls the black a shot of its own,
# so the ending is fitted against the clip, not against the last shot.
SLOW_MIN_RAMP_SEC = 1.0  # ramp at/over this = a transition, under it = a cut
SLOW_MAX_RAMP_SEC = 12.0  # longer than this is a dim shot, not a transition
SLOW_MONOTONE_SHARE = 0.7  # share of ramp steps that must descend
# Black is the clip's own black: a stage shot can sit at luma 25 all night, so
# "dark" is not a fixed level, but the black an encode settles on is.
SLOW_DARK_LUMA = 6.0  # a window ending above this still holds picture
SLOW_BLACK_FLOOR = 0.2
SLOW_BLACK_MARGIN = 0.15  # ...or this much over the final frame, whichever is higher
SLOW_SLOPE_MIN = 1.0  # descent of this many grey levels per second
SLOW_SLOPE_TOL_SEC = 0.3  # ...may pause this long without ending the descent
SLOW_PLATEAU_SEC = 0.5  # the level the ramp leaves from, read just before it
SLOW_RAMP_TOP_SHARE = 0.9  # the ramp starts where luma leaves that level
SLOW_MIN_SHOT_SEC = 2.0  # shorter shots cannot hide a multi-second ramp
SLOW_WEAK_PEAK_DELTA = 6.0  # boundary this soft is a candidate slow transition

# burned-in supers. The scan reads each frame against the ones SUPER_LAGS_SEC
# later, and two distances rather than one because the two shapes want opposite
# things. A title card is read best from close together -- its frames are
# identical -- but it is also the shortest super in the corpus at 2.3 s, and at a
# two-second lag it fits into a single reading, which the same-pixels-twice test
# will not take. An overlay is the reverse: a second apart the footage under it
# has not moved enough to disagree with itself, and the pair is refused as too
# still. Measured on the anchor: the card needs the 1 s lag, the Taurus People
# personnel lower third needs the 2 s one.
SUPER_RATE = 2.0  # scan rate; a whole song at the lettering grid is 280 MB here
SUPER_LAGS_SEC = (1.0, 2.0)
SUPER_PAD_SEC = 2.0  # native-fps window either side of a scanned edge
SUPER_MERGE_SEC = 0.5  # refined spans this close are one graphic

# mid-shot blended double-image ("ghosting"): two pictures on screen at once,
# inside what scene detection calls a single shot. The reliable signature is
# the blend fit, not the high-frequency dip -- a mix between two similar
# frames does dip, but a mix into a flatter picture need not, so hf is
# reported as evidence rather than required.
GHOST_HALF_SEC = 0.75  # endpoints sampled this far either side of the frame
GHOST_STEP_SEC = 0.25
GHOST_MIN_CHANGE = 8.0  # endpoints must differ structurally by this much grey
GHOST_ALPHA_BAND = (0.2, 0.8)  # the frame must sit between them, not on one
# Tight on purpose: a false "two pictures at once" would mislead the judge more
# than a missed one. A real cross-dissolve fits at 0.04-0.30 residual (measured
# on a synthetic xfade); slow drift inside a real shot sits at 0.31-0.35.
GHOST_BLEND_RESIDUAL = 0.25
GHOST_MERGE_SEC = 1.0  # hits closer than this are one event
GHOST_MAX_EVENTS = 20

# pack v3 -- audio class track. Spectral flatness is the discriminator:
# applause is broadband noise (flatness high, centroid ~1 kHz+), a held chord
# or a decayed cymbal is tonal (flatness low). Method lifted from
# gauntlet/recon/end_tail_zoom.py; the band is clamped well inside the AAC
# lowpass because near-zero bins above it would drag every geometric mean to
# zero and flatten the discriminator itself.
AUDIO_RATE = 48000
AUDIO_WIN_SEC = 1.0
AUDIO_HOP_SEC = 0.5  # overlap: a 1 s window is stable, a 1 s grid is coarse
AUDIO_BAND = (80.0, 8000.0)
AUDIO_HF_BAND = (2000.0, 8000.0)
# Silence is the clip's own room tone, and measured jazz leaves only ~4 dB
# between its quietest passage and an empty room, so no absolute level works.
# A clip is credited with a room tone only when its quietest window sits well
# under its own quiet band (the 20th percentile); silence is then what lies
# within a few dB of that floor, and never inside the quiet band itself.
AUDIO_QUIET_PERCENTILE = 20.0
AUDIO_FLOOR_GAP_DB = 6.0
AUDIO_SILENCE_MARGIN_DB = 6.0
AUDIO_SILENCE_HEADROOM_DB = 3.0
# Applause is seeded strictly and grown loosely. Measured on the Taurus tail:
# the crowd runs flatness 0.14-0.16 at a 2.0-2.5 kHz centroid, while a ride
# cymbal over a quiet passage reaches 0.10 at 1.4 kHz -- so nothing becomes
# applause without a window that clears the strict pair, and the burst's
# quieter edges are then grown from it.
AUDIO_SEED_FLAT = 0.12
AUDIO_SEED_CENTROID = 1800.0
AUDIO_HOLD_FLAT = 0.04
AUDIO_HOLD_CENTROID = 900.0
AUDIO_BRIDGE_SEC = 1.5  # a burst may dip below the hold pair for this long
AUDIO_REFINE_SEC = 1.0  # onset refinement searches this far either side
AUDIO_REFINE_STEP_SEC = 0.05

FONT_CANDIDATES = [
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/consola.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
]

# Shot-length bins: RHYTHM_BINS, imported rather than restated. This pack's
# histogram is read side by side with correlate_timeline's and with the style
# profiles, and the local copy this replaced spelt the same six bins "<2s"
# instead of "<2" -- so no key in a pack ever matched a key in a reading, and
# every comparison between them silently compared nothing.


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
    hist = {name: 0 for name, _ in RHYTHM_BINS}
    for length in lengths:
        for name, edge in RHYTHM_BINS:
            if length < edge:
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
    dtype: Any = np.float64,
) -> np.ndarray:
    """Decode a clip (or a window of it) to an (n, height, width) array.

    `fps=None` keeps the native frame rate -- transition typing needs every
    frame; the motion track resamples to a coarse grid instead.

    `dtype` is float64 for every pass that subtracts frames from each other, which
    on uint8 would wrap a negative difference round to 255. Pass `np.uint8` to hold
    a whole song at once: 8k frames is 80 MB of bytes and 640 MB of doubles.
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
        return np.zeros((0, height, width), dtype=dtype)
    return (
        np.frombuffer(proc.stdout[: n * stride], dtype=np.uint8)
        .reshape(n, height, width)
        .astype(dtype)
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


def blend_fit(pre: np.ndarray, post: np.ndarray, mid: np.ndarray) -> tuple[float, float]:
    """Least-squares fit of `mid` as `pre + alpha * (post - pre)`.

    Returns (residual normalised by the pre->post difference, alpha): residual
    ~0 for a real dissolve frame and ~1 for a frame that is simply something
    else; alpha says how far through the mix the frame sits.
    """
    base = post - pre
    scale = float(np.sqrt((base * base).sum()))
    if scale < 1e-6:
        return 1.0, 0.0
    target = mid - pre
    alpha = float((target * base).sum() / (base * base).sum())
    residual = target - alpha * base
    return float(np.sqrt((residual * residual).sum())) / scale, alpha


def blend_residual(pre: np.ndarray, post: np.ndarray, mid: np.ndarray) -> float:
    """How well `mid` is explained as a linear mix of `pre` and `post`."""
    return blend_fit(pre, post, mid)[0]


def boundary_window(
    clip: Path, cut: float, fps: float, duration: float
) -> tuple[np.ndarray, float]:
    """The native-fps grey window around one boundary, and where it starts.

    Split out of `classify_transition` so the transition typing and the visual
    delta read the same decode: they ask different questions of the identical
    frames, and decoding twice would double the slowest stage of a pack build.
    """
    half = TRANS_HALF_FRAMES / fps
    start = max(cut - half, 0.0)
    dur = max(min(cut + half, duration) - start, 0.0)
    if dur <= 0.0:
        return np.zeros((0, TRANS_H, TRANS_W), dtype=np.float64), start
    return decode_grey(clip, TRANS_W, TRANS_H, start=start, dur=dur), start


def classify_transition(clip: Path, cut: float, fps: float, duration: float) -> dict[str, Any]:
    """hard | dissolve | fade for one boundary, from a native-fps window."""
    arr, start = boundary_window(clip, cut, fps, duration)
    return transition_from(arr, start)


def transition_from(arr: np.ndarray, start: float) -> dict[str, Any]:
    """hard | dissolve | fade for one already-decoded boundary window.

    Five-plus frames across the boundary are enough to separate the three: a
    hard cut puts all the change in one frame pair; a dissolve spreads it over
    several pairs whose intermediate frames are linear blends of the endpoints;
    a fade runs the luma down to (or up from) black.

    Also reports where in the window the boundary sits -- `out_index` is one past
    the outgoing shot's last clean frame, `in_index` the incoming shot's first --
    because a ramp's own ends are the only honest place to read a visual delta
    across, and the detector's nominal cut time is not one of them.
    """
    if len(arr) == 0:
        return {"type": "unknown", "frames_sampled": 0}
    if len(arr) < 3:
        return {"type": "unknown", "frames_sampled": int(len(arr))}

    luma = arr.mean(axis=(1, 2))
    diffs = [float(np.abs(arr[i] - arr[i - 1]).mean()) for i in range(1, len(arr))]
    peak = max(diffs)
    peak_i = diffs.index(peak)
    max_luma = float(luma.max())

    # The two luma extremes stay local. They are how a fade is told from a cut
    # against black, and they were also published per boundary -- where a critic
    # read them as a grade comparison ("boundary min_luma 39-44 vs the human's
    # 22-26") and a round's verdict rested on it until the director ruled colour
    # and grade out of scope. The pack reports what a boundary *does*, and the
    # numbers that decide that are black_frames, ramp_frames and the type.
    doc: dict[str, Any] = {
        "frames_sampled": int(len(arr)),
        "window_start_sec": round(start, 3),
        "peak_frame_delta": round(peak, 2),
        # the peak pair is (peak_i, peak_i + 1): everything up to peak_i belongs to
        # the outgoing shot, everything from peak_i + 1 to the incoming one. A ramp
        # widens these below.
        "out_index": peak_i + 1,
        "in_index": peak_i + 1,
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
    doc["out_index"] = i0 + 1
    doc["in_index"] = i1 + 1

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
# per-cut visual delta + 30-degree-rule flag


def cut_delta(arr: np.ndarray, transition: dict[str, Any]) -> framing.Delta | None:
    """How far the picture steps across one boundary, or None if it cannot be read.

    The typing pass has already found where the boundary really is; this reads
    across it. `None` is the honest answer in three cases, and all three are the
    same refusal: a delta invented from frames nobody located would be a number a
    critic could quote.

    * Nothing decoded.
    * The typing found no boundary -- `none` is a detected cut with no frame-pair
      change above the noise floor, and `unknown` is a window too short to type. In
      both, the indices such a doc carries point at the argmax of decode noise, so
      reading there would produce a confident delta of about zero and flag a cut
      that is not there.
    * The boundary sits too close to the head or tail of its window to leave three
      clean frames either side.
    """
    if len(arr) == 0 or transition.get("type") in {"none", "unknown", None}:
        return None
    if "out_index" not in transition or "in_index" not in transition:
        return None
    try:
        return framing.read_across(arr, int(transition["out_index"]), int(transition["in_index"]))
    except ValueError:
        return None


def read_cut(
    clip: Path, cut: float, fps: float, duration: float
) -> tuple[dict[str, Any], framing.Delta | None]:
    """One decode, both boundary readings: what kind of transition, and how big a step."""
    arr, start = boundary_window(clip, cut, fps, duration)
    transition = transition_from(arr, start)
    return transition, cut_delta(arr, transition)


# --------------------------------------------------------------------------
# burned-in supers: lower thirds, title cards, and the cuts that land on one


def super_lags() -> tuple[int, ...]:
    """The scan distances in scanned frames, deduplicated and never zero."""
    return tuple(sorted({max(1, round(one * SUPER_RATE)) for one in SUPER_LAGS_SEC}))


def super_spans(scan: np.ndarray, lags: Sequence[int]) -> tuple[supers.Span, ...]:
    """The coarse pass: where in the scan a graphic is up at all."""
    usable = [one for one in lags if one < len(scan)]
    return supers.read_run(scan, lags=usable) if usable else ()


def super_mask(scan: np.ndarray, span: supers.Span, lags: Sequence[int]) -> np.ndarray | None:
    """The graphic's own pixels, taken from whichever reading inside the span shows most.

    Not the span's two ends: they can happen to sit in the same locked-off shot, whose
    reading is a refusal, and a mask taken from a refusal is empty. The strongest reading is
    the one that saw the most of the lettering, which is the mask the edge walk wants.

    Every lag is tried, not the shortest. A span found only at the long lag has no reading at
    the short one -- that is why it needed the long one -- and asking at one distance would
    hand back nothing and drop the super on the floor between the scan and the refinement,
    which is a miss no count in the report would show.
    """
    best: np.ndarray | None = None
    for lag in lags:
        for index in range(span.first, min(span.last, len(scan) - 1 - lag) + 1):
            mask = supers.carried(scan[index], scan[index + lag])
            if best is None or mask.sum() > best.sum():
                best = mask
    return None if best is None or not best.any() else best


def refine_super(clip: Path, span: supers.Span, mask: np.ndarray, fps: float) -> supers.Span:
    """The same super again at native rate, so its in and out are frames rather than
    scan steps.

    The scan runs at a couple of frames a second, which places a boundary to within half
    a second -- useless for the convention this exists to check, where a card clearing one
    frame before its entrance and a card clearing over it are the same reading half a
    second wide. The window is cut on frame boundaries so the index the walk returns is a
    source frame number and not a rounding of one.
    """
    lead = max(0, int(round(span.first / SUPER_RATE * fps)) - int(round(SUPER_PAD_SEC * fps)))
    tail = int(round(span.last / SUPER_RATE * fps)) + int(round(SUPER_PAD_SEC * fps))
    window = decode_grey(
        clip,
        supers.GRID_WIDTH,
        supers.GRID_HEIGHT,
        start=lead / fps,
        dur=(tail - lead + 1) / fps,
        dtype=np.uint8,
    )
    middle = int(round((span.first + span.last) / 2 / SUPER_RATE * fps)) - lead
    anchor = max(0, min(middle, len(window) - 1))
    if len(window) == 0:
        return span
    found = supers.edges(window, mask, anchor)
    return span._replace(
        first=lead + found.first,
        last=lead + found.last,
        ramp_in=found.ramp_in,
        ramp_out=found.ramp_out,
    )


def merge_supers(spans: Sequence[supers.Span], fps: float) -> list[supers.Span]:
    """One super per graphic, where two scan spans refined onto the same one.

    A super whose picture holds still in the middle comes back as two spans with an
    unread stretch between them; refined, both walk out to the same frames. Merging on
    the refined frames rather than bridging harder in the scan keeps the coarse pass
    honest about what it actually saw.
    """
    merged: list[supers.Span] = []
    gap = max(1, int(round(SUPER_MERGE_SEC * fps)))
    for span in sorted(spans, key=lambda one: one.visible_first):
        if merged and span.visible_first <= merged[-1].visible_last + gap:
            last = merged[-1]
            merged[-1] = last._replace(
                first=min(last.first, span.first),
                last=max(last.last, span.last),
                ramp_in=last.ramp_in if last.first <= span.first else span.ramp_in,
                ramp_out=last.ramp_out if last.last >= span.last else span.ramp_out,
                top=min(last.top, span.top),
                left=min(last.left, span.left),
                bottom=max(last.bottom, span.bottom),
                right=max(last.right, span.right),
                pixels=max(last.pixels, span.pixels),
                pairs=last.pairs + span.pairs,
            )
            continue
        merged.append(span)
    return merged


def super_scan(clip: Path, fps: float, cuts: Sequence[float]) -> dict[str, Any]:
    """Every burned-in graphic in the clip, and every cut that lands inside one.

    Two passes for one reason: the coarse one can afford to look at the whole clip and
    the fine one cannot. A scan of a whole song at the resolution lettering needs is a
    few hundred megabytes at two frames a second and several gigabytes at twenty-four.
    """
    lags = super_lags()
    scan = decode_grey(
        clip, supers.GRID_WIDTH, supers.GRID_HEIGHT, fps=SUPER_RATE, dtype=np.uint8
    )
    refined: list[supers.Span] = []
    for span in super_spans(scan, lags):
        mask = super_mask(scan, span, lags)
        if mask is not None:
            refined.append(refine_super(clip, span, mask, fps))
    spans = merge_supers(refined, fps)
    frames = [int(round(cut * fps)) for cut in cuts]
    review = supers.review(spans, frames)
    # `t` and `end` in seconds beside the frames, exclusive at the end, because that is the
    # shape every other catalog in this repo is read in -- and correlate_timeline joins this
    # one the same way it joins tunes.
    for record in review["supers"]:
        record["t"] = round(record["visible_first"] / fps, 3)
        record["end"] = round((record["visible_last"] + 1) / fps, 3)
    review["scan"] = {
        "rate_fps": SUPER_RATE,
        "lag_frames": list(lags),
        "grid": f"{supers.GRID_WIDTH}x{supers.GRID_HEIGHT}",
        "frames_scanned": len(scan),
    }
    return review


def straddled_cuts(review: dict[str, Any]) -> set[int]:
    """The source frames of the cuts that land inside a super, for tagging the cut list."""
    return {int(one["cut"]) for one in review["straddles"]}


# --------------------------------------------------------------------------
# slow transitions: multi-second ramps the +/-12-frame window cannot see


def luma_track(clip: Path) -> dict[str, np.ndarray]:
    """Coarse whole-clip track: time, mean luma, and high-frequency energy.

    One decode serves both v3 video passes. `hf` is the mean absolute spatial
    gradient -- it collapses when a frame is a soft blend of two pictures.
    """
    arr = decode_grey(clip, SLOW_W, SLOW_H, fps=SLOW_FPS)
    if len(arr) == 0:
        empty = np.zeros(0, dtype=np.float64)
        return {"t": empty, "luma": empty, "hf": empty, "_frames": arr}
    luma = arr.mean(axis=(1, 2))
    hf = np.abs(np.diff(arr, axis=1)).mean(axis=(1, 2)) + np.abs(np.diff(arr, axis=2)).mean(
        axis=(1, 2)
    )
    t = np.arange(len(arr), dtype=np.float64) / SLOW_FPS
    return {"t": t, "luma": luma, "hf": hf, "_frames": arr}


def ramp_to_black(t: np.ndarray, luma: np.ndarray) -> dict[str, Any]:
    """Type the way a window of luma arrives at its final frame.

    Three steps, in the order that survives real footage: find the black the
    window ends on (relative to the encode's own black, not a fixed level);
    walk back over the sustained descent that reached it; then put the ramp's
    start where luma left the level it had been holding. A hard cut crosses
    that in one frame, a dissolve or a fade takes seconds. Measured against the
    human Taurus tail, whose stage shot sits at luma 26 -- an absolute "still
    lit" threshold called that dissolve dim and put its start ten seconds early.
    """
    if len(t) < 3:
        return {"kind": "unknown", "frames_sampled": int(len(t))}
    final = float(luma[-1])
    fps = (len(t) - 1) / float(t[-1] - t[0]) if t[-1] > t[0] else SLOW_FPS
    doc: dict[str, Any] = {
        "frames_sampled": int(len(t)),
        "final_luma": round(final, 2),
        "max_luma": round(float(luma.max()), 2),
    }
    if final > SLOW_DARK_LUMA:
        doc["kind"] = "ends_lit"
        return doc

    black_level = max(SLOW_BLACK_FLOOR, final + SLOW_BLACK_MARGIN)
    black_i = len(luma) - 1
    while black_i > 0 and luma[black_i - 1] <= black_level:
        black_i -= 1
    doc["black_level"] = round(black_level, 2)
    doc["black_at_sec"] = round(float(t[black_i]), 3)
    doc["black_hold_sec"] = round(float(t[-1] - t[black_i]), 3)
    if black_i == 0 or float(luma.max()) <= SLOW_DARK_LUMA:
        doc["kind"] = "black_window"
        return doc

    # the sustained descent that reached black
    k = max(int(round(0.15 * fps)), 1)
    tol = max(int(round(SLOW_SLOPE_TOL_SEC * fps)), 1)
    slope = np.zeros(len(luma), dtype=np.float64)
    for i in range(len(luma)):
        lo, hi = max(i - k, 0), min(i + k, len(luma) - 1)
        slope[i] = (luma[hi] - luma[lo]) / max((hi - lo) / fps, 1e-6)
    descent_i, viol, j = black_i, 0, black_i
    while j > 0:
        j -= 1
        if slope[j] <= -SLOW_SLOPE_MIN:
            descent_i, viol = j, 0
        else:
            viol += 1
            if viol > tol:
                break

    plateau_lo = max(descent_i - int(round(SLOW_PLATEAU_SEC * fps)), 0)
    plateau = float(luma[plateau_lo : descent_i + 1].max())
    top = SLOW_RAMP_TOP_SHARE * plateau
    holding = [i for i in range(descent_i, black_i + 1) if luma[i] >= top]
    ramp_i = holding[-1] if holding else descent_i

    steps = np.diff(luma[ramp_i : black_i + 1])
    descending = float((steps <= 0.0).mean()) if len(steps) else 1.0
    ramp = float(t[black_i] - t[ramp_i])
    doc.update(
        {
            "descent_start_sec": round(float(t[descent_i]), 3),
            "plateau_luma": round(plateau, 2),
            "ramp_start_sec": round(float(t[ramp_i]), 3),
            "ramp_start_luma": round(float(luma[ramp_i]), 2),
            "ramp_sec": round(ramp, 3),
            "monotone_share": round(descending, 3),
        }
    )
    if ramp < SLOW_MIN_RAMP_SEC:
        doc["kind"] = "hard_to_black"
    elif ramp <= SLOW_MAX_RAMP_SEC and descending >= SLOW_MONOTONE_SHARE:
        doc["kind"] = "dissolve_to_black"
    else:
        doc["kind"] = "dim_to_black"
    return doc


def slow_transition_scan(
    track: dict[str, np.ndarray],
    shots: list[tuple[float, float]],
    transitions: list[dict[str, Any]],
    duration: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fit trailing windows; return slow-ramp events plus the clip's ending.

    Per-shot candidates are any shot long enough to hide a multi-second ramp
    and any shot whose closing scene-detect boundary was weak -- a soft
    boundary is exactly what a dissolve leaves behind. The ending is fitted
    against the clip's own last seconds rather than the last shot, because a
    ramp into black usually trips scene detection somewhere along it and the
    black then reads as a shot of its own.
    """
    t, luma = track["t"], track["luma"]
    if len(t) == 0:
        return [], {"kind": "unknown", "frames_sampled": 0}

    tail_start = max(duration - SLOW_ENDING_SEC, 0.0)
    sel = t >= tail_start
    ending = ramp_to_black(t[sel], luma[sel])
    ending["window"] = [round(tail_start, 3), round(duration, 3)]

    weak_end = set()
    for i in range(len(shots) - 1):
        doc = transitions[i] if i < len(transitions) else {}
        peak = float(doc.get("peak_frame_delta", 0.0) or 0.0)
        soft = doc.get("type") in {"dissolve", "fade", "none", "unknown"}
        if soft or peak < SLOW_WEAK_PEAK_DELTA:
            weak_end.add(i)

    events: list[dict[str, Any]] = []
    for i, (start, end) in enumerate(shots):
        if not (i in weak_end or (end - start) >= SLOW_MIN_SHOT_SEC):
            continue
        win_start = max(end - SLOW_TAIL_SEC, start)
        sel = (t >= win_start) & (t <= end)
        doc = ramp_to_black(t[sel], luma[sel])
        if doc["kind"] not in {"dissolve_to_black", "dim_to_black"}:
            continue
        doc["shot_index"] = i + 1
        doc["window"] = [round(win_start, 3), round(end, 3)]
        doc["is_last_shot"] = i == len(shots) - 1
        events.append(doc)
    return events, ending


def ghost_scan(
    track: dict[str, np.ndarray],
    shots: list[tuple[float, float]],
    exclude: tuple[float, float] | None = None,
) -> list[dict[str, Any]]:
    """Mid-shot blended double-images: an hf dip whose frames are linear mixes.

    Scene detection sees one shot; the eye (and the critics, in the R1
    thumbnails) sees two pictures on screen at once. The signature is narrow:
    high-frequency energy drops well under the shot's own median while the
    frames in the dip are explained as a blend of the frames either side.
    """
    t, hf, frames = track["t"], track["hf"], track["_frames"]
    if len(t) == 0:
        return []
    half = max(int(round(GHOST_HALF_SEC * SLOW_FPS)), 1)
    step = max(int(round(GHOST_STEP_SEC * SLOW_FPS)), 1)
    merge = max(int(round(GHOST_MERGE_SEC * SLOW_FPS)), step)
    events: list[dict[str, Any]] = []
    for si, (start, end) in enumerate(shots):
        if end - start < SLOW_MIN_SHOT_SEC or len(events) >= GHOST_MAX_EVENTS:
            continue
        idx = np.flatnonzero((t >= start) & (t <= end))
        if len(idx) < 2 * half + 1:
            continue
        med = float(np.median(hf[idx])) or 1.0
        hits: list[dict[str, Any]] = []
        for i in range(int(idx[0]) + half, int(idx[-1]) - half + 1, step):
            if exclude and exclude[0] <= float(t[i]) <= exclude[1]:
                continue  # the ending ramp is typed by the slow pass, not here
            pre, post, mid = frames[i - half], frames[i + half], frames[i]
            if min(float(pre.mean()), float(post.mean())) <= SLOW_DARK_LUMA:
                continue  # a ramp into or out of black is the ending pass's job
            # structural change only: a stage light dimming makes every frame
            # a perfect "blend" of its neighbours without any double image
            change = float(np.abs((post - post.mean()) - (pre - pre.mean())).mean())
            if change < GHOST_MIN_CHANGE:
                continue
            resid, alpha = blend_fit(pre, post, mid)
            if resid > GHOST_BLEND_RESIDUAL or not (
                GHOST_ALPHA_BAND[0] <= alpha <= GHOST_ALPHA_BAND[1]
            ):
                continue
            hits.append(
                {
                    "i": i,
                    "resid": resid,
                    "alpha": alpha,
                    "change": change,
                    "hf_ratio": float(hf[i]) / med,
                }
            )
        for group in group_hits(hits, merge):
            first, last = group[0]["i"], group[-1]["i"]
            events.append(
                {
                    "shot_index": si + 1,
                    "start": round(float(t[max(first - half, 0)]), 3),
                    "end": round(float(t[min(last + half, len(t) - 1)]), 3),
                    "duration": round(float(t[min(last + half, len(t) - 1)] - t[first - half]), 3),
                    "blend_residual": round(max(g["resid"] for g in group), 3),
                    "alpha_range": [
                        round(min(g["alpha"] for g in group), 3),
                        round(max(g["alpha"] for g in group), 3),
                    ],
                    "structure_change": round(max(g["change"] for g in group), 2),
                    "hf_dip_ratio": round(min(g["hf_ratio"] for g in group), 3),
                    "kind": "blend_ghost",
                }
            )
            if len(events) >= GHOST_MAX_EVENTS:
                break
    return events


def ending_ramp(ending: dict[str, Any]) -> tuple[float, float] | None:
    """The span the ending pass owns, so the ghost scan does not re-report it."""
    if "black_at_sec" not in ending:
        return None
    start = ending.get("descent_start_sec", ending.get("ramp_start_sec"))
    if start is None:
        return None
    return float(start), float(ending["black_at_sec"])


def group_hits(hits: list[dict[str, Any]], merge: int) -> list[list[dict[str, Any]]]:
    """Split hit frames into runs separated by more than `merge` frames."""
    groups: list[list[dict[str, Any]]] = []
    for hit in hits:
        if groups and hit["i"] - groups[-1][-1]["i"] <= merge:
            groups[-1].append(hit)
        else:
            groups.append([hit])
    return groups


# --------------------------------------------------------------------------
# loudness curve

RMS_FLOOR_DB = -100.0  # what digital silence is reported as, in place of -inf


def loudness_curve(samples: np.ndarray) -> list[dict[str, float]] | None:
    """1-second-window RMS level in dBFS. Returns None if the clip has no audio.

    Read off the mono PCM `decode_mono` already returned, rather than out of a
    second ffmpeg astats pass over the same file: the whole clip's audio is in
    memory by the time this is wanted, and decoding it twice bought nothing but
    a second decode of every pack.

    Whole windows only. A trailing part-second would be a level measured over
    less music than every other point on the curve, and it sits exactly where a
    reader is looking hardest -- at the ending.
    """
    length = int(AUDIO_RATE)
    if len(samples) < length:
        return None
    points: list[dict[str, float]] = []
    for start in range(0, len(samples) - length + 1, length):
        seg = samples[start : start + length]
        rms = float(np.sqrt((seg**2).mean()))
        level = 20.0 * float(np.log10(rms)) if rms > 0.0 else RMS_FLOOR_DB
        points.append({"t": round(start / AUDIO_RATE, 2), "rms_db": round(level, 2)})
    return points or None


# --------------------------------------------------------------------------
# audio class track


def decode_mono(clip: Path) -> np.ndarray:
    """Decode the clip's audio to mono float32 PCM at AUDIO_RATE."""
    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(clip),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(AUDIO_RATE),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if not proc.stdout:
        return np.zeros(0, dtype=np.float64)
    usable = len(proc.stdout) - (len(proc.stdout) % 4)
    return np.frombuffer(proc.stdout[:usable], dtype=np.float32).astype(np.float64)


def audio_features(x: np.ndarray) -> list[dict[str, float]]:
    """Per-window RMS, spectral flatness, centroid and high-band share."""
    length = int(AUDIO_WIN_SEC * AUDIO_RATE)
    hop = max(int(AUDIO_HOP_SEC * AUDIO_RATE), 1)
    if len(x) < length:
        return []
    win = np.hanning(length)
    freqs = np.fft.rfftfreq(length, 1.0 / AUDIO_RATE)
    band = (freqs >= AUDIO_BAND[0]) & (freqs <= AUDIO_BAND[1])
    high = (freqs >= AUDIO_HF_BAND[0]) & (freqs <= AUDIO_HF_BAND[1])
    out: list[dict[str, float]] = []
    for start in range(0, len(x) - length + 1, hop):
        seg = x[start : start + length]
        rms = float(np.sqrt((seg**2).mean() + 1e-20))
        power = (np.abs(np.fft.rfft(seg * win)) + 1e-12) ** 2
        pb = power[band]
        flat = float(np.exp(np.log(pb).mean()) / pb.mean())
        centroid = float((freqs[band] * pb).sum() / pb.sum())
        out.append(
            {
                "t": round(start / AUDIO_RATE, 2),
                "rms_db": round(20.0 * float(np.log10(rms + 1e-12)), 2),
                "flatness": round(flat, 5),
                "centroid_hz": round(centroid, 1),
                "hf_share": round(float(power[high].sum() / pb.sum()), 4),
            }
        )
    return out


def grow_from_seeds(seed: np.ndarray, hold: np.ndarray, bridge: int) -> np.ndarray:
    """Expand each seed window outwards over hold windows, bridging short dips."""
    out = seed.copy()
    n = len(seed)
    for i in np.flatnonzero(seed):
        for step in (-1, 1):
            j = int(i) + step
            pending: list[int] = []
            while 0 <= j < n:
                if hold[j]:
                    out[j] = True
                    for p in pending:
                        out[p] = True
                    pending = []
                else:
                    pending.append(j)
                    if len(pending) > bridge:
                        break
                j += step
    return out


def classify_audio(features: list[dict[str, float]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """applause | music | silence per window, plus the thresholds used.

    Applause is decided first and by spectrum, because the case that matters
    is a fading crowd under a tail dissolve: it is quiet enough to trip any
    level test, and calling that "silence" is the exact mistake G16 is about.
    Silence is what is left when a window is at the clip's own room tone.
    """
    if not features:
        return [], {}
    rms = np.array([f["rms_db"] for f in features], dtype=np.float64)
    flat = np.array([f["flatness"] for f in features], dtype=np.float64)
    cent = np.array([f["centroid_hz"] for f in features], dtype=np.float64)

    quiet_band = float(np.percentile(rms, AUDIO_QUIET_PERCENTILE))
    floor_db = float(rms.min())
    has_floor = floor_db <= quiet_band - AUDIO_FLOOR_GAP_DB
    silence_at = (
        min(floor_db + AUDIO_SILENCE_MARGIN_DB, quiet_band - AUDIO_SILENCE_HEADROOM_DB)
        if has_floor
        else float("-inf")
    )
    seed = (flat >= AUDIO_SEED_FLAT) & (cent >= AUDIO_SEED_CENTROID)
    hold = (flat >= AUDIO_HOLD_FLAT) & (cent >= AUDIO_HOLD_CENTROID)
    bridge = max(int(round(AUDIO_BRIDGE_SEC / AUDIO_HOP_SEC)), 1)
    applause = grow_from_seeds(seed, hold, bridge)

    raw = [
        "applause" if applause[i] else ("silence" if rms[i] <= silence_at else "music")
        for i in range(len(features))
    ]
    # de-flicker: one window disagreeing with identical neighbours is a
    # measurement wobble, not a change in what the room is doing. Applause
    # windows are left alone -- the grow pass already decided those.
    smooth = list(raw)
    for i in range(1, len(raw) - 1):
        if raw[i] == "applause":
            continue
        if raw[i] != raw[i - 1] and raw[i - 1] == raw[i + 1]:
            smooth[i] = raw[i - 1]

    track = [{**f, "class": smooth[i]} for i, f in enumerate(features)]
    thresholds = {
        "window_sec": AUDIO_WIN_SEC,
        "hop_sec": AUDIO_HOP_SEC,
        "band_hz": list(AUDIO_BAND),
        "silence_at_or_below_db": round(silence_at, 2) if has_floor else None,
        "room_tone_found": bool(has_floor),
        "clip_min_rms_db": round(floor_db, 2),
        "clip_quiet_band_db": round(quiet_band, 2),
        "applause_seed": f"flatness >= {AUDIO_SEED_FLAT} and centroid >= {AUDIO_SEED_CENTROID}",
        "applause_hold": f"flatness >= {AUDIO_HOLD_FLAT} and centroid >= {AUDIO_HOLD_CENTROID}",
        "applause_seed_windows": int(seed.sum()),
        "bridge_sec": AUDIO_BRIDGE_SEC,
    }
    return track, thresholds


def refine_onset(x: np.ndarray, t_win: float) -> float:
    """Sharpen a class boundary to the loudness edge nearest it.

    The class grid is a 1-s window, so a boundary lands within a second of the
    truth; the edge that caused it is usually a step in level (a chord ending,
    a room opening into music). Returns the refined time, or the input if
    nothing beats the grid.
    """
    step = max(int(AUDIO_REFINE_STEP_SEC * AUDIO_RATE), 1)
    lo = max(int((t_win - AUDIO_REFINE_SEC) * AUDIO_RATE), 0)
    hi = min(int((t_win + AUDIO_REFINE_SEC) * AUDIO_RATE), len(x))
    span = max(int(0.25 * AUDIO_RATE), step)
    best_t, best_d = t_win, 0.0
    for s in range(lo, hi - step + 1, step):
        pre = x[max(s - span, 0) : s]
        post = x[s : s + span]
        if len(pre) < step or len(post) < step:
            continue
        a = 20.0 * float(np.log10(np.sqrt((pre**2).mean()) + 1e-12))
        b = 20.0 * float(np.log10(np.sqrt((post**2).mean()) + 1e-12))
        if abs(b - a) > best_d:
            best_d, best_t = abs(b - a), s / AUDIO_RATE
    return round(best_t, 2) if best_d > 0.0 else t_win


def audio_class_spans(
    track: list[dict[str, Any]], samples: np.ndarray | None = None
) -> list[dict[str, Any]]:
    """Collapse the per-window classes into contiguous runs.

    Each run after the first also carries `start_refined`: the class grid puts
    a boundary within a window of the truth, and the loudness edge inside that
    window says where it actually happened.
    """
    spans: list[dict[str, Any]] = []
    for row in track:
        end = round(float(row["t"]) + AUDIO_WIN_SEC, 2)
        if spans and spans[-1]["class"] == row["class"]:
            spans[-1]["end"] = end
        else:
            spans.append({"class": row["class"], "start": round(float(row["t"]), 2), "end": end})
    for i, span in enumerate(spans):
        if i + 1 < len(spans):  # overlapping windows: a run ends where the next begins
            span["end"] = spans[i + 1]["start"]
        span["duration"] = round(span["end"] - span["start"], 2)
        if i and samples is not None and len(samples):
            span["start_refined"] = refine_onset(samples, float(span["start"]))
    return spans


def audio_class_summary(track: list[dict[str, Any]], spans: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {name: 0.0 for name in ("music", "applause", "silence")}
    for row in track:
        totals[str(row["class"])] += AUDIO_HOP_SEC
    firsts: dict[str, Any] = {}
    for name in ("music", "applause", "silence"):
        hit = next((s for s in spans if s["class"] == name), None)
        firsts[f"first_{name}_sec"] = (
            hit.get("start_refined", hit["start"]) if hit is not None else None
        )
    return {
        "windows": len(track),
        "seconds_by_class": {k: round(v, 2) for k, v in totals.items()},
        **firsts,
        "final_class": track[-1]["class"] if track else None,
        "spans": len(spans),
    }


# --------------------------------------------------------------------------
# subject track (who the shot is on, crossed with who is soloing)


SUBJECT_FIELDS = ("subject", "subject_kind", "on_soloist", "on_soloist_by", "on_soloist_seconds")
"""The only fields carried over from a correlate reading.

Everything else in that file names the cut -- the timeline, the camera clips, the angle
roles -- and a blind pack that carries a timeline name is not a blind pack. Copying the
four columns by name is the guard: a field added to correlate later cannot leak through
this without someone adding it here.
"""


def load_subject_track(path: Path) -> list[dict[str, Any]]:
    """The on-soloist track out of a correlate_timeline cuts file, stripped to four columns."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = doc.get("cuts", []) if isinstance(doc, dict) else doc
    if not isinstance(rows, list):
        sys.exit(f"error: {path} is neither a correlate cuts file nor a list of shots")
    track = []
    for row in rows:
        if not isinstance(row, dict) or "t" not in row or "seconds" not in row:
            sys.exit(f"error: {path} has a shot with no t/seconds -- not a correlate cuts file")
        track.append(
            {
                "t": float(row["t"]),
                "seconds": float(row["seconds"]),
                **{name: row.get(name) for name in SUBJECT_FIELDS},
            }
        )
    return track


def place_subject_track(
    track: list[dict[str, Any]], span: tuple[float, float] | None, duration: float
) -> dict[str, Any]:
    """The track in the extracted clip's own time: shifted, and cut to what the clip holds.

    A shot the span cuts through keeps its place so a judge can still read what shot 7 is on.
    Its seconds still count where the front held all the way through it -- the line is the
    same either side of the cut, so the part inside the clip is exact arithmetic rather than
    a guess. Where the front changed inside that shot they are dropped: scaling a split
    across a boundary nobody measured would overstate whichever line the shot opened on.
    """
    start = span[0] if span else 0.0
    placed: list[dict[str, Any]] = []
    partial = 0
    for row in track:
        head, tail = row["t"] - start, row["t"] - start + row["seconds"]
        if tail <= 0 or head >= duration:
            continue
        whole = head >= 0 and tail <= duration
        partial += 0 if whole else 1
        inside = round(min(tail, duration) - max(head, 0.0), 3)
        placed.append(
            {
                "start": round(max(head, 0.0), 3),
                "end": round(min(tail, duration), 3),
                "whole": whole,
                "counted_seconds": _counted(row.get("on_soloist_seconds"), whole, inside),
                **{name: row.get(name) for name in SUBJECT_FIELDS},
            }
        )
    counted = [{"on_soloist_seconds": row["counted_seconds"]} for row in placed]
    return {
        "shots": placed,
        "shots_outside_clip": len(track) - len(placed),
        "shots_cut_by_the_span": partial,
        "shots_left_out_of_the_share": sum(1 for row in placed if row["counted_seconds"] is None),
        "summary": subject_module.summary(counted),
    }


def _counted(split: Any, whole: bool, inside: float) -> dict[str, float] | None:
    """What a placed shot contributes to the share: all of it, the part inside, or nothing."""
    if not isinstance(split, dict) or not split:
        return None
    if whole:
        return {str(line): float(held) for line, held in split.items()}
    if len(split) > 1:
        return None
    return {str(next(iter(split))): inside}


def attach_subjects(shot_docs: list[dict[str, Any]], placed: list[dict[str, Any]]) -> None:
    """Label each *detected* shot with the authored shot that holds most of it.

    The pack's own shots come from a scene scan and the track comes from the timeline, so the
    two boundary lists agree only as well as the detector does. Matching by overlap rather
    than by index means a missed cut reads as one shot carrying the subject it mostly is,
    instead of a track silently sliding one shot out of step for the rest of the film.
    """
    for shot in shot_docs:
        best, held = None, 0.0
        for row in placed:
            overlap = min(shot["end"], row["end"]) - max(shot["start"], row["start"])
            if overlap > held:
                best, held = row, overlap
        shot["subject"] = (
            None
            if best is None
            else {
                "subject": best["subject"],
                "subject_kind": best["subject_kind"],
                "on_soloist": best["on_soloist"],
                "on_soloist_by": best["on_soloist_by"],
                "overlap_sec": round(held, 3),
            }
        )


# --------------------------------------------------------------------------
# contact sheets


def find_font() -> Path | None:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


class Tile(NamedTuple):
    """One captioned frame to grab: when, where it goes, and how it is labelled."""

    t: float
    dest: Path
    caption: str
    text_color: str = "white"
    box_color: str = "black@0.6"


def thumb_chain(caption: str, font: Path | None, text_color: str, box_color: str) -> str:
    """The filter chain one tile is made with: fit into the tile, burn the caption.

    `font` is a bare file name resolved against the run's cwd -- never an absolute
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
    return chain


def grab_tiles(clip: Path, tiles: Sequence[Tile], font: Path | None, cwd: Path) -> list[bool]:
    """Grab several frames of one clip in a single ffmpeg run. Returns one flag per tile.

    One process, one seek per tile, one output file per tile: the frames are
    independent, so they go in as separate inputs and come out through a
    filter_complex with a map each. A tile per process is what this replaced --
    six per cut and thirty-six cuts is 216 ffmpeg starts per label, nearly all
    of it process and decoder setup for one JPEG.

    Success is read off the files rather than the exit code, because a run that
    fails on one input still writes the others, and every tile that exists is a
    real frame. The caller stands a placeholder in for the rest.
    """
    if not tiles:
        return []
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-y"]
    for tile in tiles:
        cmd += ["-ss", f"{tile.t:.3f}", "-i", str(clip)]
    chains = ";".join(
        f"[{i}:v]{thumb_chain(tile.caption, font, tile.text_color, tile.box_color)}[t{i}]"
        for i, tile in enumerate(tiles)
    )
    cmd += ["-filter_complex", chains]
    for i, tile in enumerate(tiles):
        cmd += ["-map", f"[t{i}]", "-frames:v", "1", "-q:v", "3", str(tile.dest)]
    run(cmd, cwd=cwd)
    return [tile.dest.exists() for tile in tiles]


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
    """Grab one frame at t, scaled to THUMB_W x THUMB_H, timestamp burned in."""
    return grab_tiles(clip, [Tile(t, dest, caption, text_color, box_color)], font, cwd)[0]


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


def delta_caption(reading: framing.Delta | None) -> str:
    """The visual delta as it appears on a filmstrip row, or nothing.

    ASCII only: the caption goes through ffmpeg's drawtext against whatever font the
    box happens to have, and a glyph it cannot render is a blank where a number
    should be.
    """
    if reading is None:
        return "  d=?"
    return f"  d={reading.delta:.2f}{' JUMP' if reading.jump_cut else ''}"


def build_cut_strips(
    clip: Path,
    cuts: list[float],
    duration: float,
    fps: float,
    label_dir: Path,
    work: Path,
    deltas: Sequence[framing.Delta | None] = (),
) -> tuple[list[str], list[dict[str, Any]]]:
    """One filmstrip row per cut: the last frames out, the first frames in.

    Contact-sheet midpoints cannot answer the two questions a cut is judged on
    -- does it land on the hit, and is the return to a framing a jump cut --
    because both live in the frames immediately either side of the boundary.
    Returns the sheet file names and, per cut, where its row landed.

    The second of those questions now has a number, so it is captioned onto the
    row's first tile: a critic reading the sheet sees `d=0.62`, or `d=0.08 JUMP`
    on a boundary the 30-degree check flagged, beside the frames it was measured
    from. Left in cuts.json alone it would be a number nobody looking at the
    picture ever meets.
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
            tiles: list[Tile] = []
            for k, side in offsets:
                # ffmpeg's input -ss returns the first frame whose pts is >= the
                # request, so aim a quarter-frame BEFORE the wanted frame. Aiming
                # at its middle lands on the frame after it -- which on a cut
                # strip means showing the incoming shot in an "outgoing" tile.
                pts = cut + k / fps
                t = min(max(pts - 0.25 / fps, 0.0), last_t)
                tile_no += 1
                caption = f"c{cut_no:02d} {side} {k:+d}f  {max(pts, 0.0):.3f}s"
                if (k, side) == offsets[0]:
                    caption += delta_caption(deltas[cut_no - 1] if cut_no <= len(deltas) else None)
                colors = (
                    ("white", "black@0.6") if side == "OUT" else ("black", "0x00D7FF@0.9")
                )
                tiles.append(Tile(t, frames_dir / f"f{tile_no:04d}.jpg", caption, *colors))
            # The whole row in one ffmpeg run: six frames of one clip, six maps.
            for tile, ok in zip(tiles, grab_tiles(clip, tiles, font, work), strict=True):
                if not ok:
                    placeholder_tile(tile.dest, f"{tile.caption}  (no frame)", font, work)
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
    subjects: Path | None = None,
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

    # The boundary reads come before the filmstrips: a row's caption carries its own
    # visual delta, so the number has to exist before the tile it is drawn on.
    reads = [read_cut(clip, cut, fps, duration) for cut in cuts]
    transitions = [one for one, _ in reads]
    deltas = [one for _, one in reads]
    delta_summary = framing.summarize(
        [one for one in deltas if one is not None],
        unread=sum(1 for one in deltas if one is None),
    )

    sheets = build_sheets(clip, shots, label_dir, work)
    strips, placement = build_cut_strips(clip, cuts, duration, fps, label_dir, work, deltas)
    row_of = {p["cut_index"]: p for p in placement}

    track = motion_track(clip)

    # What the graphics layer put on the picture, and which cuts land inside it. Measured
    # off the render because nothing else can see it: a super is burned in by the time
    # anybody watches, and the timeline it came from says only that a title track exists.
    supers_review = super_scan(clip, fps, cuts)
    straddled = straddled_cuts(supers_review)

    # v3: the same boundaries again at multi-second scale, where a dissolve
    # the +/-12-frame window cannot hold is the obvious shape.
    slow = luma_track(clip)
    slow_events, ending = slow_transition_scan(slow, shots, transitions, duration)
    tail_ramp = ending_ramp(ending)
    ghosts = ghost_scan(slow, shots, exclude=tail_ramp)

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

    subject_track = (
        None
        if subjects is None
        else place_subject_track(load_subject_track(subjects), span, duration)
    )
    if subject_track is not None:
        attach_subjects(shot_docs, subject_track["shots"])

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
        "visual_delta": delta_summary,
        "supers": supers_review,
        "cut_times_sec": cuts,
        "cuts": [
            {
                "index": i + 1,
                "t": cut,
                "transition": transitions[i],
                "delta": deltas[i].as_record() if deltas[i] is not None else None,
                # Whether a graphic was on screen either side of this cut (#183). The
                # straddle is the fault; a super arriving with the shot or clearing for
                # it is not, and the review has already told those apart.
                "straddles_super": int(round(cut * fps)) in straddled,
                "strip_sheet": row_of.get(i + 1, {}).get("sheet"),
                "strip_row": row_of.get(i + 1, {}).get("row"),
            }
            for i, cut in enumerate(cuts)
        ],
        "shots": shot_docs,
        "ending": ending,
        "slow_transitions": slow_events,
        "ghosting": ghosts,
    }
    if subject_track is not None:
        cuts_doc["subject_track"] = subject_track

    samples = decode_mono(clip)
    features = audio_features(samples)
    audio_track, audio_thresholds = classify_audio(features)
    if audio_track:
        spans = audio_class_spans(audio_track, samples)
        cuts_doc["audio_class_1s"] = audio_track
        cuts_doc["audio_class_spans"] = spans
        cuts_doc["audio_class_thresholds"] = audio_thresholds
        cuts_doc["audio_class_summary"] = audio_class_summary(audio_track, spans)
    curve = loudness_curve(samples)
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
    # The supers again as a catalog of their own, so correlate_timeline can join them onto a
    # timeline's cuts the way it joins the cut deltas: same records shape, one row per super.
    (label_dir / "supers.json").write_text(
        json.dumps(
            {
                "kind": "supers",
                "count": len(supers_review["supers"]),
                "supers": supers_review["supers"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

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
        "visual_delta": delta_summary,
        "supers": {
            key: supers_review[key]
            for key in ("cards", "overlays", "straddled", "held_frames")
        },
        "ending": {
            k: ending.get(k) for k in ("kind", "black_at_sec", "ramp_sec", "black_hold_sec")
        },
        "slow_transitions": len(slow_events),
        "ghost_events": len(ghosts),
        "audio_classes": cuts_doc.get("audio_class_summary", {}).get("seconds_by_class"),
        "on_soloist": None if subject_track is None else subject_track["summary"],
        "files": ["clip.mp4", "cuts.json", "supers.json", *sheets, *strips],
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
        "--a-subjects",
        default=None,
        help="correlate_timeline cuts file for --a: carries its on-soloist track into the pack",
    )
    parser.add_argument(
        "--b-subjects",
        default=None,
        help="correlate_timeline cuts file for --b",
    )
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

    # Both or neither. One side carrying a subject track and the other not is a difference
    # between the labels that has nothing to do with the cuts, and a critic reading one
    # annotated version against one bare one is no longer comparing the two edits.
    subject_files = {"--a-subjects": args.a_subjects, "--b-subjects": args.b_subjects}
    if any(subject_files.values()) and not all(subject_files.values()):
        missing = [flag for flag, given in subject_files.items() if not given]
        sys.exit(
            f"error: {', '.join(missing)} missing. A subject track on one label only is an "
            "asymmetry between the labels, not a measurement -- pass both or neither."
        )
    # Keyed by input flag rather than by source path: two spans of one video are two labels
    # sharing a path, and keying on the path would hand both of them the same track.
    track_of_flag: dict[str, Path | None] = {}
    for flag, given in (("--a", args.a_subjects), ("--b", args.b_subjects)):
        track = None if given is None else Path(given).resolve()
        if track is not None and not track.is_file():
            sys.exit(f"error: subject track not found: {track}")
        track_of_flag[flag] = track

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

    # A label that never matched back to its flag would silently build without its track,
    # which is the asymmetry the both-or-neither check above exists to refuse.
    if all(subject_files.values()) and set(flag_of) != {"A", "B"}:
        sys.exit(
            "error: subject tracks were given, but a label could not be matched back to the "
            "flag it came from, so one label would build without its track. Refusing to build."
        )

    label_entries = []
    for label in ("A", "B"):
        src, span = labels[label]
        print(f"[{label}] building...", file=sys.stderr)
        subjects = track_of_flag.get(flag_of.get(label, ""))
        label_entries.append(build_label(label, src, span, out, args.keep_work, subjects))

    # Refuse to seal a pack whose detector couldn't see the cuts it was told
    # to expect -- a confident manifest built on a blind detector is worse
    # than no manifest at all. Checked before any sealed/summary file is
    # written.
    expect_by_flag = {"--a": args.expect_a, "--b": args.expect_b}
    promised = {flag for flag, count in expect_by_flag.items() if count is not None}
    checked: set[str] = set()
    for entry in label_entries:
        flag = flag_of.get(entry["label"])
        if flag is None:
            continue
        checked.add(flag)
        expected = expect_by_flag[flag]
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

    # A flag that was given an expectation and never matched back to a label is
    # the guard not running, and the guard exists because a pack built on a half
    # blind detector once voided a round's verdict. Skipping quietly there hands
    # back exactly the pack the check was asked to refuse.
    unchecked = promised - checked
    if unchecked:
        sys.exit(
            f"error: {', '.join(sorted(unchecked))} carried an expected cut count, but no "
            "label could be matched back to it, so the count was never checked. Refusing to "
            "write manifest.json."
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
        "pack_version": 4,
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
            "IN = first frames of the new shot (cyan caption); the row's first tile "
            "also carries d=<visual delta>, and JUMP where the 30-degree check flagged "
            "it (d=? means the boundary could not be read -- see visual_delta)",
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
            "blind_spot": f"cannot see a ramp longer than {2 * TRANS_HALF_FRAMES + 1} frames "
            "-- see slow_transition",
        },
        "visual_delta": {
            "question": "how different is the picture after the cut from the picture before "
            "it -- the step the 30-degree rule is about",
            "method": f"four terms on a {framing.GRID_WIDTH}x{framing.GRID_HEIGHT} grey grid, "
            f"median-stacked over {framing.STACK_FRAMES} frames each side of the boundary the "
            "transition pass located (so a dissolve is read across its ramp, not inside it)",
            "terms": {
                "layout": "1 - best normalised correlation of the row/column profiles over "
                f"a lag search of +/-{framing.MAX_SHIFT:.3f} of each axis; a picture that "
                "merely slid sideways still matches itself, and a peak found at the edge of "
                "the search is refused rather than credited",
                "structure": f"1 - correlation of the frame as a {framing.BLOCK_ROWS}x"
                f"{framing.BLOCK_COLS} grid of block means; row/column profiles are marginals "
                "and two pictures with their bright patches at opposite corners share both, so "
                "this is the term marginals cannot fake",
                "content": f"total-variation distance between {framing.HISTOGRAM_BINS}-bin "
                "luma histograms",
                "scale": "change in the spread of the frame's luma mass, in doublings, "
                f"clamped at {framing.SCALE_SPAN}",
            },
            "composite": f"{framing.WEIGHT_LAYOUT}*layout + {framing.WEIGHT_STRUCTURE}*structure "
            f"+ {framing.WEIGHT_CONTENT}*content + {framing.WEIGHT_SCALE}*scale, 0 to 1",
            "jump_cut_flag": f"composite < {framing.JUMP_DELTA}, calibrated so the human "
            "deliverables' own cuts sit clear of it "
            "(gauntlet/recon/cut_delta_calib.json)",
            "reported_fields": "delta, layout, structure, content, scale, shift_x, shift_y, "
            "jump_cut, reason. The whole delta is null where the boundary could not be read: "
            "too close to the window edge, or typed none/unknown, which is a detected cut with "
            "no frame-pair change to read across. shift_x and shift_y are null individually "
            "where that axis found no alignment inside the lag search",
            "blind_spot": "no subject identity -- two cameras on the same soloist with "
            "different backgrounds read as a step, so the flag is a candidate for a human "
            "eye, not a verdict",
        },
        "supers": {
            "question": "which burned-in graphics are on screen when -- lower thirds, "
            "title cards, bugs -- and does any cut land inside one",
            "method": f"each frame of a {SUPER_RATE} fps scan read against the one "
            f"{' and '.join(str(one) for one in SUPER_LAGS_SEC)} s later on a "
            f"{supers.GRID_WIDTH}x{supers.GRID_HEIGHT} grey grid. "
            "A graphic is what two frames of different pictures agree about: the camera saw "
            "something else, the graphics layer drew the same thing. Every span found is then "
            "walked out at native rate so its in and out are frames rather than scan steps",
            "classes": {
                "overlay": f"the composition really changed between the two frames "
                f"(framing delta >= {supers.STEP}) and a compact high-contrast region agrees "
                "across them anyway",
                "card": f"the frames agree over at least {supers.HELD} of the pixels -- the "
                "screen is holding one picture -- and that picture carries such a region, "
                "which is what keeps a black gap from reading as a card. A freeze frame is "
                "held and detailed in the same way and will read as one",
                "unread": "anything else, and deliberately most of it. Pixel agreement alone "
                "cannot prove a graphic on this material: two frames of one still shot "
                "disagree from noise while its brightest static object -- a piano keyboard "
                "with a maker's name across it -- agrees perfectly, in the same place, every "
                "time",
            },
            "believed_only_when_seen_twice": "a span is dropped unless two readings agree on "
            "the same pixels, so a coincidence has to recur exactly to survive",
            "straddle": "a cut with the graphic on screen either side of it. A super arriving "
            "with the new shot (cut at its first frame) or clearing for it (cut one past its "
            "last) is not one -- those are the edits the check exists to tell the fault from",
            "clears_before": "frames between a super's last visible frame and the next cut, "
            "which is the title-card convention as a number: the human's cards read 1",
            "reported_fields": "kind, first, last, ramp_in, ramp_out, visible_first, "
            "visible_last, frames, t, end, clears_before, and the box in frame "
            "fractions (top, left, bottom, right)",
            "blind_spot": "a super has to outlive the lag and to see the picture actually "
            f"change while it is up, so one held under {min(SUPER_LAGS_SEC)} s, or one held "
            "through a single long take, comes back as nothing rather than as absence. "
            "Recall is traded "
            "for precision on purpose: a graphic invented under a critic's cut misleads worse "
            "than one missed",
        },
        "slow_transition": {
            "method": f"mean luma over a {SLOW_W}x{SLOW_H} grey track at {SLOW_FPS} fps; "
            "walk back from the closing black run to the last frame holding picture",
            "ending_window_sec": SLOW_ENDING_SEC,
            "per_shot_window_sec": SLOW_TAIL_SEC,
            "classes": {
                "hard_to_black": f"reaches black in under {SLOW_MIN_RAMP_SEC} s",
                "dissolve_to_black": f"monotone ramp to black over {SLOW_MIN_RAMP_SEC}-"
                f"{SLOW_MAX_RAMP_SEC} s (>= {SLOW_MONOTONE_SHARE} of steps descending)",
                "dim_to_black": "arrives at black but not as one clean ramp",
                "ends_lit": "the window's last frame still holds picture",
                "black_window": "no lit frame in the window at all",
            },
            "reported_fields": "black_at_sec, ramp_start_sec, ramp_sec, black_hold_sec",
            "ghosting": {
                "method": f"mid-shot frames explained as a linear mix of the frames "
                f"+/-{GHOST_HALF_SEC} s away (a double image inside one detected shot); "
                "level-only change is removed before the test, and ramps into black "
                "are left to the ending pass",
                "blend_residual_at_or_below": GHOST_BLEND_RESIDUAL,
                "alpha_band": list(GHOST_ALPHA_BAND),
                "structure_change_at_or_above": GHOST_MIN_CHANGE,
                "hf_dip_ratio": "reported as evidence, not required",
            },
        },
        "audio_class": {
            "method": "1-s windows of mono PCM; spectral flatness, centroid and RMS "
            f"over {AUDIO_BAND[0]:.0f}-{AUDIO_BAND[1]:.0f} Hz",
            "classes": {
                "music": "tonal -- neither of the other two",
                "applause": f"a window at flatness >= {AUDIO_SEED_FLAT} with centroid "
                f">= {AUDIO_SEED_CENTROID:.0f} Hz, grown over neighbours holding "
                f"flatness >= {AUDIO_HOLD_FLAT} and centroid >= {AUDIO_HOLD_CENTROID:.0f} Hz",
                "silence": "within "
                f"{AUDIO_SILENCE_MARGIN_DB} dB of the clip's own room-tone floor, where a "
                f"floor exists (its quietest window {AUDIO_FLOOR_GAP_DB} dB or more under "
                f"its {AUDIO_QUIET_PERCENTILE:.0f}th-percentile level)",
            },
            "order": "applause is decided first: a fading crowd is quiet enough to trip "
            "any level test, and calling that silence is the mistake this pass exists for",
            "smoothing": "a lone window between two identical neighbours takes their class",
            "onsets": "each run carries start_refined -- the loudness edge inside the "
            "window that moved the class",
            "written_to": "cuts.json: audio_class_1s, audio_class_spans, audio_class_summary",
        },
        "subject_track": {
            "carried": all(subject_files.values()),
            "method": "authored, not detected: each label's own correlate_timeline reading of "
            "what its shots are framed on (the angle sidecar's subject) crossed with who was "
            "out front (the solo changes), shifted into the extracted clip's own time",
            "lines": {
                "soloist": "framed on the player out front",
                "ensemble": "framed on the band rather than one player",
                "other_player": "framed on a player who was not soloing -- not `other`, which "
                "is the name of a stem",
                "elsewhere": "framed on something that is neither a player nor the band (an "
                "audience camera, a room shot)",
                "unlabelled": "screen time no sidecar label reaches -- counted apart from the "
                "shares, never in them",
                "black": "a stretch nothing covers -- a fact about the edit rather than about "
                "the labelling, so counted apart from unlabelled as well",
            },
            "how_a_shot_reaches_the_soloist_line": "shots[].subject.on_soloist_by -- "
            "`front_match` where the subject matched the front the solo map measured, "
            "`follow_camera` where the sidecar says that camera follows whoever is out front "
            "and the shot was taken at its word; the summary's "
            "soloist_seconds_by_follow_camera is how much of the share is the second kind",
            "per_detected_shot": "shots[].subject -- the authored shot holding most of it, with "
            "the overlap in seconds, because the pack's boundaries are a scene scan's and the "
            "track's are the timeline's",
            "seconds": "a shot the span cuts through counts the part inside the clip where "
            "the front held through it, and is left out of the share where it did not "
            "(subject_track.shots_left_out_of_the_share counts those); every shot carries its "
            "own counted_seconds",
            "both_or_neither": "one label annotated and the other bare would be a difference "
            "between the labels that is not a difference between the cuts",
            "written_to": "cuts.json: subject_track, and shots[].subject",
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
