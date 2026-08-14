"""#179 end to end: run the real `analyze_structure` job on the board mix, defaults only.

The sweeps score the arithmetic; this proves the wiring — the tagger, the scaled reading,
the loudness curve read back from `analyze_music`'s cache, the settle step, the file on
disk. No hand-set numbers: whatever the job's defaults are is what this reports. Writes
gauntlet/recon/board_tunes_job.json.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
OUT = Path(__file__).with_name("board_tunes_job.json")
TRUTH = [107.4405, 1373.8725, 1920.1265, 2725.014, 3568.4815]
TOLERANCE = 5.0


def main() -> None:
    from resolve_mcp.analysis import structure
    from resolve_mcp.jobs import runner, store

    began = time.time()
    # refresh, always: the half is keyed on the audio and the settings, neither of which
    # changes when the rules do, so a cached answer would be the old code's and this file
    # is meant to be evidence about this one.
    record = structure.analyze_structure(audio=MIX, tunes=True, solos=False, refresh=True)
    job_id = record["job_id"]
    while True:
        got = runner.wait_for(job_id, timeout=60.0)
        if got.state != store.RUNNING:
            break
    payload = got.payload()
    report: dict[str, Any] = {
        "mix": MIX,
        "job_id": job_id,
        "state": payload.get("state"),
        "elapsed_s": round(time.time() - began, 1),
        "error": payload.get("error"),
        "human_cut_starts_s": TRUTH,
    }
    gist = (payload.get("result") or {}).get("tunes") or {}
    report["gist"] = {key: value for key, value in gist.items() if key != "path"}
    path = gist.get("path")
    if path and Path(path).exists():
        body = json.loads(Path(path).read_text(encoding="utf-8"))
        report["path"] = path
        starts = [round(row["t"], 3) for row in body.get("tunes", [])]
        report["boundaries_s"] = starts
        report["talk_seconds"] = [row.get("talk_seconds") for row in body.get("tunes", [])]
        errors = [min((abs(one - want) for one in starts), default=1e9) for want in TRUTH]
        report["errors_s"] = [round(one, 3) for one in errors]
        report["within_tolerance"] = sum(1 for one in errors if one <= TOLERANCE)
        report["quiet_calls"] = body.get("quiet_calls")
        report["dropped_calls"] = body.get("dropped_calls")
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("DONE", OUT, report.get("within_tolerance"), "of", len(TRUTH))


if __name__ == "__main__":
    main()
