# resolve-mcp

An MCP server that gives Claude Code hands inside **DaVinci Resolve Studio** — so the
musical and editorial reasoning happens in Claude and the mechanical work happens in Resolve.

Build contract: [issue #22](https://github.com/danielbaldwin47/resolve-mcp/issues/22).

## Status

P1 in progress. Shipped so far: the server skeleton, the session/project tools, the media
pool tools, the timeline read and interchange tools, and the `run_python` escape hatch.

| Tool | What it does |
| --- | --- |
| `get_status` | Connection state, Resolve version, current project + timeline, fps |
| `list_projects` | Project names in the current database folder |
| `open_project` | Loads a project by name; the result echoes the new context |
| `snapshot_project` | Writes an opaque `.drp` backup before a big operation |
| `import_media` | Imports files and image sequences into a bin, still-duration workaround applied |
| `list_media` | Summarises media pool clips, with offline state; spills big listings to disk |
| `inspect_clip` | One clip in full: properties, metadata, audio mapping, markers, dual-time bounds |
| `set_clip_metadata` | Batch metadata writes, each field routed by what the clip reports |
| `organize_media` | Batch bin operations: create nested bins, move clips |
| `relink_media` | Points offline clips at media that moved (folder relink or file replace) |
| `list_timelines` | Timelines with version, duration, fps and track stack; names the newest cut |
| `inspect_timeline` | One timeline at a chosen detail and range, in dual time |
| `export_timeline` | Writes a timeline out as OTIO, FCPXML or DRT |
| `import_timeline` | Materialises a **new** timeline from such a file — never overwrites one |
| `run_python` | Escape hatch: runs scripting-API Python in the server process |

Bin paths are slash-separated from the media pool root (`Concert/Angles`) and
case-sensitive. A clip counts as **offline** when it has a file path that is not on
disk — Resolve's scripting API exposes no offline flag.

Interchange is the **structural escape hatch**. The scripting API cannot cut a transition,
so a dissolve is made by exporting the cut to OTIO, editing the transition into that
document, and importing it back. An import is always given a name no timeline in the
project answers to — colliding names walk the `<base> v<N>` convention — so the cut that is
already there is never the thing that gets written over.

## Requirements

- Windows 11, DaVinci Resolve **Studio** 21.0.3 (external scripting must be enabled:
  _Preferences > System > General > External scripting using_ = **Local**)
- **CPython 3.12 x64 installed from python.org** — not a uv-managed interpreter. See
  [ADR 0001](docs/adr/0001-python-interpreter-must-be-a-registered-install.md): the
  Resolve scripting library crashes the process outright on a standalone build, so the
  server refuses to attach on one. [uv](https://docs.astral.sh/uv/) still manages the
  venv and the lockfile.
- Resolve running, with a project open, before the first Resolve-touching tool call

## Install

```sh
uv venv --python "C:/Users/<you>/AppData/Local/Programs/Python/Python311/python.exe"
uv sync
```

`uv sync` alone will pick a uv-managed interpreter, which runs the unit suite fine but
cannot attach to Resolve.

## Run

```sh
uv run resolve-mcp
```

The server speaks MCP over stdio and logs to stderr only. Register it with Claude Code:

```sh
claude mcp add resolve -- uv --directory C:/Users/Daniel/repos/resolve-mcp run resolve-mcp
```

## Configuration

Zero-config by default; every path has an environment override.

| Variable | Default | Purpose |
| --- | --- | --- |
| `RESOLVE_SCRIPT_API` | `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting` | Scripting API root (holds `Modules/DaVinciResolveScript.py`) |
| `RESOLVE_SCRIPT_LIB` | `C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll` | Scripting library |
| `RESOLVE_MCP_CACHE` | `%LOCALAPPDATA%\resolve-mcp` | Cache root: snapshots, analysis artifacts, model weights |
| `RESOLVE_MCP_LOG_LEVEL` | `INFO` | Log level for the stderr logger |
| `RESOLVE_MCP_ALLOW_ANY_PYTHON` | unset | Bypass the interpreter check (see ADR 0001) |

## Development

```sh
uv run pytest                 # unit suite — runs with Resolve closed, against the fake seam
uv run pytest -m live         # live smoke tier — needs real Resolve running, on a python.org interpreter
uv run mypy                   # type check
uv run ruff check .           # lint
```

Tests exercise external behaviour only: they call the tool functions in-process (no stdio
transport) against a fake Resolve API object substituted at the connection singleton.
