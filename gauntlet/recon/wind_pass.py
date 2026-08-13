"""Run ONLY the opt-in third pass (other -> wind/comp) into the existing stems directory.

Why not `separate_stems(split_wind=True)`: the flag is not a stems-key param, so a directory
missing the third pass reads as *partial*, and partial is redone whole (stems.py:149-152) —
that would re-run htdemucs_ft over 74 minutes of audio (~17 min on this box) just to reach the
pass we actually want. `separator.separate` writes exactly what `_wind()` in
analysis/structure.py reads back, so filling `<stems>/other/` directly gives analyze_structure
the wind/comp voices at the cost of one pass.

READ-ONLY on Resolve: never connects. Reads the mix `other` stem off disk, writes the
`other/` pass directory beside it.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

GPU_SEPARATOR = r"C:\Users\Daniel\.venvs\audio-separator-gpu\Scripts\audio-separator.exe"
os.environ["RESOLVE_MCP_AUDIO_SEPARATOR"] = GPU_SEPARATOR

STEMS_DIR = Path(
    r"C:\Users\Daniel\AppData\Local\resolve-mcp\stems"
    r"\Zinc-Set-2-Reaper-v4.wav-8833f33949fe-d73a1e4c156e"
)
OUT = Path(__file__).with_name("wind_pass.json")

report: dict[str, Any] = {"state": "starting", "pid": os.getpid(), "separator": GPU_SEPARATOR}


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")


def main() -> None:
    from resolve_mcp.audio import separator
    from resolve_mcp.audio import stems as stems_module
    from resolve_mcp.config import get_config

    config = get_config()
    report["wind_model"] = config.wind_model
    report["config_separator"] = config.audio_separator

    source = separator.collect(STEMS_DIR / stems_module.MIX_PASS).get(stems_module.OTHER_SOURCE)
    if source is None:
        report.update({"state": "failed", "error": f"no other stem under {STEMS_DIR}"})
        write()
        return
    report["source"] = str(source)
    out_dir = STEMS_DIR / stems_module.OTHER_PASS
    report["out_dir"] = str(out_dir)
    report["state"] = "running"
    write()

    started = time.time()

    def progress(fraction: float) -> None:
        report["progress"] = round(fraction, 4)
        report["elapsed_seconds"] = round(time.time() - started, 1)
        write()

    found = separator.separate(
        source,
        out_dir,
        config.wind_model,
        stems_module.WIND_STEMS,
        progress=progress,
        config=config,
    )
    report.update(
        {
            "state": "completed",
            "elapsed_seconds": round(time.time() - started, 1),
            "stems": {stems_module.WIND_KEYS.get(k, k): str(v) for k, v in found.items()},
        }
    )
    write()


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:  # noqa: BLE001 - the record is the only reporting channel
        # detail carries the separator's own last 40 lines (separator.OUTPUT_TAIL), which is the
        # only place the reason for an exit-1 exists — str(exc) says "refused" and nothing more.
        report.update(
            {
                "state": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "detail": getattr(exc, "detail", None),
                "trace": traceback.format_exc(),
            }
        )
        write()
        raise
