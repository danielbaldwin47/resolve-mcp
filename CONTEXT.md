# CONTEXT.md

Repo map for agents: every module under `src/resolve_mcp/`, what it owns, the test
that covers it, the seam that test drives (`tests/test_context_map.py` keeps it
complete). Structural — no signatures, no history. Narrative lives in
`docs/context/`, one file per area, read ranged (or Grepped) only when you are
about to work there: [vocabulary](docs/context/vocabulary.md) ·
[analysis](docs/context/analysis.md) · [audio](docs/context/audio.md) ·
[cut](docs/context/cut.md) · [jobs](docs/context/jobs.md) ·
[resolve](docs/context/resolve.md) · [titles + tools](docs/context/tools.md) ·
[video](docs/context/video.md) · [tests](docs/context/tests.md) ·
[agent-owned trees + docs](docs/context/repo.md).

**What this is.** An MCP server that lets an agent edit concert footage in DaVinci
Resolve Studio: analyse audio (beats, structure, transcription, stems), author cut
and titles files as validated JSON, materialise them as timelines. The server
measures; Claude decides. Seams — `fake`: the fake Resolve API in
`tests/fakes/` swapped in at `resolve/connection` (`set_connection()`), tools
called in-process; `pure`: no Resolve handle, direct calls over dicts, fixture
audio or synthetic frames; `live`: a running Resolve Studio (`pytest -m live`);
`sub`: the script run as a subprocess, the way the harness runs it.

