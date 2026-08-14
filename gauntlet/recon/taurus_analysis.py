"""Run the three event analyses over the Zinc master mix, one long-lived process.

Jobs run in daemon threads inside the calling process (jobs/runner.start_job), so the process
has to outlive them: this starts all three and polls the records until each closes.

* analyze_structure(tunes+solos) — front changes off the stems
* detect_drum_fills — fill candidates off the drum pass
* detect_phrases — phrase boundaries off the melodic stem

The melodic stem is `wind` if the opt-in third pass landed (gauntlet/recon/wind_pass.py), else
`other`. Phrases takes an explicit {name: path} mapping because its own directory reader only
looks in the `mix` pass and would never see the wind file.

READ-ONLY on Resolve: never connects.
"""

from __future__ import annotations

import json
import time
import traceback
from pathlib import Path
from typing import Any

STEMS_DIR = Path(
    r"C:\Users\Daniel\AppData\Local\resolve-mcp\stems"
    r"\Zinc-Set-2-Reaper-v4.wav-8833f33949fe-d73a1e4c156e"
)
AUDIO = Path(
    r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
)
"""The master the director handed over, NOT the acquired copy in AppData.

The beat grid every one of these three reads is cached against this path's identity
(gauntlet/recon/grid_probe.py measured it: hit on the master, miss on the copy — jobs/cache.py
hashes what the server wrote and fingerprints what the director handed over). Asking under the
copy re-runs the beat model, and beat_this is not installed in this worktree, so the copy turns
a cache hit into a hard dependency failure.
"""
WIND_REPORT = Path(__file__).with_name("wind_pass.json")
OUT = Path(__file__).with_name("taurus_analysis.json")

WIND_DEADLINE = 12 * 60.0
POLL = 5.0

report: dict[str, Any] = {"state": "starting", "jobs": {}}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def wind_state() -> dict[str, Any]:
    if not WIND_REPORT.is_file():
        return {"state": "absent"}
    try:
        return dict(json.loads(WIND_REPORT.read_text(encoding="utf-8")))
    except json.JSONDecodeError:
        return {"state": "unreadable"}


def main() -> None:
    from resolve_mcp.analysis import fills, phrases, structure
    from resolve_mcp.audio import separator
    from resolve_mcp.audio import stems as stems_module
    from resolve_mcp.config import get_config
    from resolve_mcp.jobs import store

    config = get_config()

    # 1. Wait for the third pass, but only so long — the reads below work either way.
    deadline = time.time() + WIND_DEADLINE
    wind = wind_state()
    while wind.get("state") not in {"completed", "failed", "absent"} and time.time() < deadline:
        time.sleep(POLL)
        wind = wind_state()
    report["wind_pass"] = wind

    other_dir = STEMS_DIR / stems_module.OTHER_PASS
    wind_files = separator.collect(other_dir) if other_dir.is_dir() else {}
    keys = stems_module.WIND_KEYS
    named = {keys[label]: path for label, path in wind_files.items() if label in keys}
    has_wind = len(named) == len(stems_module.WIND_KEYS)
    melodic = stems_module.WIND if has_wind else stems_module.OTHER_SOURCE
    report["third_pass_landed"] = has_wind
    report["melodic_stem"] = melodic
    write()

    mix = separator.collect(STEMS_DIR / stems_module.MIX_PASS)
    residual = stems_module.OTHER_SOURCE
    melodic_paths = dict(named) if has_wind else {residual: mix[residual]}
    report["melodic_path"] = str(melodic_paths[melodic])

    # 2. Start all three. They are independent and each is a thread of its own.
    started: dict[str, str] = {}

    # tunes=False deliberately: the tune boundaries for this concert are already settled in
    # gauntlet/recon/songs_map.json, and the applause half needs panns_inference, which is not
    # installed here. Asking for it would fail the whole job for a half nothing here reads.
    record = structure.analyze_structure(
        AUDIO, tunes=False, solos=True, stems=STEMS_DIR, config=config
    )
    started["analyze_structure"] = str(record["job_id"])
    report["jobs"]["analyze_structure"] = record
    write()

    record = fills.detect_drum_fills(STEMS_DIR, AUDIO, config=config)
    started["detect_drum_fills"] = str(record["job_id"])
    report["jobs"]["detect_drum_fills"] = record
    write()

    record = phrases.detect_phrases(melodic_paths, AUDIO, stem=melodic, config=config)
    started["detect_phrases"] = str(record["job_id"])
    report["jobs"]["detect_phrases"] = record
    write()

    report["state"] = "running"
    write()

    # 3. Poll until every one closes.
    pending = dict(started)
    began = time.time()
    while pending:
        time.sleep(POLL)
        for name, job_id in list(pending.items()):
            loaded = store.load(job_id, config)
            report["jobs"][name] = loaded.payload()
            if loaded.state != store.RUNNING:
                del pending[name]
        report["elapsed_seconds"] = round(time.time() - began, 1)
        write()

    report["state"] = "done"
    report["states"] = {
        name: report["jobs"][name].get("state") for name in started
    }
    write()


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001 - the record is the only reporting channel
        report.update(
            {
                "state": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(),
            }
        )
        write()
        raise
