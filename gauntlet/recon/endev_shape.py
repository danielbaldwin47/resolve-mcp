"""What does Resolve's OTIO export put at the end of a video track whose audio outlives it?"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "endev_shape.json"
TIMELINE = "SCRATCH ending-devices v1 (tail staging)"


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import timeline as tl_tools

    target = HERE / "endev_shape.otio"
    exported = tl_tools.export_timeline(timeline=TIMELINE, path=str(target))
    print("export", exported.get("ok"), exported.get("error"), flush=True)
    if not exported.get("ok"):
        return
    doc = json.loads(Path(exported["path"]).read_text(encoding="utf-8"))
    shape: list[dict[str, Any]] = []
    for track in (doc.get("tracks") or {}).get("children") or []:
        shape.append(
            {
                "kind": track.get("kind"),
                "name": track.get("name"),
                "children": [
                    {
                        "schema": child.get("OTIO_SCHEMA"),
                        "name": child.get("name"),
                        "frames": ((child.get("source_range") or {}).get("duration") or {}).get(
                            "value"
                        ),
                    }
                    for child in track.get("children") or []
                ],
            }
        )
    OUT.write_text(json.dumps(shape, indent=1, default=str), encoding="utf-8")
    print(json.dumps(shape, default=str), flush=True)


if __name__ == "__main__":
    main()