| Module | Owns | Test | Seam |
| --- | --- | --- | --- |
| `__main__` | `python -m resolve_mcp` entry: start the server | `test_server` | fake |
| `config` | zero-config defaults, `RESOLVE_MCP_*` env overrides | `test_config` | pure |
| `deliver` | render preset + timeline span as a background job | `test_render_tools` | fake |
| `document` | read agent JSON off disk, hash the bytes read; `Preflight`, the loaded-document + findings shape both contracts subclass | `test_findings` | pure |
| `errors` | structured cause/fix errors; tracebacks never reach the agent | `test_connection` | pure |
| `ffmpeg` | the one place the server shells out to ffmpeg; NVDEC probe | `test_hardware_decode` | pure |
| `findings` | `{rule, id, message, fix_hint}`; `report` the one `{errors, warnings}` reply, `refuse` the one raise-if-errors preamble | `test_findings` | pure |
| `interpreter` | which interpreters may attach to fusionscript (ADR 0001) | `test_interpreter` | pure |
| `lease` | is the owner of a claim still alive: `SESSION`, `liveness`, `claim`/`holder` | `test_lease` | pure |
| `logging_config` | stderr-only logging (stdout is MCP transport) | `test_server` | fake |
| `naming` | names for written files and `<base> v<N>` timelines | `test_naming` | pure |
| `server` | FastMCP app + tool registration from each module's `TOOLS`; no logic | `test_server` | fake |
| `sharing` | the Windows retry for a file another handle holds | `test_sharing` | pure |
| `spill` | oversized results → disk; `capped` is the one truncated-reply shape | `test_spill` | pure |
| `timing` | frames authoritative; seconds/timecode/fps derived | `test_timing` | pure |
| `analysis/applause` | applause bursts → tune boundaries, walked to the band's entry | `test_applause_spans` | pure |
| `analysis/barmap` | a cut read against the bar map: `map_bar`, `in_group`, `bar_offset` | `test_bar_map_join` | pure |
| `analysis/bars` | the bar map: meter + phase over the grid, or `refused` | `test_bar_map` | fake |
| `analysis/beats` | grid + downbeats, model injected (ADR 0002); `trust`, `spacing` | `test_beat_grid` | pure |
| `analysis/correlate` | a cut against its music: the join over the readings below | `test_correlate_timeline` | fake |
| `analysis/cuda` | preload the CUDA runtime so CTranslate2 finds it on Windows | `test_whisper_runtime` | pure |
| `analysis/decode` | WAV → numpy, no third-party decoder | `test_energy_curves` | pure |
| `analysis/device` | which device the torch models infer on, named not defaulted | `test_device` | pure |
| `analysis/drums` | hits per drum stem | `test_drum_fills` | fake |
| `analysis/energy` | loudness curves; `rms_curve` the cheap level-only pass | `test_energy_curves` | pure |
| `analysis/fills` | drum-fill candidates over the hits | `test_drum_fills` | fake |
| `analysis/halves` | identify/cache/write shared by every detector; `written`, `collected` | `test_music_analysis` | fake |
| `analysis/melody` | notes off one melodic stem, model injected | `test_melody` | pure |
| `analysis/music` | beats + energy + gist job; `beats_of`/`energy_of` shared entries | `test_music_analysis` | fake |
| `analysis/phrases` | phrase boundaries: where the soloist stops | `test_phrases` | fake |
| `analysis/records` | sliceable record files: `write`, the one reader `rows(path, field)` | `test_records` | pure |
| `analysis/rhythm` | how varied the cutting is: `shot_rhythm`, `gears`, `quiet_floor` | `test_rhythm` | pure |
| `analysis/silence` | RMS silence spans | `test_transcript` | pure |
| `analysis/solos` | front-of-band changes off stem energy and timbre | `test_solo_changes` | pure |
| `analysis/stats` | offsets, histogram, measured-at-all over a record column | `test_correlate_timeline` | fake |
| `analysis/structure` | tunes + solo changes job over the shared halves | `test_music_structure` | fake |
| `analysis/subject` | what a shot frames × who is out front → `on_soloist` | `test_subject` | pure |
| `analysis/transcribe` | transcription job | `test_transcription` | fake |
| `analysis/transcript` | transcript document + Word/Transcription vocabulary | `test_transcript` | pure |
| `analysis/virtual` | a cut file read back as its words: the P4 self-review | `test_virtual_transcript` | fake |
| `analysis/whisper` | default backend: faster-whisper large-v3 | `test_whisper_runtime` | pure |
| `audio/acquire` | concert audio out of Resolve onto disk, both routes | `test_audio_acquisition` | fake |
| `audio/ffmpeg` | the per-source-clip acquisition route's ffmpeg commands | `test_audio_acquisition` | fake |
| `audio/riff` | the WAV container: PCM, float and extensible headers | `test_wav_container` | pure |
| `audio/separator` | python-audio-separator out of process; torch build probed | `test_stem_separation` | fake |
| `audio/stems` | the two stem passes + opt-in `split_wind`; `claimed` claim policy | `test_stem_separation` | fake |
| `audio/wav` | WAV header facts + the one unreadable-WAV error | `test_wav_container` | pure |
| `cut/document` | cut file read off disk | `test_cut_tools` | fake |
| `cut/layout` | where every entry lands: positions, placements, overlays | `test_cut_layout` | pure |
| `cut/otio` | the tail's document surgery over exported OTIO, no Resolve | `test_cut_otio` | pure |
| `cut/resolution` | the optional delivery resolution, one reading for rules and build | `test_cut_resolution` | fake |
| `cut/schema` | schema v1 verbatim, served by `get_cut_schema` | `test_cut_tools` | fake |
| `cut/tail` | the optional tail device: type, duration, audio fade | `test_cut_tail` | fake |
| `cut/validate` | 12 errors + W1/W2/W8/W9 shared by dry run and build | `test_cut_validate` | pure |
| `jobs/cache` | hash-keyed results; `audio_identity`, `fingerprint` (ADR 0003, 0007) | `test_job_cache` | pure |
| `jobs/detached` | hand a job to a process that outlives this one | `test_detached_jobs` | fake |
| `jobs/lifecycle` | job states; `verdict(record, now, alive)` from the record alone | `test_job_lifecycle` | pure |
| `jobs/runner` | start heavy work without stalling stdio | `test_job_runner` | pure |
| `jobs/store` | one JSON record per job on disk; `load` = read → verdict → write | `test_job_store` | pure |
| `jobs/worker` | the detached process's entry point | `test_detached_jobs` | fake |
| `resolve/apply` | titles file → the owned Titles track | `test_titles_tools` | fake |
| `resolve/build` | materialise a cut file as a timeline | `test_build_timeline` | fake |
| `resolve/camera_sidecar` | camera model off the card's own XML (not an angle sidecar) | `test_media_tools` | fake |
| `resolve/connection` | **the seam**: lazy singleton, probe, one auto reconnect | `test_connection` | fake |
| `resolve/cut` | the cut-file contract (`Preflight` subclass) | `test_findings` | pure |
| `resolve/fusion` | Text+ node, text, opacity fade spline | `test_titles_tools` | fake |
| `resolve/interchange` | timeline export/import | `test_timeline_interchange` | fake |
| `resolve/loader` | import DaVinciResolveScript + direct-attach | `test_loader` | pure |
| `resolve/markers` | marker read/write, the review loop's transport | `test_marker_tools` | fake |
| `resolve/media` | the six media operations, all callers of `pool` | `test_media_tools` | fake |
| `resolve/mix` | where the master mix sits under a timeline | `test_mix` | fake |
| `resolve/pool` | the media pool adapter: bins, lookup, reading, frame bounds | `test_media_pool` | fake |
| `resolve/render` | render queue | `test_render_queue` | fake |
| `resolve/scripting` | `run_python` with handles pre-bound | `test_run_python` | fake |
| `resolve/session` | session/project wrappers | `test_session_tools` | fake |
| `resolve/settings` | the timeline settings the server writes; resolution read back | `test_cut_resolution` | fake |
| `resolve/tail` | the tail's export/import round trip: `Staging` in, `Landed` out | `test_cut_tail` | fake |
| `resolve/takes` | take selectors + in-place `swap_take` | `test_swap_take` | fake |
| `resolve/timeline` | timeline read wrappers | `test_timeline_tools` | fake |
| `resolve/title_edit` | edit one title already on the timeline, no re-apply | `test_title_edit` | fake |
| `resolve/titles` | titles file against a project + dry run (`Preflight` subclass) | `test_findings` | pure |
| `titles/assets` | PNG title cards: what an event points at, frames behind it | `test_titles_assets` | pure |
| `titles/document` | titles file read off disk | `test_titles_tools` | fake |
| `titles/schema` | titles schema verbatim, served by `get_titles_schema` | `test_titles_tools` | fake |
| `titles/validate` | 9 errors + 2 warnings | `test_titles_validate` | pure |
| `tools/analysis` | analysis + correlate tools | `test_correlate_timeline` | fake |
| `tools/cut` | cut tools: schema, dry run, build, swap, virtual transcript | `test_cut_tools` | fake |
| `tools/envelope` | shared envelope + `@tool`/`@tool_without_connection` + connection injection + handle-death retry + job-record wrap | `test_envelope` | fake |
| `tools/escape_hatch` | `run_python` | `test_run_python` | fake |
| `tools/jobs` | job status/list/cancel tools | `test_job_tools` | fake |
| `tools/media` | the media tools over `resolve/media` | `test_media_tools` | fake |
| `tools/project` | session/project tools | `test_session_tools` | fake |
| `tools/render` | deliver tools | `test_render_tools` | fake |
| `tools/stems` | `separate_stems` | `test_stem_separation` | fake |
| `tools/timeline` | timeline, marker and interchange tools | `test_timeline_tools` | fake |
| `tools/titles` | `apply_titles`, `list_titles`, `edit_title` | `test_titles_tools` | fake |
| `tools/video` | frame grab, scene, occlusion, quality tools | `test_frame_grabs` | fake |
| `video/blocking` | how blocked one frame is + the discriminator; numpy, no I/O | `test_occlusion` | pure |
| `video/ffmpeg` | the commands video routes run; NVDEC flags, fallback, report | `test_hardware_decode` | pure |
| `video/frames` | frame grabs — the one compute route that is not a job | `test_frame_grabs` | fake |
| `video/framing` | how far the picture steps across a cut; the 30-degree flag | `test_visual_delta` | pure |
| `video/jpeg` | read back dimensions | `test_frame_grabs` | fake |
| `video/occlusion` | blocking as a cached job over a sampled range | `test_occlusion` | fake |
| `video/picture` | sharpness, exposure, clipping, stability of one frame | `test_quality` | pure |
| `video/quality` | picture as a cached job; three calibrated floors | `test_quality` | fake |
| `video/sampled` | range check, sample grid, runs → windows shared by both scans | `test_occlusion` | fake |
| `video/scenes` | scene-cut detection as a cached job | `test_scene_cuts` | fake |
| `video/source` | clip name → file path + the clip's own frame numbering | `test_frame_grabs` | fake |
| `video/supers` | burned-in graphics: which are up when, which cuts land inside | `test_supers` | pure |

