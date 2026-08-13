"""Measure the opening/ending convention across the 5 Zinc 6-17 set-2 deliverables.

Per song:
  * contact sheet of the first 8 s at 0.5 s intervals (320 px wide tiles)
  * contact sheet of the last 8 s at 0.5 s intervals
  * 0.1 s-window RMS curve over the first 15 s and the last 15 s

Frames are viewed by the agent; this script only produces them.
Dependencies: stdlib + ffmpeg/ffprobe on PATH.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

SRC = Path("S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos")
OUT = Path(__file__).resolve().parent / "openings_frames"
RESULT = Path(__file__).resolve().parent / "openings_survey.json"

SONGS = {
    "hardest_part": "6-17 - Zinc Set 2 - Hardest Part.mp4",
    "maitland_boulevard": "6-17 - Zinc Set 2 - Maitland Boulevard.mp4",
    "sambra": "6-17 - Zinc Set 2 - Sambra.mp4",
    "soultrane": "6-17 - Zinc Set 2 - Soultrane.mp4",
    "taurus_people": "6-17 - Zinc Set 2 - Taurus People.mp4",
}

RMS_RE = re.compile(
    r"pts_time:([0-9]+\.?[0-9]*)\s*\n[^\n]*RMS_level=(-?[0-9]+\.?[0-9]*|-inf)"
)


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe(clip: Path) -> dict[str, float]:
    proc = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(clip),
        ]
    )
    doc = json.loads(proc.stdout)
    vid = next(s for s in doc["streams"] if s["codec_type"] == "video")
    num, den = vid["r_frame_rate"].split("/")
    return {
        "duration_s": round(float(doc["format"]["duration"]), 3),
        "fps": round(float(num) / float(den), 3),
        "width": vid["width"],
        "height": vid["height"],
    }


def sheet(
    clip: Path,
    start: float,
    dest: Path,
    cols: int = 6,
    rows: int = 3,
    dur: float = 8.0,
    fps: str = "2",
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += [
        "-i",
        str(clip),
        "-t",
        f"{dur:.3f}",
        "-vf",
        f"fps={fps},scale=320:-2,tile={cols}x{rows}:margin=4:padding=4:color=0x202020",
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(dest),
    ]
    proc = run(cmd)
    if not dest.exists():
        print(f"SHEET FAIL {dest}: {proc.stderr[-800:]}", file=sys.stderr)


def strip(clip: Path, times: list[float], destdir: Path) -> None:
    """One frame per named time, for refining a transition found on the sheet."""
    destdir.mkdir(parents=True, exist_ok=True)
    for t in times:
        dest = destdir / f"t{t:07.3f}.jpg"
        run(
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
                "scale=320:-2",
                "-q:v",
                "3",
                str(dest),
            ]
        )


def rms_window(clip: Path, start: float, dur: float, win: float = 0.1) -> list[dict[str, float]]:
    samples = int(8000 * win)
    cmd = ["ffmpeg", "-hide_banner", "-nostats"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += [
        "-i",
        str(clip),
        "-t",
        f"{dur:.3f}",
        "-vn",
        "-af",
        f"aresample=8000,asetnsamples={samples},astats=metadata=1:reset=1,"
        "ametadata=print:key=lavfi.astats.Overall.RMS_level",
        "-f",
        "null",
        "-",
    ]
    proc = run(cmd)
    blob = proc.stdout + "\n" + proc.stderr
    points: list[dict[str, float]] = []
    for t_str, rms_str in RMS_RE.findall(blob):
        rms = -100.0 if rms_str == "-inf" else float(rms_str)
        points.append({"t": round(start + float(t_str), 3), "rms_db": round(rms, 2)})
    return points


def first_rise(curve: list[dict[str, float]], floor_pad: float = 12.0) -> dict[str, float] | None:
    """First window whose RMS rises floor_pad dB above the opening floor and stays up."""
    if len(curve) < 10:
        return None
    head = sorted(p["rms_db"] for p in curve[:10])
    floor = head[len(head) // 2]
    thresh = floor + floor_pad
    for i, p in enumerate(curve):
        if p["rms_db"] >= thresh and all(q["rms_db"] >= thresh - 6 for q in curve[i : i + 5]):
            return {
                "t": p["t"],
                "rms_db": p["rms_db"],
                "floor_db": floor,
                "thresh_db": round(thresh, 2),
            }
    return None


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "sheets"
    doc: dict[str, object] = {}
    if RESULT.exists():
        doc = json.loads(RESULT.read_text(encoding="utf-8"))

    if mode == "sheets":
        songs: dict[str, object] = {}
        for key, name in SONGS.items():
            clip = SRC / name
            info = probe(clip)
            d = info["duration_s"]
            sheet(clip, 0.0, OUT / f"{key}_head.jpg")
            sheet(clip, max(0.0, d - 8.0), OUT / f"{key}_tail.jpg")
            head_rms = rms_window(clip, 0.0, 15.0)
            tail_rms = rms_window(clip, max(0.0, d - 15.0), 15.0)
            songs[key] = {
                "file": name,
                "probe": info,
                "head_sheet": str((OUT / f"{key}_head.jpg").as_posix()),
                "tail_sheet": str((OUT / f"{key}_tail.jpg").as_posix()),
                "head_rms_0p1s": head_rms,
                "tail_rms_0p1s": tail_rms,
                "head_first_rise": first_rise(head_rms),
            }
            print(f"{key}: dur={d} fps={info['fps']} rise={songs[key]['head_first_rise']}")
        doc["songs"] = songs
        RESULT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        return

    if mode == "long":
        # 0-36 s at 1 s, one 6x6 sheet per song: locates the super's in and out.
        for key, name in SONGS.items():
            clip = SRC / name
            sheet(clip, 0.0, OUT / f"{key}_head36.jpg", cols=6, rows=6, dur=36.0, fps="1")
            print(f"{key}: head36 done")
        return

    if mode == "luma":
        # per-frame average luma over the first 12 s: exact black / fade-up shape.
        YAVG_RE = re.compile(
            r"pts_time:([0-9]+\.?[0-9]*)\s*\n[^\n]*YAVG=(-?[0-9]+\.?[0-9]*)"
        )
        songs = doc.setdefault("songs", {})  # type: ignore[union-attr]
        for key, name in SONGS.items():
            clip = SRC / name
            proc = run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(clip),
                    "-t",
                    "12",
                    "-an",
                    "-vf",
                    "scale=160:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                    "-f",
                    "null",
                    "-",
                ]
            )
            blob = proc.stdout + "\n" + proc.stderr
            pts = [
                {"t": round(float(a), 3), "yavg": round(float(b), 2)}
                for a, b in YAVG_RE.findall(blob)
            ]
            songs[key]["head_yavg"] = pts  # type: ignore[index]
            lo = [p for p in pts if p["yavg"] < 2.0]
            print(f"{key}: n={len(pts)} first5={pts[:5]} sub2_until={lo[-1]['t'] if lo else None}")
        RESULT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        return

    if mode == "text":
        # Near-white pixel share inside the lower-third band. A super is white
        # serif type on a dark stage floor, so the share plateaus while it is up
        # and the fades show as the ramps either side.
        TXT_RE = re.compile(r"pts_time:([0-9]+\.?[0-9]*)\s*\n[^\n]*YAVG=(-?[0-9]+\.?[0-9]*)")
        songs = doc.setdefault("songs", {})  # type: ignore[union-attr]
        for key, name in SONGS.items():
            clip = SRC / name
            proc = run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(clip),
                    "-t",
                    "60",
                    "-an",
                    "-vf",
                    "crop=iw*0.55:ih*0.14:iw*0.03:ih*0.76,scale=480:-2,format=gray,"
                    "lutyuv=y='if(gt(val,200),255,0)',signalstats,"
                    "metadata=print:key=lavfi.signalstats.YAVG",
                    "-f",
                    "null",
                    "-",
                ]
            )
            blob = proc.stdout + "\n" + proc.stderr
            pts = [
                {"t": round(float(a), 3), "white_pct": round(float(b) / 2.55, 3)}
                for a, b in TXT_RE.findall(blob)
            ]
            songs[key]["band_white_pct_60s"] = pts  # type: ignore[index]
            vals = sorted(p["white_pct"] for p in pts)
            base = vals[len(vals) // 2]
            thresh = max(base + 0.30, 0.40)
            hi = [p for p in pts if p["white_pct"] >= thresh]
            spans: list[list[float]] = []
            for p in hi:
                if spans and p["t"] - spans[-1][1] < 0.8:
                    spans[-1][1] = p["t"]
                else:
                    spans.append([p["t"], p["t"]])
            spans = [s for s in spans if s[1] - s[0] >= 1.0]
            # widen each full-strength span out along its fade ramps
            edge = max(base + 0.05, 0.08)
            ramped: list[dict[str, float]] = []
            for lo, hi_t in spans:
                i0 = next(i for i, p in enumerate(pts) if p["t"] >= lo)
                i1 = max(i for i, p in enumerate(pts) if p["t"] <= hi_t)
                a = i0
                while a > 0 and pts[a - 1]["white_pct"] >= edge:
                    a -= 1
                b = i1
                while b < len(pts) - 1 and pts[b + 1]["white_pct"] >= edge:
                    b += 1
                ramped.append(
                    {
                        "fade_in_start": pts[a]["t"],
                        "full_in": lo,
                        "full_out": hi_t,
                        "fade_out_end": pts[b]["t"],
                    }
                )
            songs[key]["super_spans_full"] = spans  # type: ignore[index]
            songs[key]["supers"] = ramped  # type: ignore[index]
            songs[key]["super_threshold_pct"] = round(thresh, 3)  # type: ignore[index]
            print(f"{key}: base={base} spans={spans} ramped={ramped}")
        RESULT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        return

    if mode == "bandsheet":
        # Strip the lower-third text band only, 0.5 s apart, 80 tiles = 0-40 s.
        # Presence/absence of the super is read off the strip directly.
        for key, name in SONGS.items():
            clip = SRC / name
            dest = OUT / f"{key}_band.jpg"
            dest.parent.mkdir(parents=True, exist_ok=True)
            proc = run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-y",
                    "-i",
                    str(clip),
                    "-t",
                    "40",
                    "-an",
                    "-vf",
                    "crop=iw*0.55:ih*0.14:iw*0.03:ih*0.76,fps=2,scale=480:-2,eq=brightness=0.06,"
                    "tile=4x20:margin=3:padding=3:color=0x303030",
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    str(dest),
                ]
            )
            print(f"{key}: band sheet {'ok' if dest.exists() else proc.stderr[-400:]}")
        return

    if mode == "band":
        # Lower-third band luma over the first 40 s: the supers are white serif
        # text on a dark stage floor, so YMAX in the band pins when text is up.
        BAND_RE = re.compile(r"pts_time:([0-9]+\.?[0-9]*)\s*\n[^\n]*YMAX=([0-9]+\.?[0-9]*)")
        songs = doc.setdefault("songs", {})  # type: ignore[union-attr]
        for key, name in SONGS.items():
            clip = SRC / name
            proc = run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(clip),
                    "-t",
                    "40",
                    "-an",
                    "-vf",
                    "crop=iw*0.60:ih*0.16:iw*0.03:ih*0.76,scale=320:-2,signalstats,"
                    "metadata=print:key=lavfi.signalstats.YMAX",
                    "-f",
                    "null",
                    "-",
                ]
            )
            blob = proc.stdout + "\n" + proc.stderr
            pts = [
                {"t": round(float(a), 3), "ymax": float(b)} for a, b in BAND_RE.findall(blob)
            ]
            songs[key]["band_ymax_40s"] = pts  # type: ignore[index]
            hi = [p for p in pts if p["ymax"] >= 600]
            spans: list[tuple[float, float]] = []
            for p in hi:
                if spans and p["t"] - spans[-1][1] < 0.5:
                    spans[-1] = (spans[-1][0], p["t"])
                else:
                    spans.append((p["t"], p["t"]))
            spans = [s for s in spans if s[1] - s[0] > 0.5]
            songs[key]["band_hi_spans"] = spans  # type: ignore[index]
            print(f"{key}: n={len(pts)} spans={spans}")
        RESULT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        return

    if mode == "tailluma":
        YAVG_RE = re.compile(
            r"pts_time:([0-9]+\.?[0-9]*)\s*\n[^\n]*YAVG=(-?[0-9]+\.?[0-9]*)"
        )
        songs = doc.setdefault("songs", {})  # type: ignore[union-attr]
        for key, name in SONGS.items():
            clip = SRC / name
            d = float(songs[key]["probe"]["duration_s"])  # type: ignore[index]
            start = max(0.0, d - 15.0)
            proc = run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    str(clip),
                    "-t",
                    "15",
                    "-an",
                    "-vf",
                    "scale=160:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
                    "-f",
                    "null",
                    "-",
                ]
            )
            blob = proc.stdout + "\n" + proc.stderr
            pts = [
                {"t": round(start + float(a), 3), "yavg": round(float(b), 2)}
                for a, b in YAVG_RE.findall(blob)
            ]
            songs[key]["tail_yavg"] = pts  # type: ignore[index]
            tail_lo = [p for p in pts if p["yavg"] < 2.0]
            print(
                f"{key}: dur={d} last={pts[-3:]} sub2_from={tail_lo[0]['t'] if tail_lo else None}"
            )
        RESULT.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        return

    if mode == "refine":
        # argv: refine <song_key> <t_start> <t_end> <step>
        key = sys.argv[2]
        t0, t1, step = float(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5])
        clip = SRC / SONGS[key]
        times = []
        t = t0
        while t <= t1 + 1e-9:
            times.append(round(t, 3))
            t += step
        strip(clip, times, OUT / f"{key}_refine")
        print(f"{key}: {len(times)} frames -> {OUT / f'{key}_refine'}")
        return

    raise SystemExit(f"unknown mode {mode}")


if __name__ == "__main__":
    main()
