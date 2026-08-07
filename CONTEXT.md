# CONTEXT.md

Repo map and vocabulary for agents. Read this instead of exploring the tree;
spawn an Explore agent only for what this file can't hold — exact signatures,
current line numbers, behaviour. Structural only, no signatures: those go
stale silently. **Maintenance rule: a PR that adds, moves, or deletes a
module updates this map in the same PR.**

## What this project is

An MCP server that lets an agent edit concert footage in DaVinci Resolve
Studio: analyse audio (beats, structure, transcription, stems), author cut
and titles files as validated JSON, and materialise them as Resolve
timelines. The server measures; Claude decides.

## Vocabulary

- **cut file** — agent-authored JSON describing a timeline (schema v1,
  `cut/schema.py`); validated then materialised, never edited in place.
- **titles file** — agent-authored JSON of Text+ title events, applied to
  one owned track.
- **job** — background compute (analysis, stems, scenes) with one JSON
  record on disk; disk is the only source of truth.
- **envelope** — the shared tool-result shape every MCP tool returns
  (`tools/envelope.py`).
- **spill** — oversized results written to disk for the agent to grep
  instead of truncating.
- **the seam** — `resolve/connection.py` singleton, substituted by
  `tests/fakes.py` via `set_connection()`; the only place fakes attach.
- **fake tier / live tier** — `pytest -m 'not live'` against fakes (the
  default) vs `-m live` against a running Resolve Studio. See CLAUDE.md.
- **stem** — separated audio (mix → vocals/drums/bass/other; drums →
  kick/snare/toms), path is a content hash (ADR 0003).

## Module map — `src/resolve_mcp/`

Top level:

| Module | Responsibility |
| --- | --- |
| `config.py` | zero-config defaults, `RESOLVE_MCP_*` env overrides |
| `deliver.py` | render preset + timeline span as a background job |
| `document.py` | read agent-authored JSON off disk, hash exactly the bytes read |
| `errors.py` | structured cause/fix errors; tracebacks never reach the agent |
| `ffmpeg.py` | the one place the server shells out to ffmpeg (argv lists) |
| `findings.py` | shared finding shape `{rule, id, message, fix_hint}` |
| `interpreter.py` | guard on which interpreters may attach to fusionscript (ADR 0001) |
| `logging_config.py` | stderr-only logging (stdout belongs to MCP transport) |
| `naming.py` | names for written files and `<base> v<N>` timelines |
| `server.py` | FastMCP app + tool registration; no logic |
| `spill.py` | oversized results → disk |
| `timing.py` | frames authoritative; seconds/timecode/fps derived |

`analysis/` — compute jobs that read audio and write findings to disk:
`applause` (bursts → tune boundaries), `beats` (grid + downbeats, model
injected per ADR 0002), `correlate` (measure a cut against its music),
`decode` (WAV → numpy, stdlib only), `drums` (hits per stem), `energy`
(loudness curves), `fills` (drum-fill candidates), `halves` (shared
identify/cache/write pattern), `music` (beats + energy + gist job),
`records` (sliceable record files), `silence` (RMS spans), `solos` (front
of band changes), `structure` (tunes + solo changes job), `transcribe`
(job), `transcript` (document + Word/Transcription/Transcriber vocabulary),
`whisper` (default backend: faster-whisper large-v3).

`audio/` — concert audio out of Resolve onto disk: `acquire` (both routes),
`ffmpeg` (per-source-clip route), `separator` (python-audio-separator out
of process), `stems` (two-pass separation), `wav` (read back written WAVs).

`cut/` — cut-file schema v1: `document` (read off disk), `schema`
(verbatim, served by `get_cut_schema`), `validate` (11 errors + 2
warnings, shared by dry run and build pre-flight).

`jobs/` — `cache` (hash-keyed results), `runner` (start heavy work without
stalling stdio), `store` (one JSON record per job on disk).

`resolve/` — connection management + thin scripting-API wrappers: `apply`
(titles file → owned track), `build` (materialise cut file), `connection`
(**the seam**: lazy singleton, probe, one auto reconnect), `cut` (cut-file
contract), `fusion` (Text+ node, text, opacity fade spline), `interchange`
(timeline export/import), `loader` (import DaVinciResolveScript +
direct-attach), `markers` (read/write, review-loop transport), `media`
(media pool: import, list, inspect, bins, relink), `render` (render
queue), `scripting` (`run_python` with handles pre-bound), `session`
(session/project wrappers), `takes` (take selectors + in-place swap),
`timeline` (timeline read wrappers), `titles` (titles file against a
project + dry run).

`titles/` — `document` (read off disk), `schema` (verbatim, served by
`get_titles_schema`), `validate` (9 errors + 2 warnings).

`tools/` — MCP tool layer, thin, grouped by workflow: `analysis`, `cut`,
`envelope` (**shared envelope + `@tool` decorator + handle-death retry**),
`escape_hatch` (`run_python`), `jobs`, `media`, `project`, `render`,
`stems`, `timeline`, `titles`, `video`. Registration: each module exposes
`TOOLS: tuple`; `server.build_server()` iterates every module's `TOOLS`
and calls `mcp.tool(fn)` — nothing binds to FastMCP at import, so every
tool is callable in tests without the transport.

`video/` — `ffmpeg` (the two commands video routes run), `frames` (frame
grabs — the one compute route that is not a job), `jpeg` (read back
dimensions), `scenes` (scene-cut detection as cached job), `source` (clip
name → file path + the clip's own frame numbering).

## Test map — `tests/`

`tests/fakes.py` is the fake Resolve API (single file; grep the class name,
then ranged-read). Classes in order: `DroppedHandleError`, `AnswersNone`,
`FakeSpline`, `FakeFusionTool`, `FakeFusionComp`, `FakeTimelineItem`,
`FakeTrack`, `FakeTimeline`, `FakeMediaPoolItem`, `FakeFolder`,
`FakeMediaPool`, `FakeProject`, `FakeProjectManager`, `FakeResolve`,
`FakeConnector`, `FakeSeparator`. Installed by the `attach` fixture in
`tests/conftest.py` (autouse `_clean_globals` resets the seam and pins a
hermetic `Config` around every test). Other helpers: `tests/cutfile.py`
(miniature cut file + media pool), `tests/otio.py` (hand-edited OTIO with
a dissolve), `tests/text_plus_probe.py` (Text+ probe fixtures).

Test files pair 1:1 with the module they cover (`test_cut_validate.py` ↔
`cut/validate.py`, `test_timeline_tools.py` ↔ `tools/timeline.py` +
`resolve/timeline.py`, …). Live tier: `test_live_smoke.py` (module-level
`pytest.mark.live`) and two `@pytest.mark.live` tests in
`test_live_analysis.py`; everything else is fake-tier.

## Docs

- `docs/adr/` — 0001 interpreter must be a registered install; 0002
  analysis models are injected; 0003 stems fingerprinted (path is a
  content hash).
- `docs/agents/` — issue-tracker conventions (wayfinder map ops), triage
  labels, domain-docs usage.
- Wayfinder: map = issue #1, scope = #2, spec = #22, build tickets #23–#47.
