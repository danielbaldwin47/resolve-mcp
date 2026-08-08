# CONTEXT.md

Repo map and vocabulary for agents. Structural on purpose — module names
and responsibilities, no signatures or line numbers: those rot silently.

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
- **bin path** — a media pool folder, slash-separated from the root. To a
  tool addressing one clip by name: omitted is the whole pool, a name is
  that bin and everything nested inside it, `""` is the root folder alone
  — the value `list_media` reports for a root clip, so a listing reads
  back verbatim (#122). `list_media` itself takes the same paths but says
  how deep to walk with its own `recursive` flag.
- **the seam** — `resolve/connection.py` singleton, substituted by
  `tests/fakes/` via `set_connection()`; the only place fakes attach.
- **fake tier / live tier** — `pytest -m 'not live'` against fakes (the
  default) vs `-m live` against a running Resolve Studio. See CLAUDE.md.
- **stem** — separated audio (mix → vocals/drums/bass/other; drums →
  kick/snare/toms), path is a content hash (ADR 0003).
- **style layer** — `styles/` at the repo root: layered Markdown style
  profiles (`base.md` + `concert.md`), the corpus record (`corpus.md`) and
  per-project angle sidecars (`angles/*.json`). Agent-authored,
  director-editable, and **never touched by server code** — see
  `docs/agents/style-layer.md`, guarded by `tests/test_style_layer.py`.
- **provenance tag** — what every style claim ends with, saying how well it is
  known. The vocabulary is settled in #13: `[stated principle]`,
  `[measured — N projects, n=…, context]`, `[review feedback, YYYY-MM]`,
  `[believed, unverified]`.
- **angle sidecar** — one JSON file per Resolve project labelling each camera
  by subject × character; `role` is the only key `correlate_timeline` reads,
  and it arrives as a mapping the agent lifted, never as a path.

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
injected per ADR 0002; `trust` says which beats the grid describes well enough
to count, #112), `correlate` (measure a cut against its music — by default the *visible* edit,
every frame resolved to the topmost enabled video item with uncovered stretches
as black shots, #142; `track=` measures one video track alone. Gates the beat
statistics on `trust` and leaves the transient ones ungated), `cuda` (preloads
the CUDA runtime the `analysis` extra ships, so CTranslate2 finds it on Windows;
pure decisions, #128),
`decode` (WAV → numpy, no third-party decoder), `drums` (hits per stem), `energy`
(loudness curves), `fills` (drum-fill candidates), `halves` (shared
identify/cache/write pattern), `music` (beats + energy + gist job),
`records` (sliceable record files), `silence` (RMS spans), `solos` (front
of band changes), `structure` (tunes + solo changes job), `transcribe`
(job), `transcript` (document + Word/Transcription/Transcriber vocabulary),
`virtual` (a cut file read back as the words it will contain — the P4
self-review, warnings only, touches no Resolve handle), `whisper`
(default backend: faster-whisper large-v3).

`audio/` — concert audio out of Resolve onto disk: `acquire` (both routes),
`ffmpeg` (per-source-clip route), `riff` (the WAV container itself: PCM,
IEEE float and extensible headers, because stdlib `wave` opens PCM only),
`separator` (python-audio-separator out of process), `stems` (two-pass
separation), `wav` (header facts + the one unreadable-WAV error).

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
queue), `camera_sidecar` (camera model off the card's own XML, for media
Resolve reports no camera metadata for — #94; not an **angle sidecar**),
`scripting` (`run_python` with handles pre-bound), `session`
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

There is no `styles/` module and there never will be: the style layer is data
the agent owns, not code the server runs.

`video/` — `ffmpeg` (the two commands video routes run), `frames` (frame
grabs — the one compute route that is not a job), `jpeg` (read back
dimensions), `scenes` (scene-cut detection as cached job), `source` (clip
name → file path + the clip's own frame numbering).

## Test map — `tests/`

`tests/fakes/` is the fake Resolve API, one module per subsystem — open the
module, not the package, and never the whole package at once:

- `core.py` — `DroppedHandleError`, `AnswersNone` (the primitives)
- `fusion.py` — `FakeSpline`, `FakeFusionInput`, `FakeFusionTool`,
  `FakeFusionComp`
- `timeline_item.py` — `FakeTimelineItem`
- `timeline.py` — `FakeTrack`, `FakeTimeline`, `TrackSpec`, and the frame
  arithmetic an append lands on
- `media.py` — `FakeMediaPoolItem`, `FakeFolder`, `text_plus_template`, and
  the helpers that build clips from paths
- `pool.py` — `FakeMediaPool`, `media_pool()`
- `project.py` — `FakeProject`, `FakeProjectManager`
- `connection.py` — `FakeResolve`, `FakeConnector`, `EXPORT_TYPES`
- `separator.py` — `FakeSeparator`
- `fixtures.py` — `write_wav`/`write_clicks`/`write_hits`/`write_sections`/
  `write_jpeg`, `ffmpeg_absent`, `ffmpeg_refusing`; and the headers stdlib
  `wave` cannot write, built by hand — `write_float_wav`,
  `write_extensible_pcm_wav`, `write_tagged_wav`
- `builders.py` — `studio()`, `sync_reference()`, `with_a_mix()`

`__init__.py` re-exports every public name, so `from .fakes import X` works
whatever module `X` lives in and no test file names a submodule. Cross-module
references that exist only in annotations sit under `if TYPE_CHECKING`; that
is what keeps the runtime import graph acyclic. Installed by the `attach` fixture in
`tests/conftest.py` (autouse `_clean_globals` resets the seam and pins a
hermetic `Config` around every test). Other helpers: `tests/cutfile.py`
(miniature concert cut file + media pool), `tests/roughcut.py` (the P4
substrate: one talking head said twice, its transcript, and the b-roll that
covers the join), `tests/otio.py` (hand-edited OTIO with a dissolve),
`tests/text_plus_probe.py` (Text+ probe fixtures).

Test files pair 1:1 with the module they cover (`test_cut_validate.py` ↔
`cut/validate.py`, `test_timeline_tools.py` ↔ `tools/timeline.py` +
`resolve/timeline.py`, …). `test_wav_container.py` ↔ `audio/riff.py` is the
exception that earns its keep: the headers it covers are read by `audio/wav.py`,
`analysis/decode.py` and `analysis/silence.py` alike, and #110 was a bug in what
all three agreed about. `test_rough_cut_pillar.py` is the other exception: it
covers no single module, walking the P4 pillar across `cut`, `build`, `takes`
and `virtual` in one pass because the joins are what a per-module test cannot
see. Live tier: `test_live_smoke.py` (module-level
`pytest.mark.live`) and two `@pytest.mark.live` tests in
`test_live_analysis.py`; everything else is fake-tier.

## Docs

- `docs/adr/` — 0001 interpreter must be a registered install; 0002
  analysis models are injected; 0003 stems fingerprinted (path is a
  content hash); 0004 editor-state getters answer only for the current
  timeline; 0005 source frames are read off the left offset, not the
  source start.
- `docs/agents/` — issue-tracker conventions (wayfinder map ops), triage
  labels, domain-docs usage, the style layer (sidecar + profile formats,
  provenance tags, how a corpus pass is run), the rough-cut pillar
  (`rough-cut.md`: the brief and b-roll catalog the agent owns, the
  assembly loop, the `virtual_transcript` self-review and the cut report).
- Wayfinder: map = issue #1, scope = #2, spec = #22, build tickets #23–#47.
