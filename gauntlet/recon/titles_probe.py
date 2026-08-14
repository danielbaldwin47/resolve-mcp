"""Probe: the titles schema rules, and what Text+ templates the pool actually holds."""

from __future__ import annotations

import json

POOL = """
mp = project.GetMediaPool()
root = mp.GetRootFolder()
out = []


def walk(folder, path):
    for c in folder.GetClipList():
        try:
            fp = c.GetClipProperty('File Path')
            typ = c.GetClipProperty('Type')
        except Exception:
            fp, typ = None, None
        if not fp:
            out.append(path + ' | ' + str(c.GetName()) + ' | ' + str(typ))
    for sub in folder.GetSubFolderList():
        walk(sub, path + '/' + sub.GetName())


walk(root, '')
result = '\\n'.join(out[:300])
"""


def main() -> None:
    from resolve_mcp import interpreter as interp

    interp.ensure_supported()
    from resolve_mcp.tools import escape_hatch
    from resolve_mcp.tools import titles as title_tools

    _ = title_tools, json
    res = escape_hatch.run_python(code=POOL)
    print(res.get("result"), flush=True)


if __name__ == "__main__":
    main()
