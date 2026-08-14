"""Find the burned-in supers in the human deliverables, and check their cuts. READ-ONLY.

`resolve_mcp.video.supers` says a graphic is what two frames of *different pictures* agree
about. Whether that reading finds the supers a human can see -- and only those -- is not
something the fixture tier can answer: the fixtures build the signal they test for. This
script runs the real path over the director's own final cuts, where the supers are known by
eye (a title card on Taurus People, a titling super and a personnel lower third on Hardest
Part) and the cuts are the ones a critic accepted.

Three things come back, and all three are the point:

* every super found, with its in and out as frames and seconds, so a human can scrub to one
  and see whether it is there;
* every cut that lands inside one -- the deliverables are the control here, and a straddle
  found in them is far more likely to be this measurement's fault than the editor's;
* `clears_before` per super, which is the title-card convention (#169) as a number instead
  of a thing verified by hand off filmstrips.

Usage: uv run python gauntlet/recon/super_scan.py [--songs N] [--only NAME] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gauntlet" / "tools"))

import ab_pack  # noqa: E402

from resolve_mcp.video import supers  # noqa: E402

HUMAN_DIR = Path(r"S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos")
OUT = ROOT / "gauntlet" / "recon" / "super_scan.json"


def measure(clip: Path) -> dict[str, Any]:
    """One deliverable, through the same two passes a pack build runs."""
    started = time.monotonic()
    fps = ab_pack.probe_fps(clip)
    duration = ab_pack.probe_duration(clip)
    cuts = ab_pack.detect_cuts(clip)
    review = ab_pack.super_scan(clip, fps, cuts)
    return {
        "clip": str(clip),
        "fps": round(fps, 4),
        "duration_sec": round(duration, 3),
        "cuts_detected": len(cuts),
        "seconds_to_measure": round(time.monotonic() - started, 1),
        **review,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--songs", type=int, default=0, help="stop after this many")
    parser.add_argument("--only", default=None, help="substring of the filename to measure")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    clips = sorted(HUMAN_DIR.glob("*.mp4"))
    if args.only:
        clips = [one for one in clips if args.only.lower() in one.name.lower()]
    if args.songs:
        clips = clips[: args.songs]
    if not clips:
        print(f"error: no deliverables under {HUMAN_DIR}", file=sys.stderr)
        return 1

    measured = []
    for clip in clips:
        print(f"measuring {clip.name} ...", flush=True)
        found = measure(clip)
        measured.append(found)
        print(
            f"  {len(found['supers'])} supers "
            f"({found['cards']} cards, {found['overlays']} overlays), "
            f"{found['straddled']} straddled of {found['cuts']} cuts, "
            f"{found['seconds_to_measure']}s",
            flush=True,
        )
        for one in found["supers"]:
            print(
                f"    {one['kind']:<8} f{one['visible_first']}-{one['visible_last']} "
                f"({one['t']}-{one['end']}s) box "
                f"top={one['top']} left={one['left']} bottom={one['bottom']} "
                f"right={one['right']} clears_before={one['clears_before']}",
                flush=True,
            )

    receipt = {
        "what": "burned-in supers in the human deliverables, and the cuts that land on them",
        "measured_at": time.strftime("%Y-%m-%d"),
        "reading": {
            "grid": f"{supers.GRID_WIDTH}x{supers.GRID_HEIGHT}",
            "scan_fps": ab_pack.SUPER_RATE,
            "lag_sec": list(ab_pack.SUPER_LAGS_SEC),
            "changed_at_or_below": supers.CHANGED,
            "held_at_or_above": supers.HELD,
            "contrast": supers.CONTRAST,
            "min_area_share": supers.MIN_AREA,
            "max_box_share": supers.MAX_BOX,
            "step_across_span_at_or_above": supers.STEP,
        },
        "caveat": (
            "the cut list comes from the same per-frame scene detector the pack uses, which "
            "is blind to the dissolve-cut songs (#203). A song reporting few cuts has few "
            "cuts to check a straddle against, not few straddles."
        ),
        "songs": measured,
        "totals": {
            "supers": sum(len(one["supers"]) for one in measured),
            "cards": sum(one["cards"] for one in measured),
            "overlays": sum(one["overlays"] for one in measured),
            "straddled": sum(one["straddled"] for one in measured),
            "cuts": sum(one["cuts"] for one in measured),
        },
    }
    args.out.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"\nwrote {args.out}")
    print(json.dumps(receipt["totals"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
