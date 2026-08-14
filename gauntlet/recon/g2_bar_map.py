"""The G2 bar map over the Zinc mix, for real — the reading #180's server work produces.

Gap G2 is that the beat grid over this set reports ``meter: 1`` at 214 "bpm": the model
tracked the swung eighth and called every one of them a downbeat, so no bar-position rule
can fire. ``analysis.bars`` is the second reading over that same grid. This runs it on the
real mix and on the real bass stem — the walking-quarter witness — and writes both readings
side by side so the disagreement, if there is one, is visible rather than averaged.

No Resolve, no beat model: the grid comes off the cached beats file the corpus was measured
with, and the accents come off the audio. Writes gauntlet/recon/g2_bar_map.json, and the
window of bar lines the ear check needs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from resolve_mcp.analysis import bars  # noqa: E402

CACHE = Path(r"C:\Users\Daniel\AppData\Local\resolve-mcp")
BEATS = CACHE / "analysis" / "Zinc-Set-2-Reaper-v4-62537c590a14-beats.json"
MIX = Path(r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav")
BASS = (
    CACHE
    / "stems"
    / "Zinc-Set-2-Reaper-v4.wav-8833f33949fe-d73a1e4c156e"
    / "mix"
    / "Zinc-Set-2-Reaper-v4.wav-8833f33949fe_(Bass)_htdemucs_ft.wav"
)
OUT = Path(__file__).with_name("g2_bar_map.json")

SPANS = {
    "taurus-people": (3514.9, 4450.5),
    "whole-set": (0.0, 4450.5),
}
"""Tune boundaries off the cached structure analysis, plus the whole set for contrast.

The whole set is here to be *wrong*: one fold, one meter and one phase over five tunes at
different tempos with applause between them cannot describe any of them, and the number it
returns is what a caller who skipped the span arguments would get.
"""

EAR_CHECK_BARS = 24
"""How many consecutive bar lines to list for the ear check.

Two dozen is a chorus and a bit at this tempo: long enough that a map one beat out drifts
audibly against the tune, short enough to check by hand in one sitting.
"""


def read(
    path: Path,
    grid: list[dict[str, object]],
    label: str,
    span: tuple[float, float],
) -> dict[str, object]:
    if not path.is_file():
        return {"witness": label, "error": f"not on disk: {path}"}
    start, end = span
    inside = [row for row in grid if start <= float(row["t"]) < end]  # type: ignore[arg-type]
    times = [float(row["t"]) for row in inside]  # type: ignore[arg-type]
    salience = bars.accents(path, times)
    mapped = bars.mapped(inside, salience)
    return {
        "witness": label,
        "audio": str(path),
        "span": [start, end],
        "beats_in_span": len(inside),
        "gist": bars.gist(mapped, bars.DEFAULT_MINIMUM_CONFIDENCE, label),
        "reasons": mapped.reasons,
        "ear_check_bar_lines": [one._asdict() for one in mapped.bars[:EAR_CHECK_BARS]],
    }


def main() -> None:
    written = json.loads(BEATS.read_text(encoding="utf-8"))
    grid = list(written["beats"])
    readings = [
        read(path, grid, f"{label}/{tune}", span)
        for tune, span in SPANS.items()
        for label, path in (("mix", MIX), ("bass", BASS))
    ]
    receipt = {
        "question": "Does analysis.bars recover a bar line the beat grid could not give (#180, G2)?",
        "beats_file": str(BEATS),
        "grid_beats": len(grid),
        "readings": readings,
        "note": (
            "The two witnesses agreeing on meter and phase is the result to trust. Disagreeing "
            "means the mix accents and the bass line are not saying the same thing about where "
            "the bar starts, and the bass is the one to believe on this idiom — it walks "
            "quarters. A refusal is a real answer: it says RMS at the beat carries no bar-level "
            "accent on this material, which is what brushes and a walking line sound like. The "
            "ear check is the verdict where a map was written: play the mix from the first "
            "listed time and count whether the rest land on the one."
        ),
    }
    OUT.write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    for reading in readings:
        print(reading.get("witness"), reading.get("gist") or reading.get("error"))


if __name__ == "__main__":
    main()
