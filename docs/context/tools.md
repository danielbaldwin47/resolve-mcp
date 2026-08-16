# `titles/` and `tools/` — the MCP tool layer

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

`titles/` — `document` (read off disk), `schema` (verbatim, served by
`get_titles_schema`), `validate` (9 errors + 2 warnings).

`tools/` — MCP tool layer, thin, grouped by workflow: `analysis`, `cut`,
`envelope` (**shared envelope + the `@tool` / `@tool_without_connection` decorators +
handle-death retry + the job-record wrap**),
`escape_hatch` (`run_python`), `jobs`, `media`, `project`, `render`,
`stems`, `timeline`, `titles`, `video`. Registration: each module exposes
`TOOLS: tuple`; `server.build_server()` iterates every module's `TOOLS`
and calls `mcp.tool(fn)` — nothing binds to FastMCP at import, so every
tool is callable in tests without the transport.

Which decorator a tool takes says whether it touches Resolve (#229). `@tool`
declares `connection: ResolveConnection` first and is handed the live one —
no body calls `get_connection()` — and the decorator strips that parameter
from the signature registration reads, so the agent never sees it. A tool
that answers from documents alone (a schema, a cut file, a job record) takes
`@tool_without_connection` and keeps its own signature.

There is no `styles/` module and there never will be: the style layer is data
the agent owns, not code the server runs.
