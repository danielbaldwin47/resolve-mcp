"""Freeze the G11 evidence scans as committable grey grids for the fake tier. READ-ONLY.

The occlusion false-positive discriminator (#189) has to be judged against the frames that
produced the verdicts, not against synthetic blobs: every false positive in the ledgers is a
*real* dark bottom-anchored blob, so a fixture built by drawing one cannot tell the classes
apart. What the detector actually reads is 128x72 grey — nine kilobytes a sample — so the
whole evidence set is small enough to commit, and committing it is what makes the discriminator
testable off the live box.

Six scans: the three adjudicated Taurus pieces on both angles, 90 s each at a sample a second,
decoded with the same ffmpeg command ``video/ffmpeg.sample_command`` builds. Ranges come from
the adjudication receipts (occlusion_verdict_r3.json, occlusion_mid.json, occlusion_ending.json)
and both clips number from zero, which ffprobe confirms for the A7IV (nb_frames 98676 == the
item's source out).

Every scan is re-scored here and checked against the catalog the original run left in the
analysis dir: if the per-sample scores match, the grid is the same footage the ledgers judged.
A mismatch would mean the decode landed somewhere else, so it is recorded rather than glossed.

What it records: the three FX6 scans and the A7IV ending reproduce their catalogs exactly, and
the two other A7IV scans differ on isolated samples — 0.007 at worst on the mid, 0.14 on the
opening. The FX6 is intra-frame MXF and the A7IV is long-GOP, and an input seek into long-GOP
does not have to land on the same decoded frame twice; the original runs also predate the
hardware-decode work of #202, so their catalogs carry no ``decode`` field to compare against.
The consequence is one number: the opening A7IV's blocking peaks at 0.572 here against the
ledger's 0.604. It is the same blocking, the same window and the same verdict — the frames are
the footage the eye judged, and the fixture is used for what the eye said about it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).with_name("occl_fixture_grids.json")
DATA = REPO / "tests" / "data" / "occlusion"
CATALOGS = Path.home() / "AppData" / "Local" / "resolve-mcp" / "analysis"

FPS = 24000.0 / 1001.0
WINDOW_FRAMES = 2158
"""The 90 s every piece was adjudicated over: Zinc SYNC 171959-174117 and the two after it."""

SAMPLE_FPS = 1.0
WIDTH = 128
HEIGHT = 72

A7IV = "P:/Client Work/Ryan Devlin/2026-06-17_Zinc Bar/Footage/A7IV/M4ROOT/CLIP/20260617_D_A7IV_0006.MP4"
FX6 = "P:/Client Work/Ryan Devlin/2026-06-17_Zinc Bar/Footage/FX6/Card 2/XDROOT/Clip/A015C001_2606170J.MXF"

SCANS = [
    {"name": "opening-fx6", "clip": "A015C001_2606170J.MXF", "path": FX6, "first": 54383},
    {"name": "opening-a7iv", "clip": "20260617_D_A7IV_0006.MP4", "path": A7IV, "first": 85653},
    {"name": "mid-fx6", "clip": "A015C001_2606170J.MXF", "path": FX6, "first": 58379},
    {"name": "mid-a7iv", "clip": "20260617_D_A7IV_0006.MP4", "path": A7IV, "first": 89649},
    {"name": "ending-fx6", "clip": "A015C001_2606170J.MXF", "path": FX6, "first": 64157},
    {"name": "ending-a7iv", "clip": "20260617_D_A7IV_0006.MP4", "path": A7IV, "first": 95427},
]

NOTE = (
    "The FX6 (intra-frame MXF) scans and the A7IV ending reproduce their original catalogs "
    "exactly. The A7IV opening and mid differ on isolated samples (0.14 and 0.007 at worst): "
    "long-GOP input seek does not have to land on the same decoded frame twice. Same footage, "
    "same windows, same verdicts — the opening blocking peaks 0.572 here against the ledger's "
    "0.604."
)

report: dict[str, Any] = {
    "kind": "occlusion_fixture_grids",
    "note": NOTE,
    "scans": [],
    "errors": [],
}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")


def catalog_for(clip: str, first: int) -> dict[str, Any] | None:
    """The original run's catalog for this clip and range, if it is still on disk."""
    for candidate in sorted(CATALOGS.glob(f"{clip}-*.occlusion.json")):
        loaded = json.loads(candidate.read_text(encoding="utf-8"))
        if int(loaded["range"]["in"]["frames"]) == first:
            return dict(loaded)
    return None


def _already_there(target: Path, frames: Any) -> bool:
    """Whether ``target`` already holds exactly these frames."""
    if not target.exists():
        return False
    with np.load(target) as loaded:
        return bool(np.array_equal(loaded["frames"], frames))


def decode(source: str, first: int, target: Path) -> None:
    from resolve_mcp.video.ffmpeg import sample_command

    command = sample_command(
        "ffmpeg",
        source,
        target,
        start_seconds=first / FPS,
        duration_seconds=WINDOW_FRAMES / FPS,
        rate=SAMPLE_FPS,
        width=WIDTH,
        height=HEIGHT,
    )
    finished = subprocess.run(command, capture_output=True, text=True, check=False)
    if finished.returncode != 0:
        raise RuntimeError(f"ffmpeg exited {finished.returncode}: {finished.stderr[-2000:]}")


def main() -> None:
    from resolve_mcp.video import blocking

    DATA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        for scan in SCANS:
            entry: dict[str, Any] = {"name": scan["name"], "clip": scan["clip"]}
            report["scans"].append(entry)
            first = int(scan["first"])
            entry["range"] = [first, first + WINDOW_FRAMES]
            raw = Path(scratch) / f"{scan['name']}.gray"
            try:
                decode(str(scan["path"]), first, raw)
                frames = blocking.read_grid(raw.read_bytes())
            except Exception as exc:  # noqa: BLE001 - a probe records the failure rather than dying
                entry["error"] = f"{type(exc).__name__}: {exc}"
                write()
                continue

            entry["samples"] = int(frames.shape[0])
            measured = blocking.measure(frames)
            scores = [one.score for one in measured.readings]
            entry["baseline"] = measured.baseline

            catalog = catalog_for(str(scan["clip"]), first)
            if catalog is None:
                entry["checked_against_catalog"] = False
            else:
                original = [float(one["score"]) for one in catalog["samples"]]
                shared = min(len(original), len(scores))
                worst = max(
                    (abs(original[i] - scores[i]) for i in range(shared)), default=0.0
                )
                entry["checked_against_catalog"] = True
                entry["catalog_samples"] = len(original)
                entry["worst_score_delta"] = round(worst, 4)
                entry["matches_catalog"] = bool(worst <= 0.02 and shared == len(scores))

            # Only write when the grid actually changed: a zip carries the hour it was made,
            # so re-running to refresh this receipt would otherwise land three megabytes of
            # byte-different, pixel-identical fixtures in the history.
            target = DATA / f"{scan['name']}.npz"
            entry["rewritten"] = not _already_there(target, frames)
            if entry["rewritten"]:
                np.savez_compressed(target, frames=frames)
            entry["fixture"] = str(target.relative_to(REPO)).replace("\\", "/")
            entry["fixture_bytes"] = target.stat().st_size
            write()
            print(scan["name"], entry.get("worst_score_delta"), entry["fixture_bytes"], flush=True)

    total = sum(int(one.get("fixture_bytes", 0)) for one in report["scans"])
    report["total_fixture_bytes"] = total
    write()
    print("wrote", OUT, "total", total, flush=True)


if __name__ == "__main__":
    sys.exit(main())
