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
- **songs file** — `projects/<project>/songs.json`, song key → title +
  personnel; agent-authored, never read by server code — the facts behind
  the titles file (`docs/agents/rough-cut.md`, #132).
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
  back verbatim (#122); `""` is never the whole pool — that is the
  omitted form. Each media tool taking a bin — `list_media`,
  `inspect_clip`, `relink_media`, and per item on `set_clip_metadata` and
  `organize_media`'s `move_clips` — also takes `recursive`, false meaning
  that bin's own clips alone: the address of a copy a subfolder shadows
  (#134). The analysis and video tools resolve a clip by name too but take
  no flag, so their refusals never offer the shallow form.
- **the seam** — `resolve/connection.py` singleton, substituted by
  `tests/fakes/` via `set_connection()`; the only place fakes attach.
- **fake tier / live tier** — `pytest -m 'not live'` against fakes (the
  default) vs `-m live` against a running Resolve Studio. See CLAUDE.md.
- **stem** — separated audio (mix → vocals/drums/bass/other; drums →
  kick/snare/toms/ride/crash), path is a content hash (ADR 0003). The drum
  model writes `hh` too; it is not collected (#125).
- **wind / comp** — the two halves of the opt-in third pass over `other`
  (#153). `wind` is horns and reeds; `comp` is accompaniment — piano,
  guitar, vibes, percussion, and the bass line itself on a capture whose
  `bass` stem came back near-silent (#126). Where both are on disk they
  replace `other` as voices in `solos` — `other` *is* their sum, and
  measuring all three counts the residual twice (#157).
  _Avoid_: "piano stem" as a name for `comp` — it is accompaniment, and
  nothing may name it otherwise (#126).
- **bar map** — `analysis/bars.py`'s reading: one record per bar, each with its
  downbeat time, its length, the grid beat it starts on and its `in_group`
  position in the four-bar group. Every map says its `source` — `model` when the
  beat model committed to a meter and the map takes it at its word, `inferred`
  when it was recovered from the accents, `refused` when neither reading was
  worth having. The last is the point: the failure it ends is a grid quietly
  reporting `meter: 1` and callers doing bar arithmetic on it (#180).
  _Avoid_: reading `in_group` as a phrase — it is hypermeter, saying a bar line
  is a plausible place for a phrase to turn over, never that one did.
- **tactus** — the pulse the bars are counted in, and the thing a bar map folds
  a subdivision-scale grid down to. Not the grid's own beat: on the corpus
  anchor the grid is swung eighths and the tactus is every second one of them.
- **phrase** — the cut-placement unit (#46, `styles/concert.md` §1): a
  stretch of the soloist's line between two endings. `analysis/phrases.py`
  reports the **boundaries**, each with two times — `measured_t`, where the
  line actually stopped, and `t`, the beat inside the rest that a cut is
  placed on. Not the `phrase` factor inside `fills`, which is only "how far
  into a four-bar group does this land".
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
  by subject × character; `correlate_timeline` reads `role` and `subject`
  (falling back to the subject half of a `subject-character` role) plus the
  optional `voice`, which says what the solo map calls that subject, and it
  arrives as a mapping the agent lifted, never as a path.
  _Avoid_: `camera_sidecar` for this — that module reads a camera model off
  the card's own XML (#94) and is not an angle sidecar.
- **super** — a burned-in graphic: a lower third, a title card, a bug. Read off a
  render (`video/supers.py`), never off a timeline, because by then it is pixels.
  Two shapes: a **card** holds the whole frame, an **overlay** sits on the picture.
  The reading is what two frames whose footage has moved on still agree about, and
  it is believed only where the *same* pixels agree twice — on a dark stage of
  locked-off cameras a lit music stand carries across a reading as well as
  lettering does, but never twice in the same place (#183).
  _Avoid_: reading a **straddle** — a cut with a graphic up either side of it — as
  a fault on its own. The human deliverables hold a lower third across cuts all
  night.

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
`applause` (bursts → tune boundaries, then a beat-density floor drops the calls
with no pulse under them, #133; every boundary then walks forward off the applause
to where the loudness curve says the band comes in, and a mix the threshold finds
no clapping in at all is read at its own scale instead, #179),
`bars` (the **bar map**: a rule layer over the grid for the material the beat
model will not commit a meter to — folds a too-fast grid to the tactus, then
scores every meter and phase against a per-beat accent reading and takes the
widest lead over the runner-up, refusing rather than guessing when the accents
say nothing. The accent reading is injected per ADR 0002 and defaults to RMS off
the mix; a named stem reads that instead, #180), `beats` (grid + downbeats, model
injected per ADR 0002; `trust` says which beats the grid describes well enough
to count, #112; `spacing` says how wide a beat is at each beat), `correlate`
(measure a cut against its music — by default the *visible* edit,
every frame resolved to the topmost enabled video item with uncovered stretches
as black shots, #142; `track=` measures one video track alone. Gates the beat
statistics on `trust`, refuses as `stranded` a cut further from its beat than a
beat is wide — the grid does not reach it, #160 — and leaves the transient ones
ungated. Also reads the cutting itself: `shot_rhythm` bins the shot lengths,
measures the longest strict A/B alternation run and the longest monotonic
duration `ramp`, and says `reads_metronomic` with the heuristic that drew it,
and its `gears` block splits the cut's span into loudness terciles off a 1 s
RMS curve and reports cuts per minute in each, the loud/quiet `rate_ratio`,
where the sub-2 s shots sit, `one_speed`, and `outside_shots` — shots past
the analysed mix, counted apart rather than clamped into a tercile — plus
`quiet_floor`, the passages the slow gear is held through, found by smoothing
that curve rather than off the per-window tercile labels, each read for the
spread its lone flashes are not holding up (#190) —
warnings the report carries, never gates. Takes an optional **bar map**
(`bars=`) and then reports `map_bar`, `in_group` and `bar_offset` per cut and
a `bar_groups` histogram — ungated on the beat gate, since the map exists for
the grids that gate refuses whole, #180),
`cuda` (preloads
the CUDA runtime the `analysis` extra ships, so CTranslate2 finds it on Windows;
pure decisions, #128),
`decode` (WAV → numpy, no third-party decoder), `device` (which device the
torch models infer on, announced once per process and carried in job records —
no silent CPU fallback, #202; the inventory and the stays-on-CPU corpus policy:
`docs/reference/compute-device-inventory.md`), `drums` (hits per stem), `energy`
(loudness curves; `rms_curve` is the cheap level-only pass, no K-weighting and
no onsets), `fills` (drum-fill candidates), `halves` (shared
identify/cache/write pattern, plus `collected`/`stem_named` — where a
separation's stems are and which one was asked for, shared by every detector
that reads one: `phrases` off the line, `bars` off the pulse),
`melody` (notes off one melodic stem —
monophonic pitch + gating, model injected per ADR 0002; the reading `phrases`
is a rule layer over, as `drums` is to `fills`), `music` (beats + energy + gist
job; `beats_of`/`energy_of` are the shared entries other jobs read a grid or a
loudness curve through, one measurement per piece of audio), `phrases` (phrase boundaries: where the soloist stops, which is the
cut-placement unit #46 named, #143),
`records` (sliceable record files), `silence` (RMS spans), `solos` (front
of band changes: lead off the stem energy, timbre off one stem's brightness —
with the third pass on disk the voices are `wind`/`comp` rather than `other`
and timbre reads `wind`, #157), `structure` (tunes + solo changes job; both
halves read the shared beats half and the tune half the shared energy half; its
stem loader is what reaches the third pass), `subject` (what a shot is framed
on crossed with who is out front: the angle sidecar's subject read as
player/ensemble/other, joined to the solo windows in seconds so a shot that
outlives its solo is split where the front changed — pure, no I/O, read by
`correlate`, #181), `transcribe`
(job), `transcript` (document + Word/Transcription/Transcriber vocabulary),
`virtual` (a cut file read back as the words it will contain — the P4
self-review, warnings only, touches no Resolve handle), `whisper`
(default backend: faster-whisper large-v3).

`audio/` — concert audio out of Resolve onto disk: `acquire` (both routes),
`ffmpeg` (per-source-clip route), `riff` (the WAV container itself: PCM,
IEEE float and extensible headers, because stdlib `wave` opens PCM only),
`separator` (python-audio-separator out of process), `stems` (two passes —
mix into four, then the drum stem into the kit — plus an opt-in third,
`split_wind`, splitting `other` into `wind` and `comp`; `comp` is
accompaniment, never a piano stem), `wav` (header facts + the one
unreadable-WAV error).

`cut/` — cut-file schema v1: `document` (read off disk), `schema`
(verbatim, served by `get_cut_schema`), `validate` (12 errors + W1, W2,
W8 — W3-W7 are `virtual_transcript`'s over the same document — shared by
dry run and build pre-flight), `tail` (the optional **tail** device: one
reading of `{type, duration_frames, audio_fade_frames}` for both the rules
and the build). A `segments` entry is a shot or a **gap**
(`{"id", "gap": <frames>}`, literal black); `is_gap`/`entry_duration`/
`overlay_track` are the accessors every walker of that array shares.

`jobs/` — `cache` (hash-keyed results; `audio_identity` is the content hash
wherever the file sits, read off a `known_hash` note remembered against a
stat, except under `audio_dir` where it is always read for real;
`fingerprint` is path+size+mtime and stays the identity for video sources
and stems — ADR 0007, ADR 0003), `runner` (start heavy work without
stalling stdio), `store` (one JSON record per job on disk), `detached` (hand
a job to a process that outlives this one — flags, command, environment),
`worker` (that process's entry point: `python -m resolve_mcp.jobs.worker
<job-id>`). A worker returning `runner.Detached` instead of a result moves
the rest of its job into that process; `separate_stems` does, once the audio
is acquired, so a half-hour separation survives the server exiting. A
detached record is judged by its pid rather than by its session, and only the
worker writes it — the launcher's reading of the worker pid goes to a
`<job-id>.launcher` note beside the record, folded in by readers only while
the record has no pid of its own, so a launcher can never overwrite a result.

`resolve/` — connection management + thin scripting-API wrappers: `apply`
(titles file → owned track), `build` (materialise cut file), `connection`
(**the seam**: lazy singleton, probe, one auto reconnect), `cut` (cut-file
contract), `fusion` (Text+ node, text, opacity fade spline), `interchange`
(timeline export/import), `loader` (import DaVinciResolveScript +
direct-attach), `markers` (read/write, review-loop transport), `media`
(media pool: import, list, inspect, bins, relink), `mix` (where the master
mix sits under a timeline — the one axis a rebuild does not move; read by
`build`'s marker carry and by `analysis/correlate`), `render` (render
queue), `camera_sidecar` (camera model off the card's own XML, for media
Resolve reports no camera metadata for — #94; not an **angle sidecar**),
`scripting` (`run_python` with handles pre-bound), `session`
(session/project wrappers), `tail` (materialising a cut's tail: the OTIO
document edit + the export/import round trip `build` takes when a tail has
a transition to cut in — a hard out that fades nothing builds directly —
because the scripting API cannot cut a transition at all), `takes`
(take selectors + in-place swap), `timeline` (timeline read wrappers),
`titles` (titles file against a project + dry run).

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

`video/` — `ffmpeg` (the commands video routes run), `frames` (frame
grabs — the one compute route that is not a job), `jpeg` (read back
dimensions), `scenes` (scene-cut detection as cached job), `blocking` (how
blocked one frame is: the near-field obstruction arithmetic, numpy + scipy,
no I/O), `occlusion` (that arithmetic as a cached job over a sampled range —
per-sample scores and the unusable windows to keep a cut out of), `framing`
(how far the picture steps *across* a cut and the 30-degree-rule jump-cut
flag: layout, content and size terms over two grey frames, numpy only, no
I/O — the pack measures with it, `analysis/correlate` joins its catalog on),
`supers` (the **super**: which burned-in graphics are up when, and which cuts land
inside one. Pure numpy + scipy over frames somebody else decoded — the pack does
both decodes, a coarse scan for where and a native-fps walk for the exact in and
out, and `analysis/correlate` joins the catalog on as `straddles_super`, #183),
`source` (clip name → file path + the clip's own frame numbering).

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
- `fixtures.py` — `write_wav`/`write_clicks`/`write_hits`/`write_tones`
  (a melodic stem: pitched notes of a known length with known gaps)/
  `write_sections`/
  `write_jpeg`, `ffmpeg_absent`, `ffmpeg_refusing`, `hwaccel_probe_reply`
  (answers the `-hwaccels` capability probe so fake runners survive it);
  and the headers stdlib
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
`tests/text_plus_probe.py` (Text+ probe fixtures), `tests/live_state.py` (the
state the live tier builds for itself: `sweep_suite_timelines()` clears the
previous run's leftovers, `restore_current()` leaves the director's cut open,
`write_hard_cut_clip()` generates the clip the scene scan needs — decisions
covered by `test_live_state.py` in the fake tier).

Test files pair 1:1 with the module they cover (`test_cut_validate.py` ↔
`cut/validate.py`, `test_timeline_tools.py` ↔ `tools/timeline.py` +
`resolve/timeline.py`, …). `test_wav_container.py` ↔ `audio/riff.py` is the
exception that earns its keep: the headers it covers are read by `audio/wav.py`,
`analysis/decode.py` and `analysis/silence.py` alike, and #110 was a bug in what
all three agreed about. `test_rough_cut_pillar.py` is one of two other
exceptions: it covers no single module, walking the P4 pillar across `cut`,
`build`, `takes` and `virtual` in one pass because the joins are what a
per-module test cannot see. `test_cut_devices.py` (#141) is the second, for the
same reason: gaps and overlay tracks are one device each across `cut/validate`,
`resolve/build`, `resolve/takes` and `analysis/virtual`, and the interesting
failures are the disagreements between them. `test_cut_tail.py` is the third: the
tail is one device across `cut/tail`, `cut/validate`, `resolve/tail` and
`resolve/build`, and a dissolve that did not land looks exactly like a cut that
never asked for one. `test_hardware_decode.py` (#202) is the fourth: NVDEC is
one decision across `ffmpeg` (the probe), `video/ffmpeg` (flags, fallback,
report) and the three video routes that carry the report, and the failure worth
testing is a decode that ran one way and reported another. Live tier: `test_live_smoke.py` (module-level
`pytest.mark.live`) and two `@pytest.mark.live` tests in
`test_live_analysis.py`; everything else is fake-tier. The live tier assumes no
project state it can build itself (#135): a session-scoped sweep clears the last
run's timelines, `a_known_cut` builds and makes current the short cut the export
and round-trip tests read, and `a_clip_with_hard_cuts` generates the scan clip
unless `RESOLVE_MCP_SCENE_SCAN_CLIP` names a real one. That variable is
**unset on the live box**: its pool was checked in #135 and holds no flattened
render, only raw continuous angles — so the generated clip is the default there,
and the variable is for a project that does have an edit to scan.

## Agent-owned trees — `gauntlet/`, `projects/`

Neither is read by server code; both are the agent's own working record, and
like `styles/` they are data, not modules.

`gauntlet/` — the gauntlet loop: agent-built cuts judged blind against the
director's own final cuts, piece by piece, where every critic loss becomes
server or workflow work and never a hand-tuned edit. `STATE.md` (protocol,
close rule, where each piece stands), `GAPS.md` (the gap ledger — one entry
per critic loss or prep finding, open/in-work/fixed), `HANDOFF.md`.
`tools/ab_pack.py` is the harness proper: a **sealed blind A/B pack builder**
— two videos in, deterministic A/B labels out, plus contact sheets,
cut-boundary filmstrips and a measured `cuts.json`, with the label→source
mapping quarantined in `assignment.json` so a critic reads the pack without
knowing whose cut is whose; it refuses to seal when its scene scan finds far
fewer cuts than the timeline holds (G3). Given each label's own
`correlate_timeline` cuts file (`--a-subjects`/`--b-subjects`, both or
neither) it also carries the on-soloist track, stripped to four columns so no
timeline or clip name reaches the pack (#181). `recon/` is one-off instruments —
one script plus its JSON receipt per question (plans, builds, pixel checks,
occlusion scans). Renders, packs, frame dirs and the per-frame ffmpeg dumps
under `recon/` are regenerable and gitignored; only scripts and receipts are
committed.

`projects/<project>/` — the agent-authored files for one Resolve project:
`README.md` (its fixed facts — timelines, master mix, what is unverified),
`songs.json`, the cut and titles files, and `cards/` — the PNG title-card
route (`bake_taurus_cards.py` bakes a `%04d` RGBA frame run per card, fade
ramps included, for a project whose media pool holds no GUI-authored Text+
template; `titles/schema.py` §6).

## Docs

- `docs/adr/` — 0001 interpreter must be a registered install; 0002
  analysis models are injected; 0003 stems fingerprinted (path is a
  content hash); 0004 editor-state getters answer only for the current
  timeline; 0005 source frames are read off the left offset, not the
  source start; 0006 markers ride the mix across a rebuild; 0007 audio is
  identified by content, the hash remembered against a stat.
- `docs/agents/` — issue-tracker conventions (wayfinder map ops), triage
  labels, domain-docs usage, the style layer (sidecar + profile formats,
  provenance tags, how a corpus pass is run), the concert pillar
  (`concert.md`: the director's three inputs, session-start analysis prep,
  song-by-song planning, the mandatory `correlate_timeline` self-review,
  the cut report and review-round conventions, #16), the rough-cut pillar
  (`rough-cut.md`: the brief and b-roll catalog the agent owns, the
  assembly loop, the `virtual_transcript` self-review and the cut report;
  also home of the `projects/<project>/` convention and the songs file's
  ownership, #132).
- Landing places for artifacts that today live only in issue and PR
  threads: research reports → `docs/research/`, spike reports and design
  bibles → `docs/reference/`, adversarial and other standalone reviews →
  `reviews/` (dated filenames). All merge to `main` in the PR that
  produced them — a finding on an unmerged branch or in a thread is
  unreadable from here.
- Wayfinder: map = issue #1, scope = #2, spec = #22, build tickets #23–#47.
