"""Final state check: the project is left on Zinc SYNC with nothing staged. READ-ONLY."""

from __future__ import annotations

import json

CODE = """
names = [project.GetTimelineByIndex(i).GetName()
         for i in range(1, project.GetTimelineCount() + 1)]
result = {'current': project.GetCurrentTimeline().GetName(),
          'timeline_count': len(names),
          'ending_timelines': [n for n in names if 'Ending' in n],
          'staging_left': [n for n in names if 'staging' in n.lower()],
          'set2_main_present': 'Zinc - Set 2 Main' in names}
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch

    print(json.dumps(escape_hatch.run_python(code=CODE), default=str)[:900])


if __name__ == "__main__":
    main()
