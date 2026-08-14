"""Print the shapes of the round-1 recon files so round 2 can read only what it needs."""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    for f in (
        "taurus_mid_events.json",
        "mid_human_cuts.json",
        "occlusion_mid.json",
        "mid_p3_recon.json",
    ):
        d = json.loads((HERE / f).read_text(encoding="utf-8"))
        print("==", f, type(d).__name__)
        if isinstance(d, dict):
            for k, v in d.items():
                n = len(v) if isinstance(v, (list, dict)) else v
                print("   ", k, type(v).__name__, str(n)[:120])
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    print("        keys:", list(v[0].keys()))
                    print("        first:", json.dumps(v[0])[:300])
                if isinstance(v, dict):
                    print("        subkeys:", list(v.keys())[:20])


if __name__ == "__main__":
    main()
