"""Does a blind pack carry the on-soloist track intact? (#181, AC3)

No Resolve, no ffmpeg, no render: this runs `ab_pack`'s subject-track path over the
two real `correlate_timeline` cuts files `subject_track.py` produced and checks the
three things a pack could get wrong.

1. **Stripped.** Only the columns SUBJECT_FIELDS names come through -- a pack that
   carries a timeline name or a camera roll's file name is not a blind pack.
2. **Whole span.** With the pack covering the whole cut, the pack's share must equal
   the share correlate already reported. A pack that quietly recomputes it differently
   is worse than a pack without one.
3. **Cut span.** With the pack covering the middle of the cut, a shot the span cuts
   through counts the part inside where the front held through it, and is left out of
   the share where it did not -- and the shots left out are counted, not swallowed.

The detected-shot join is exercised against boundaries deliberately out of step with
the authored ones (offset, and two shots merged into one), because that disagreement
is exactly what a scene scan does to a real pack.

    uv run python gauntlet/recon/subject_pack.py
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
OUT = HERE / "subject_pack.json"
TRACK_RECEIPT = HERE / "subject_track.json"
AB_PACK = HERE.parents[1] / "gauntlet" / "tools" / "ab_pack.py"


def load_ab_pack() -> Any:
    """Load the pack builder by path: `gauntlet/tools` is not a package."""
    spec = importlib.util.spec_from_file_location("ab_pack", AB_PACK)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {AB_PACK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def detected(shots: list[dict[str, Any]], offset: float) -> list[dict[str, Any]]:
    """The pack's own shot list, as a scene scan would hand it back: out of step.

    Every boundary moved by `offset`, and the second and third shots run together as
    one -- the missed cut that overlap matching exists to survive.
    """
    edges = [(float(one["start"]), float(one["end"])) for one in shots]
    if len(edges) > 3:
        edges = [edges[0], (edges[1][0], edges[2][1]), *edges[3:]]
    return [
        {"index": index, "start": round(start + offset, 3), "end": round(end + offset, 3)}
        for index, (start, end) in enumerate(edges, start=1)
    ]


def check(ab_pack: Any, name: str, cuts_file: Path, reported: dict[str, Any]) -> dict[str, Any]:
    track = ab_pack.load_subject_track(cuts_file)
    leaked = sorted({key for row in track for key in row} - {"t", "seconds", *ab_pack.SUBJECT_FIELDS})
    # The cut's own span, not the concert's: these times count from the master mix's zero, so
    # a render of this cut starts at the first shot rather than at t=0.
    opens = min(row["t"] for row in track)
    closes = max(row["t"] + row["seconds"] for row in track)

    whole = ab_pack.place_subject_track(track, (opens, closes), closes - opens)
    whole_shots = detected(whole["shots"], offset=0.4)
    ab_pack.attach_subjects(whole_shots, whole["shots"])

    middle = (opens + (closes - opens) * 0.25, opens + (closes - opens) * 0.75)
    cut = ab_pack.place_subject_track(track, middle, middle[1] - middle[0])

    return {
        "timeline": name,
        "cuts_file": str(cuts_file),
        "shots_in_track": len(track),
        "fields_beyond_the_four": leaked,
        "whole_span": {
            "summary": whole["summary"],
            "matches_correlate": whole["summary"] == reported,
            "correlate_said": reported,
            "detected_shots_labelled": sum(
                1 for shot in whole_shots if shot.get("subject") is not None
            ),
            "detected_shots": len(whole_shots),
            "first_three_detected": whole_shots[:3],
        },
        "cut_span": {
            "span_sec": [round(one, 3) for one in middle],
            "shots_outside_clip": cut["shots_outside_clip"],
            "shots_cut_by_the_span": cut["shots_cut_by_the_span"],
            "shots_left_out_of_the_share": cut["shots_left_out_of_the_share"],
            "summary": cut["summary"],
        },
    }


def main() -> None:
    ab_pack = load_ab_pack()
    receipt = json.loads(TRACK_RECEIPT.read_text(encoding="utf-8"))
    checks = [
        check(
            ab_pack,
            one["timeline"],
            Path(one["cuts_file"]),
            one["reading"]["on_soloist"],
        )
        for one in receipt["measured"]
        if one.get("cuts_file")
    ]
    OUT.write_text(
        json.dumps({"source_receipt": str(TRACK_RECEIPT), "checks": checks}, indent=2),
        encoding="utf-8",
    )
    for one in checks:
        print(
            f"{one['timeline']}: whole-span share matches correlate = "
            f"{one['whole_span']['matches_correlate']}, "
            f"leaked fields = {one['fields_beyond_the_four'] or 'none'}"
        )
    print("wrote", OUT)


if __name__ == "__main__":
    main()
