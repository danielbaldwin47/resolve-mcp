"""Validate (dry run) and then apply the Taurus titles file.

Usage: python r2_titles.py            -> validate only
       python r2_titles.py apply      -> validate, then apply
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TITLES = HERE.parents[1] / "projects" / "mcp-tests-zinc" / "taurus-people.titles.json"

BACK = """
found = None
for i in range(1, project.GetTimelineCount() + 1):
    tl = project.GetTimelineByIndex(i)
    if tl.GetName() == 'Zinc SYNC':
        found = tl
ok = project.SetCurrentTimeline(found) if found else None
result = {'set': ok, 'current': project.GetCurrentTimeline().GetName()}
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import titles as title_tools

    val = title_tools.validate_titles(titles_file=str(TITLES))
    print("VALIDATE:", json.dumps(val, default=str, indent=1)[:3000], flush=True)
    if not val.get("ok"):
        return
    if len(sys.argv) > 1 and sys.argv[1] == "apply":
        res = title_tools.apply_titles(titles_file=str(TITLES))
        print("APPLY:", json.dumps(res, default=str, indent=1)[:4000], flush=True)
    print("back:", json.dumps(escape_hatch.run_python(code=BACK), default=str)[:300], flush=True)


if __name__ == "__main__":
    main()
