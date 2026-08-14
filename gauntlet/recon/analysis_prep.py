"""Session-start analysis prep for the Zinc concert mix (READ-ONLY: no Resolve calls).

Runs the fixed suite from docs/agents/concert.md step 1-2 against the master-mix file
directly, so nothing touches the open project or the current timeline. Jobs are in-process
daemon threads (jobs/runner.py), so this process stays alive and joins each one.

Writes gauntlet/recon/analysis_prep.json.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
OUT = Path(__file__).with_name("analysis_prep.json")

report: dict[str, Any] = {"mix": MIX, "jobs": {}, "errors": []}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def note(where: str, exc: BaseException) -> None:
    report["errors"].append(
        {"where": where, "type": type(exc).__name__, "message": str(exc),
         "traceback": traceback.format_exc(limit=6)}
    )
    write()


def run(label: str, start, **kwargs: Any) -> dict[str, Any] | None:
    from resolve_mcp.jobs import runner, store

    began = time.time()
    try:
        record = start(**kwargs)
    except Exception as exc:  # noqa: BLE001
        note(f"start {label}", exc)
        return None
    job_id = record.get("job_id")
    report["jobs"][label] = {"job_id": job_id, "state": record.get("state"),
                             "cached": record.get("cached")}
    write()
    while True:
        got = runner.wait_for(job_id, timeout=30.0)
        if got.state != store.RUNNING:
            break
        report["jobs"][label]["step"] = got.step
        report["jobs"][label]["progress"] = got.progress
        report["jobs"][label]["elapsed_s"] = round(time.time() - began, 1)
        write()
    payload = got.payload()
    report["jobs"][label] = {
        "job_id": job_id,
        "state": payload.get("state"),
        "cached": payload.get("cached"),
        "elapsed_s": round(time.time() - began, 1),
        "result": payload.get("result"),
        "error": payload.get("error"),
    }
    write()
    return payload


def main() -> None:
    from resolve_mcp.analysis import music, structure

    if not Path(MIX).exists():
        report["errors"].append({"where": "mix", "message": "master mix not on disk"})
        write()
        return

    run("analyze_music", music.analyze_music, audio=MIX, beats=True, energy=True)
    run("analyze_structure_tunes", structure.analyze_structure, audio=MIX, tunes=True,
        solos=False)
    report["done"] = True
    write()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        note("main", exc)
    print("DONE", OUT)
