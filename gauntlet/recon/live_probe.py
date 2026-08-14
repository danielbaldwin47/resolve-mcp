"""READ-ONLY live recon of the open Resolve project.

Calls resolve_mcp tool functions directly (no MCP transport). Nothing here writes:
no project switch, no timeline switch (make_current stays False), no media changes.
Writes findings to live_probe.json next to this file.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("live_probe.json")
TARGET_TIMELINES = ("Zinc SYNC", "Zinc - Set 2 Main")

report: dict[str, Any] = {
    "attach": {"ok": None},
    "interpreter": {},
    "project": {},
    "timelines": {},
    "target_timelines": {},
    "media_pool": {},
    "repo_assets": {},
    "errors": [],
}

REPO_ROOT = Path(__file__).resolve().parents[2]


def collect_repo_assets(project_name: str | None) -> None:
    """Which style sidecars / project folders exist for the open project."""
    angles_dir = REPO_ROOT / "styles" / "angles"
    projects_dir = REPO_ROOT / "projects"
    sidecars = sorted(p.name for p in angles_dir.glob("*.json")) if angles_dir.is_dir() else []
    declared: dict[str, str] = {}
    for name in sidecars:
        try:
            declared[name] = json.loads(
                (angles_dir / name).read_text(encoding="utf-8")
            ).get("project")
        except Exception as exc:  # noqa: BLE001
            note_error(f"read sidecar {name}", exc)
    exact = f"{project_name}.json" if project_name else None
    report["repo_assets"] = {
        "repo_root": str(REPO_ROOT),
        "styles_angles_dir": str(angles_dir),
        "styles_angles_exists": angles_dir.is_dir(),
        "sidecar_files": sidecars,
        "sidecar_declared_project": declared,
        "sidecar_for_open_project": {
            "expected_filename": exact,
            "exists": bool(exact and (angles_dir / exact).exists()),
            "matched_by_declared_project": [
                name for name, proj in declared.items() if proj == project_name
            ],
        },
        "projects_dir": str(projects_dir),
        "projects_dir_exists": projects_dir.is_dir(),
        "projects_dir_entries": (
            sorted(p.name for p in projects_dir.iterdir()) if projects_dir.is_dir() else []
        ),
        "project_folder_for_open_project": (
            (projects_dir / project_name).is_dir() if project_name else False
        ),
    }


def note_error(where: str, exc: BaseException) -> None:
    report["errors"].append(
        {
            "where": where,
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(limit=6),
        }
    )


def main() -> None:
    import sys

    report["interpreter"] = {
        "executable": sys.executable,
        "base_prefix": sys.base_prefix,
        "version": sys.version.split()[0],
    }

    try:
        from resolve_mcp import interpreter as interp

        report["interpreter"]["registered_installs"] = [
            str(p) for p in interp.registered_install_paths()
        ]
        report["interpreter"]["is_supported"] = interp.is_supported()
        interp.ensure_supported()
        report["interpreter"]["guard"] = "passed"
    except Exception as exc:  # noqa: BLE001
        report["interpreter"]["guard"] = "refused"
        note_error("interpreter.ensure_supported", exc)
        write()
        return

    try:
        from resolve_mcp.resolve.connection import get_connection
        from resolve_mcp.tools import media as media_tools
        from resolve_mcp.tools import project as project_tools
        from resolve_mcp.tools import timeline as timeline_tools
    except Exception as exc:  # noqa: BLE001
        note_error("import resolve_mcp.tools", exc)
        write()
        return

    # --- attach + project ------------------------------------------------
    try:
        status = project_tools.get_status()
        report["attach"] = {"ok": bool(status.get("ok")), "raw": status}
        report["project"] = {
            "context": status.get("context"),
            "product": status.get("product"),
        }
    except Exception as exc:  # noqa: BLE001
        report["attach"] = {"ok": False}
        note_error("get_status", exc)
        write()
        return

    try:
        report["project"]["projects_in_db"] = project_tools.list_projects()
    except Exception as exc:  # noqa: BLE001
        note_error("list_projects", exc)

    # --- timelines -------------------------------------------------------
    try:
        listing = timeline_tools.list_timelines(limit=500)
        report["timelines"] = listing
    except Exception as exc:  # noqa: BLE001
        note_error("list_timelines", exc)

    for name in TARGET_TIMELINES:
        entry: dict[str, Any] = {}
        try:
            # detail="tracks" gives per-track item_count without pulling every clip;
            # make_current=False so the open timeline in the Resolve window never moves.
            inspected = timeline_tools.inspect_timeline(
                timeline=name, detail="tracks", make_current=False
            )
            entry["inspect"] = inspected
            if inspected.get("ok"):
                tl = inspected.get("timeline", {})
                tracks = inspected.get("tracks") or []
                entry["summary"] = {
                    "name": tl.get("name"),
                    "fps": tl.get("fps"),
                    "start": tl.get("start"),
                    "end": tl.get("end"),
                    "duration": tl.get("duration"),
                    "track_counts": tl.get("tracks"),
                    "marker_count": tl.get("markers"),
                    "item_count": inspected.get("item_count"),
                    "items_per_video_track": [
                        {
                            "index": t.get("index"),
                            "name": t.get("name"),
                            "item_count": t.get("item_count"),
                        }
                        for t in tracks
                        if t.get("type") == "video"
                    ],
                    "items_per_audio_track": [
                        {
                            "index": t.get("index"),
                            "name": t.get("name"),
                            "item_count": t.get("item_count"),
                        }
                        for t in tracks
                        if t.get("type") == "audio"
                    ],
                    "currency": inspected.get("currency"),
                }
        except Exception as exc:  # noqa: BLE001
            note_error(f"inspect_timeline({name!r})", exc)
        try:
            markers = timeline_tools.list_markers(timeline=name, limit=1000)
            entry["markers"] = {
                "ok": markers.get("ok"),
                "count": markers.get("count"),
                "colors": markers.get("colors"),
                "truncated": markers.get("truncated"),
                "spilled_to": markers.get("spilled_to"),
                "error": markers.get("error"),
            }
        except Exception as exc:  # noqa: BLE001
            note_error(f"list_markers({name!r})", exc)
        report["target_timelines"][name] = entry

    # --- media pool ------------------------------------------------------
    try:
        from resolve_mcp.resolve import media as media_wrapper

        connection = get_connection()
        pool = media_wrapper.media_pool(connection)
        root = pool.GetRootFolder()

        def count_clips(folder: Any) -> tuple[int, int]:
            """(clips directly in folder, clips in folder + all descendants)."""
            direct = len(folder.GetClipList() or [])
            total = direct
            for sub in folder.GetSubFolderList() or []:
                total += count_clips(sub)[1]
            return direct, total

        top: list[dict[str, Any]] = []
        for folder in root.GetSubFolderList() or []:
            direct, total = count_clips(folder)
            subs = [f.GetName() for f in (folder.GetSubFolderList() or [])]
            top.append(
                {
                    "name": folder.GetName(),
                    "clips_direct": direct,
                    "clips_recursive": total,
                    "subfolders": subs,
                }
            )
        root_direct = len(root.GetClipList() or [])
        report["media_pool"] = {
            "root_name": root.GetName(),
            "clips_at_root": root_direct,
            "top_level_bins": top,
            "total_clips": root_direct + sum(b["clips_recursive"] for b in top),
            "all_bin_paths": media_wrapper.bin_paths(pool),
        }
    except Exception as exc:  # noqa: BLE001
        note_error("media_pool walk", exc)
        # fall back to the tool if the raw walk failed
        try:
            report["media_pool"]["list_media_fallback"] = media_tools.list_media(limit=1)
        except Exception as exc2:  # noqa: BLE001
            note_error("list_media fallback", exc2)

    # --- repo-side assets ------------------------------------------------
    collect_repo_assets((report["project"].get("context") or {}).get("project"))

    write()


def write() -> None:
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"errors: {len(report['errors'])}")


if __name__ == "__main__":
    main()
