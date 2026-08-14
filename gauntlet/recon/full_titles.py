"""Point the Taurus titles file at the whole-song timeline and apply it. READ/WRITE (one file).

Titles never ride the cut file (schema sec 5): apply_titles owns the Titles track, so a rebuild is
followed by a re-apply. The two events are the ones the opening piece won with and their frames
are unchanged - the card and the personnel super both sit inside the opening, which the whole-song
cut carries frame for frame.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
TITLES = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people.titles.json"
TIMELINE = "Taurus People Full P4 R1 v2"


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import titles as title_tools

    doc = json.loads(TITLES.read_text(encoding="utf-8"))
    doc["timeline"] = TIMELINE
    TITLES.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    val = title_tools.validate_titles(str(TITLES))
    print("validate:", json.dumps(val, default=str)[:800], flush=True)
    if val.get("errors"):
        return
    got = title_tools.apply_titles(str(TITLES))
    print("apply:", json.dumps(got, default=str)[:1500], flush=True)


if __name__ == "__main__":
    main()
