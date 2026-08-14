"""Read-only probe: what does this Resolve build offer for transitions and audio fades?"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("endev_probe.json")


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.resolve.connection import get_connection

    conn = get_connection()
    resolve = conn.handle()
    report: dict[str, Any] = {"version": resolve.GetVersionString()}
    pm = resolve.GetProjectManager()
    project = pm.GetCurrentProject()
    report["project"] = project.GetName()
    tl = project.GetCurrentTimeline()
    report["timeline"] = tl.GetName() if tl else None

    report["resolve_attrs"] = sorted(a for a in dir(resolve) if not a.startswith("_"))
    report["project_attrs"] = sorted(a for a in dir(project) if not a.startswith("_"))
    report["timeline_attrs"] = sorted(a for a in dir(tl) if not a.startswith("_")) if tl else []
    mp = project.GetMediaPool()
    report["pool_attrs"] = sorted(a for a in dir(mp) if not a.startswith("_"))

    item = None
    if tl:
        for track in range(1, int(tl.GetTrackCount("video") or 0) + 1):
            items = tl.GetItemListInTrack("video", track) or []
            if items:
                item = items[0]
                break
    if item is not None:
        report["item_attrs"] = sorted(a for a in dir(item) if not a.startswith("_"))
        try:
            report["item_properties"] = item.GetProperty()
        except Exception as exc:  # noqa: BLE001
            report["item_properties_error"] = repr(exc)
        for name in ("Opacity", "AudioLevel", "Volume", "FadeIn", "FadeOut", "CompositeMode"):
            try:
                report.setdefault("probe_get", {})[name] = item.GetProperty(name)
            except Exception as exc:  # noqa: BLE001
                report.setdefault("probe_get", {})[name] = f"ERR {exc!r}"

    audio_item = None
    if tl:
        for track in range(1, int(tl.GetTrackCount("audio") or 0) + 1):
            items = tl.GetItemListInTrack("audio", track) or []
            if items:
                audio_item = items[0]
                break
    if audio_item is not None:
        report["audio_item_attrs"] = sorted(
            a for a in dir(audio_item) if not a.startswith("_")
        )
        try:
            report["audio_item_properties"] = audio_item.GetProperty()
        except Exception as exc:  # noqa: BLE001
            report["audio_item_properties_error"] = repr(exc)

    report["resolve_constants"] = sorted(
        a for a in dir(resolve) if a.isupper() or a.startswith("EXPORT_")
    )
    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT, flush=True)
    print("version", report["version"], "project", report["project"], flush=True)


if __name__ == "__main__":
    main()
