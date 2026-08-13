"""Which bins hold the Zinc master mix clip. READ-ONLY."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).with_name("find_mix_clip.json")
NAME = "Zinc Set 2 Reaper"


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import media as media_tools

    listing = media_tools.list_media(name_contains=NAME, recursive=True, limit=200)
    OUT.write_text(json.dumps(listing, indent=2, default=str), encoding="utf-8")
    print("DONE")


if __name__ == "__main__":
    main()
