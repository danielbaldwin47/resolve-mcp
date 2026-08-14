"""Round-2 recon: dump the shape of the ending-events recon files."""

import json
from pathlib import Path

HERE = Path(__file__).parent


def walk(o, depth=0, path=""):
    pad = "  " * depth
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, list):
                print(f"{pad}{k}: list[{len(v)}]")
                if v and isinstance(v[0], dict):
                    print(f"{pad}  keys: {list(v[0].keys())}")
                    print(f"{pad}  [0]: {json.dumps(v[0])[:300]}")
                elif v:
                    print(f"{pad}  [0]: {v[0]!r}")
            elif isinstance(v, dict):
                print(f"{pad}{k}: dict")
                if depth < 3:
                    walk(v, depth + 1)
            else:
                print(f"{pad}{k} = {str(v)[:200]}")


for name in ("taurus_ending_events.json", "occlusion_ending.json"):
    print("=" * 20, name)
    walk(json.loads((HERE / name).read_text()))
