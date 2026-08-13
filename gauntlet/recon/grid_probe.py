r"""Which audio path makes the beat-grid cache hit — the P: master or the acquired copy.

The first analysis run failed at "reading the beat grid" with beat_this missing, even though a
beats file for this concert is already on disk: the grid was computed against the master the
director handed over (P:\...\Zinc Set 2 Reaper v4.wav) and the run asked for it under the
acquired copy in AppData, which is a different identity (jobs/cache.py:57 — hash what we wrote,
fingerprint what they handed over). This asks the cache directly rather than guessing: it
rebuilds the exact key music.beats_of uses and looks it up. No detector is ever constructed, so
this is safe on a machine with no beat model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CANDIDATES = [
    Path(r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"),
    Path(
        r"C:\Users\Daniel\AppData\Local\resolve-mcp\audio"
        r"\Zinc-Set-2-Reaper-v4.wav-8833f33949fe.wav"
    ),
]
OUT = Path(__file__).with_name("grid_probe.json")


def main() -> None:
    from resolve_mcp.analysis import halves, music
    from resolve_mcp.config import get_config
    from resolve_mcp.jobs import cache

    config = get_config()
    report: dict[str, Any] = {"result_dir": str(config.result_dir), "candidates": []}
    for path in CANDIDATES:
        row: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
        if path.is_file():
            identity = halves.identity(path, config)
            row["identity"] = identity
            key = cache.cache_key(f"{music.KIND}:{music.BEATS}", [identity], {})
            row["beats_key"] = key
            hit = cache.lookup(key, config)
            row["beats_cached"] = hit is not None
            row["beats_path"] = (hit or {}).get("path")
        report["candidates"].append(row)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
