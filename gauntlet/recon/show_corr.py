"""Print the decisive lines of a taurus_build_r2.json correlate report."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPORT = Path(__file__).resolve().parent / "taurus_build_r2.json"


def main() -> None:
    d = json.loads(REPORT.read_text(encoding="utf-8"))
    b = d["build"]
    tl = b.get("timeline")
    print("built:", tl.get("name") if isinstance(tl, dict) else tl)
    print("warnings:", b.get("warnings"))
    r = d["correlate"]["result"]
    print("cuts", r["cuts"], "stranded", r["stranded"], "outside_grid", r["outside_grid"])
    print("transient_offsets", r["transient_offsets"])
    print("shot_seconds", r["shot_seconds"])
    print("clips", {k: (v["cuts"], round(v["share"], 3)) for k, v in r["clips"].items()})
    for c in r["first_cuts"]:
        start = c["in"]["seconds"] - 3603.604
        print(
            f"  cut{c['cut']:>3} {str(c['clip'])[:14]:<14} start={start:8.3f} "
            f"dur={c['seconds']:6.2f} toff={c['transient_offset']}"
        )
    if len(sys.argv) > 1:
        print(json.dumps(r, indent=1)[:4000])


if __name__ == "__main__":
    main()