**Tests not on a single row** (seam in parentheses). Spanning devices, one file each:
`test_cut_devices` (gaps + overlays across layout/validate/build/takes/virtual; fake),
`test_analysis_reports` (every detector's file opens `kind`/`audio`/`duration_seconds`;
fake), `test_rough_cut_pillar` (the P4 pillar end to end; fake), `test_style_layer`
(server code never touches `styles/`; pure), `test_text_plus_probe` (the Text+
template-append probe; fake), `test_read_guard` and `test_context_guard` (the Read hook and
the shell hook; sub), `test_context_map` (this map covers the tree; pure), `test_prune_merged` (`scripts/prune_merged.py` over an injected `Runner`; pure), `test_review_gate` (`scripts/review_gate.py` over an injected `Runner`; pure). Live tier: `test_live_smoke`, `test_live_analysis`, and
the state it builds in `live_state` (decisions covered by `test_live_state`; fake).
Fixtures and helpers: `conftest` (installs the fake seam, hermetic `Config`),
`cutfile`, `roughcut`, `mediapool`, `otio`, `text_plus_probe`, `currency_probe`.
The fake Resolve API, one module per subsystem — open the module, never the
package: `fakes/core`, `fakes/connection`, `fakes/project`, `fakes/pool`,
`fakes/media`, `fakes/timeline`, `fakes/timeline_item`, `fakes/fusion`,
`fakes/interchange`, `fakes/separator`, `fakes/fixtures`, `fakes/builders`. `tests/data/`: the one committed real footage (occlusion evidence grids).

**Not modules.** `styles/`, `gauntlet/`, `projects/` — agent-owned data, never read by server code; `scripts/` — repo maintenance (`prune_merged.py`, CLAUDE.md step 6) and the workflows' own logic (`review_gate.py`, the `Review: clean @<sha>` verdict `.github/workflows/review-gate.yml` runs). Both: `docs/context/repo.md`. ADRs cited above: `docs/adr/`.
