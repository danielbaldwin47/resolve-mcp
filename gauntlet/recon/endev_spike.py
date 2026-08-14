"""Spike: can an OTIO round trip give a tail dissolve to black AND an audio fade out?

Throwaway timelines only ('SCRATCH endev ...'). Never touches the gauntlet cuts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
OUT = HERE / "endev_spike.json"
CUT = HERE / "endev_scratch.cut.json"

FPS = 23.976
# ~20 s of picture out of the ending piece's own sources, on the scratch name.
SCRATCH_CUT: dict[str, Any] = {
    "schema": 1,
    "timeline": {"name": "SCRATCH endev"},
    "sources": {
        "fx6_wide": {
            "clip": "A015C001_2606170J.MXF",
            "bin": "Zinc Bar/Footage/FX6/Set 2",
        },
        "a7iv_kit": {
            "clip": "20260617_D_A7IV_0006.MP4",
            "bin": "Zinc Bar/Footage/A7IV/Set 2",
        },
        "master_mix": {"clip": "Zinc Set 2 Reaper v4.wav", "bin": "Zinc Bar/Audio"},
    },
    "audio": {"source": "master_mix", "in": 95332, "out": 95812},
    "segments": [
        {"id": "s01", "source": "fx6_wide", "in": 64157, "out": 64397},
        {"id": "s02", "source": "a7iv_kit", "in": 95588, "out": 95828},
    ],
}
SCRATCH_CUT["timeline"]["fps"] = FPS


def summarise(doc: dict[str, Any]) -> list[dict[str, Any]]:
    stack = doc.get("tracks") or {}
    out = []
    for track in stack.get("children") or []:
        kids = []
        for child in track.get("children") or []:
            schema = str(child.get("OTIO_SCHEMA", ""))
            rng = child.get("source_range") or {}
            kids.append(
                {
                    "schema": schema,
                    "name": child.get("name"),
                    "frames": (rng.get("duration") or {}).get("value"),
                    "rate": (rng.get("duration") or {}).get("rate"),
                    "transition_type": child.get("transition_type"),
                    "in_offset": (child.get("in_offset") or {}).get("value"),
                    "out_offset": (child.get("out_offset") or {}).get("value"),
                }
            )
        out.append({"kind": track.get("kind"), "name": track.get("name"), "children": kids})
    return out


def rational(rate: float, value: int) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": value}


def transition(name: str, rate: float, frames: int) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Transition.1",
        "name": name,
        "metadata": {},
        "transition_type": "SMPTE_Dissolve",
        "in_offset": rational(rate, frames),
        "out_offset": rational(rate, 0),
    }


def gap(rate: float, frames: int) -> dict[str, Any]:
    return {
        "OTIO_SCHEMA": "Gap.1",
        "name": "gap",
        "metadata": {},
        "effects": [],
        "markers": [],
        "source_range": {
            "OTIO_SCHEMA": "TimeRange.1",
            "start_time": rational(rate, 0),
            "duration": rational(rate, frames),
        },
    }


def rate_of(item: dict[str, Any]) -> float:
    duration = ((item.get("source_range") or {}).get("duration")) or {}
    return float(duration.get("rate") or FPS)


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import cut as cut_tools
    from resolve_mcp.tools import timeline as tl_tools

    report: dict[str, Any] = {}
    CUT.write_text(json.dumps(SCRATCH_CUT, indent=1), encoding="utf-8")

    built = cut_tools.build_timeline(cut_file=str(CUT))
    report["build_ok"] = built.get("ok")
    name = (built.get("timeline") or {}).get("name")
    report["built"] = name
    print("BUILT", name, built.get("ok"), built.get("error"), flush=True)
    if not built.get("ok"):
        OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        return

    target = HERE / "endev_spike.otio"
    exported = tl_tools.export_timeline(timeline=name, path=str(target))
    report["export"] = exported
    print("EXPORT", exported.get("ok"), exported.get("path"), flush=True)
    if not exported.get("ok"):
        OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        return

    doc = json.loads(Path(exported["path"]).read_text(encoding="utf-8"))
    report["exported_shape"] = summarise(doc)

    # Inject: tail dissolve to black on every video track, fade out on every audio track.
    frames = 142  # 5.923 s at 23.976
    for track in (doc.get("tracks") or {}).get("children") or []:
        kids = list(track.get("children") or [])
        if not kids:
            continue
        last = kids[-1]
        rate = rate_of(last)
        kids.append(transition("Fade to Black", rate, frames))
        kids.append(gap(rate, 4))
        track["children"] = kids

    injected = HERE / "endev_spike_injected.otio"
    injected.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    report["injected_shape"] = summarise(doc)

    imported = tl_tools.import_timeline(path=str(injected), name="SCRATCH endev injected")
    report["import"] = imported
    print("IMPORT", imported.get("ok"), imported.get("error"), flush=True)
    if imported.get("ok"):
        landed = imported["timeline"]["name"]
        report["landed"] = landed
        # Read the imported timeline back out as OTIO: the only getter for a transition.
        back = HERE / "endev_spike_readback.otio"
        again = tl_tools.export_timeline(timeline=landed, path=str(back))
        report["readback_export"] = {"ok": again.get("ok"), "error": again.get("error")}
        if again.get("ok"):
            report["readback_shape"] = summarise(
                json.loads(Path(again["path"]).read_text(encoding="utf-8"))
            )

    OUT.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT, flush=True)


if __name__ == "__main__":
    main()
