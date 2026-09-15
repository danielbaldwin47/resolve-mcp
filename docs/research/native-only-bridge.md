# Prototype — the analysis stack behind `run_script_unsafe` (#269)

PROTOTYPE record, throwaway branch `issue-269`. Question: could Blackmagic's native server be
the only MCP server the agent sees, with this repo's analysis code reached by subprocess?
Measured 2026-09-15, Resolve Studio 21.1.0.17, Windows 11, `mcp-tests-zinc`, RTX box.

Asset: `src/resolve_mcp/_prototype_bridge.py` — `run <tool> <json>` calls a
`tools.analysis` tool, prints its envelope as the first stdout line, closes stdout, and
stays alive until the job ends; `job <id>` peeks a record; `load <id>` goes through
`store.load`.

## Shape that works

The native worker is ResolvePython 3.14 and cannot import `resolve_mcp` (3.12 wheels,
`requires-python <3.13`). So each native call `Popen`s the worktree venv's python with
`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`, reads one line,
closes the pipe, and returns. The bridge process outlives the native call.

## Measurements

| Question | Answer | How |
|---|---|---|
| Does the 60 s cap matter? | Only for the launch, never the job. The beats job ran 36 s of CUDA work while its launch call returned in 0.98 s; correlate ran 29 s behind a 2.6 s launch. | measured |
| Launch cost | Cold first call 15.6 s (venv imports + hashing the 1.28 GB mix, 6.6 s, + 3.12 attach). Warm: 0.87–2.6 s. A bigger mix or a cold disk pushes a cold launch toward the cap. | measured |
| Poll cost | `job <id>` via subprocess 0.07–0.09 s; a raw `json.load` of the record from ResolvePython ~0 s and identical. | measured |
| Envelope | Survives intact: `ok`, `job`, `context` round-trip as one JSON line. `context` makes the 3.12 process attach to Resolve even for `analyze_music`, which needs no Resolve. | measured |
| Result round-trip | JSON on stdout is fine for records; the correlate result is 18,375 chars inline (28 keys) — the agent reads `path` and greps the file, same as today. Spill paths are plain strings and survive unchanged. | measured |
| Resolve I/O through the injected handle | Not possible as-is: the handle lives in the 3.14 process. The bridge attached separately (second `fusionscript` client, coexisting per #265) and read 525 shots of "Zinc - Set 2 Main" in 1.25 s. Honouring "one handle" would need a JSON snapshot of the cut replayed through a Resolve stand-in for `_read_cut` — a real module, not a prototype. | measured + inferred |
| GPU | `beat_this inference on CUDA (torch 2.13.0+cu130)` inside the bridge. | measured |

## Hazards the prototype exposed

1. **`store.load` from a second process kills a live job.** Thread jobs are judged by
   session; a bridge `load` of a running record wrote `failed / job_interrupted` ("The server
   restarted…"), and the holding bridge saw `failed` within 2 s and exited, taking the real
   work thread with it. Any multi-process topology needs peek-only polling or a pid-based
   verdict for bridge jobs. (`analyze_music-7d60668682f3`)
2. **Thread jobs die with their bridge.** Only `separate_stems` has the detached-worker path;
   every other job lives on a daemon thread, so a killed or crashed bridge loses the job and
   the next `load` marks it interrupted.
3. **Silent environment drift.** A plain `uv sync` in the worktree lacked the `analysis` extra;
   the first beats job failed `analysis_dependency_missing`. The native server gives no hint
   which venv a script targets — the agent owns the interpreter path.
4. **Errors hide.** stderr went to `DEVNULL`; a crashing bridge would return an empty line
   and a `JSONDecodeError` inside the native `result`, with the cause only in the bridge log.

## Agent experience versus today

Today: `analyze_music` → `get_job` → `correlate_timeline`, three typed tools whose
docstrings tell the agent what each argument means and what comes back.

Through the bridge: every call is ~15 lines of `Popen` boilerplate the agent writes from
memory; tool names and kwargs are invisible (no schema, no docstrings — the agent must
already know `correlate_timeline(beats=, timeline=, audio=)`); results arrive as JSON nested
inside the native `result`; waiting means a `time.sleep` inside a 60 s script. It worked
first try only because this session had read the source.

## Session transcript (native tool calls, in order)

1. `run_script` — current project `mcp-tests-zinc`, timeline list.
2. `run_script_unsafe` — worker is `ResolvePython.exe`, cwd the main checkout, `uv` and the
   worktree venv found.
3. `run_script_unsafe` — launch `analyze_music(refresh=True)`: envelope in 15.63 s, job
   `analyze_music-e7940b54af0e` running.
4. `run_script_unsafe` — `job` read 0.07 s: `failed`, `beat_this` missing (worktree venv).
5. `run_script_unsafe` — launch `analyze_music(beats=False)`: 0.87 s, running.
6. `run_script_unsafe` — launch `correlate_timeline(beats=…, timeline="Zinc - Set 2 Main",
   audio=…)`: 2.6 s, running.
7. `run_script_unsafe` — poll both: energy completed (~25 s), correlate running at 50 %.
8. `run_script_unsafe` — orphan test: launch, `job` running, `load` → failed, stays failed.
9. `run_script_unsafe` — correlate completed in 29 s; result 18 KB inline with `path`.
10. `run_script_unsafe` — launch `analyze_music(refresh=True)` after `uv sync --extra
    analysis`: 0.98 s, running.
11. `run_script_unsafe` — sleep 50 s, poll: completed after 36 s on CUDA.

Bridge log: `%TEMP%\resolve-mcp-prototype-bridge.log`.
