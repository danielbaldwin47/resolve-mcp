"""Where the master mix sits under 'Zinc SYNC'. READ-ONLY (make_current stays False).

resolve/mix.py answers this as one number: zero_frame, the record frame the mix's own frame
0 lands on. Every song time from align_cuts.json becomes a record frame by adding it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("mix_anchor.json")
TIMELINE = "Zinc SYNC"
MIX_CLIP = "Zinc Set 2 Reaper v4.wav"


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()

    from resolve_mcp.resolve import mix as mix_module
    from resolve_mcp.resolve.connection import get_connection
    from resolve_mcp.resolve.session import current_project
    from resolve_mcp.resolve.timeline import Reader, find_timeline

    connection = get_connection()
    project = current_project(connection)
    timeline = find_timeline(project, TIMELINE)
    reader = Reader(connection)

    shots = mix_module.audio_shots(reader, timeline)
    anchored = mix_module.anchor(shots, MIX_CLIP)
    any_anchor = mix_module.anchor(shots)

    report: dict[str, Any] = {
        "timeline": str(timeline.GetName()),
        "timeline_start_frame": int(timeline.GetStartFrame()),
        "timeline_end_frame": int(timeline.GetEndFrame()),
        "fps": str(timeline.GetSetting("timelineFrameRate")),
        "audio_shots": [
            {
                "name": s.name,
                "record_in": s.record_in,
                "source_in": s.source_in,
                "zero_frame": s.zero_frame,
            }
            for s in shots
        ],
        "anchor_for_mix_clip": None
        if anchored is None
        else {"name": anchored.name, "zero_frame": anchored.zero_frame},
        "anchor_any": None
        if any_anchor is None
        else {"name": any_anchor.name, "zero_frame": any_anchor.zero_frame},
    }
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("DONE", OUT)


if __name__ == "__main__":
    main()
