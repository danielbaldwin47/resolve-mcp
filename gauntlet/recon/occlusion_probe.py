"""Live proof for analyze_occlusion: does it find the blocked stretches that cost R2 the verdict?

Gap G11. Three shots in the R2 Taurus cut were half-blocked by an audience head, a hat and a
back, and the critics named when: roughly 15-23 s, 33.7-36 s and 38.2-48 s into the piece.
Nothing measured it, so the builder could not have known. This runs the new scan over exactly
that window on the angle the bad shots came from, and over the FX6 for contrast — the wide is
shot from the back of the room over the crowd, so its baseline is the interesting part.

The window is the deliverable's opening 90 s: Zinc SYNC frames 171959-174117. The SYNC record
frame maps into each clip's own numbering through that item's source_in (taurus_grabs.json
verified it live): A7IV source_in 31270 at record 117576, FX6 source_in 0 at the same record.

READ-ONLY: locates clips in the media pool and decodes their files. Writes
gauntlet/recon/occlusion_probe.json. Run it with the project open on the live box.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("occlusion_probe.json")

FPS = 24000.0 / 1001.0
REC_IN = 171959
"""Deliverable span start on Zinc SYNC — the downbeat the Taurus cut opens on."""

WINDOW_FRAMES = 2158
"""90.0 s: the opening the critics watched."""

ANGLES = [
    {
        "label": "A7IV",
        "clip": "20260617_D_A7IV_0006.MP4",
        # Five clips in the pool carry this name; the shoot's own bin is the one on the SYNC.
        "bin": "Zinc Bar/Footage/A7IV/Set 2",
        "record_in": 117576,
        "source_in": 31270,
    },
    {
        "label": "FX6",
        "clip": "A015C001_2606170J.MXF",
        "bin": "Zinc Bar/Footage/FX6/Set 2",
        "record_in": 117576,
        "source_in": 0,
    },
]

CRITIC_SPANS = [(15.0, 23.0), (33.7, 36.0), (38.2, 48.0)]
"""Seconds into the piece the R2 confirmation critic called obstructed."""

ON_SCREEN = {(15.0, 23.0): "FX6", (33.7, 36.0): "A7IV", (38.2, 48.0): "A7IV"}
"""Which angle the R2 cut was actually on for each of those stretches, off taurus_plan_r2.py's
cut list: fx6 14.98-22.72, a7iv 33.84-36.06, a7iv 38.24-48.02. This is the part that decides
whether the scan is right — a window on the angle that was *not* on screen proves nothing."""

VISUAL_CHECK = {
    "occlusion_frames/a7iv_42.5s_flagged.jpg": (
        "Inside the window the scan flagged (41.96-42.92 s, peak 0.604): an out-of-focus "
        "head and shoulder fills the right half of frame and runs off the bottom and right "
        "edges. This is the obstruction the critic saw, and the scan found it."
    ),
    "occlusion_frames/a7iv_35.0s_clear.jpg": (
        "Inside the stretch the scan did NOT flag (33.7-36 s, peak 0.0): the stage is clear "
        "to the lens — sax, piano, bass and kit all visible, nothing in the near field. "
        "Grabs at 34.0 and 36.0 look the same. The miss is worth reading as the critic's "
        "range being wide rather than the scan being blind, but it is unproven either way."
    ),
}
"""Frames grabbed at the peak of a flagged window and inside the missed stretch, kept beside
this receipt: a score nobody looked at is a number, not evidence."""

report: dict[str, Any] = {
    "window": {"sync_in": REC_IN, "sync_out": REC_IN + WINDOW_FRAMES, "fps": FPS},
    "critic_spans": [
        {"start_s": one, "end_s": two, "on_screen": ON_SCREEN[(one, two)]}
        for one, two in CRITIC_SPANS
    ],
    "visual_check": VISUAL_CHECK,
    "angles": {},
    "errors": [],
}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def note(where: str, exc: BaseException) -> None:
    report["errors"].append(
        {
            "where": where,
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=6),
        }
    )
    write()


def overlap(span: tuple[float, float], window: tuple[float, float]) -> float:
    return max(0.0, min(span[1], window[1]) - max(span[0], window[0]))


def _verdict(
    start: float,
    end: float,
    windows: list[dict[str, Any]],
    samples: list[dict[str, Any]],
) -> dict[str, Any]:
    """How much of one critic-named stretch the scan flagged, and how hard it scored inside it.

    The peak matters as much as the flag: a stretch the scan scored 0.30 against a 0.35
    threshold is a measurement that saw the head and was talked out of it, which is a
    different problem from one that saw nothing.
    """
    flagged = sum(overlap((start, end), (one["start_s"], one["end_s"])) for one in windows)
    inside = [one["score"] for one in samples if start <= one["at_s"] < end]
    return {
        "critic_span": {"start_s": start, "end_s": end},
        "on_screen": ON_SCREEN[(start, end)],
        "seconds_flagged": round(flagged, 2),
        "fraction_flagged": round(flagged / (end - start), 2),
        "peak_score_inside": max(inside) if inside else None,
        "mean_score_inside": round(sum(inside) / len(inside), 3) if inside else None,
    }


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.jobs import lifecycle, runner
    from resolve_mcp.tools import video as video_tools

    for angle in ANGLES:
        label = str(angle["label"])
        first = int(angle["source_in"]) + (REC_IN - int(angle["record_in"]))
        last = first + WINDOW_FRAMES
        entry: dict[str, Any] = {
            "clip": angle["clip"],
            "bin": angle["bin"],
            "source_range": {"in": first, "out": last},
        }
        report["angles"][label] = entry
        write()

        began = time.time()
        try:
            envelope = video_tools.analyze_occlusion(
                str(angle["clip"]),
                bin=str(angle["bin"]),
                start=first,
                end=last,
            )
        except Exception as exc:  # noqa: BLE001 - a probe records the failure rather than dying
            note(f"start {label}", exc)
            continue
        entry["ok"] = envelope.get("ok")
        if not envelope.get("ok"):
            entry["error"] = envelope.get("error")
            write()
            continue

        job_id = envelope["job_id"]
        entry["job_id"] = job_id
        got = runner.wait_for(job_id, timeout=60.0)
        while got.state == lifecycle.RUNNING:
            entry["step"] = got.step
            write()
            got = runner.wait_for(job_id, timeout=60.0)
        payload = got.payload()
        entry["state"] = payload.get("state")
        entry["elapsed_s"] = round(time.time() - began, 1)
        entry["error"] = payload.get("error")
        result = payload.get("result") or {}
        entry["result"] = result
        write()
        if not result:
            continue

        # Every window as seconds into the piece, which is the only frame of reference the
        # critics used and the only one a builder cutting this song thinks in.
        catalog = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        windows = [
            {
                "start_s": round((one["in"]["frames"] - first) / FPS, 2),
                "end_s": round((one["out"]["frames"] - first) / FPS, 2),
                "peak_score": one["peak_score"],
                "mean_score": one["mean_score"],
            }
            for one in catalog["windows"]
        ]
        entry["windows_in_piece"] = windows
        entry["samples_in_piece"] = [
            {
                "at_s": round((one["time"]["frames"] - first) / FPS, 2),
                "score": one["score"],
                "coverage": one["coverage"],
            }
            for one in catalog["samples"]
        ]
        entry["baseline"] = catalog["baseline"]
        entry["curve"] = [[one["at_s"], one["score"]] for one in entry["samples_in_piece"]]
        entry["verdict"] = [
            _verdict(one, two, windows, entry["samples_in_piece"]) for one, two in CRITIC_SPANS
        ]
        write()
        print(label, "windows:", json.dumps(windows), flush=True)
        print(label, "verdict:", json.dumps(entry["verdict"]), flush=True)

    # The answer the gap asks for: for each stretch the critic named, what the scan said about
    # the angle that was actually on screen there.
    report["summary"] = [
        one
        for label, entry in report["angles"].items()
        for one in entry.get("verdict", [])
        if one["on_screen"] == label
    ]
    write()
    print("summary:", json.dumps(report["summary"]), flush=True)
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
