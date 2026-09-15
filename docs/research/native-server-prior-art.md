# Prior art since Resolve 21.1: how the ecosystem is positioning around the native server

Research for [#266](https://github.com/danielbaldwin47/resolve-mcp/issues/266), part of the
[#263](https://github.com/danielbaldwin47/resolve-mcp/issues/263) wayfinder. Written 2026-09-15,
seven days after DaVinci Resolve Studio 21.1 shipped (2026-09-08). **The window is one week wide.**
Most questions this ticket asks have no answer yet, and "no evidence" is the finding — recorded as
such rather than filled with inference.

Evidence labels used throughout:

- **[on-box]** — measured on this machine against Resolve Studio 21.1.0.17 on 2026-09-15. The
  strongest tier here: it is the product itself, not a description of it.
- **[primary]** — a first-party artefact read directly (a shipped file, a repository's own source).
- **[press]** — a write-up asserting something with no evidence shown. Treat as a lead, not a fact.
- **[not-found]** — searched for and not located. Named explicitly, because absence is the result.

---

## a. What Blackmagic has said about the server's scope and roadmap

### There is no roadmap statement. [not-found]

No Blackmagic statement about future native MCP tools, about the 14 tools being a first pass, or
about third-party MCP servers, could be found anywhere as of 2026-09-15 — not on
blackmagicdesign.com, not in the shipped documentation, not on the forum.

Two access failures explain the thinness and both should be recorded, because a later session will
otherwise repeat the search:

- **blackmagicdesign.com is a client-rendered single-page app.** The 21.1 press release
  (`https://www.blackmagicdesign.com/media/release/20260908-03`), the Studio 21.1 readme
  (`https://www.blackmagicdesign.com/support/readme/baf7c071c0524fbf8ccc961925c9f443`) and the
  product page all return HTTP 200 with only a nav/footer shell — the article text is injected by
  script. The 2026-09-08 and 2026-09-10 Wayback snapshots have the same shell. Agent fetching cannot
  read these pages; **a human with a browser can**, and that is the only route to the release notes.
- **forum.blackmagicdesign.com returns HTTP 403 to non-browser fetches.** Every thread tried —
  including `t=239823` ("Release of DaVinci Resolve Studio 21.1") and `t=221680` ("Experiences with
  MCP") — refused, with no Wayback copies. **No forum post, staff or otherwise, was read.** Whether
  Blackmagic staff have discussed the MCP roadmap there is unknown, not absent.

### What Blackmagic ships in its own words

The `.mcpb` bundle's `manifest.json` is first-party Blackmagic text and is the closest thing to a
scope statement that exists. Read from
`C:\Program Files\Blackmagic Design\DaVinci Resolve\DaVinciResolve.mcpb` (manifest dated 2026-09-10,
i.e. **updated two days after the 21.1 release**). [on-box]

> "Spend more time being creative by automating repetitive tasks with Claude and DaVinci Resolve"

> "The DaVinci Resolve Studio MCP server saves you time by automating repetitive actions in the
> different pages, or creating new tools and looks, that can be saved and reused when you need them."

Its three stated key capabilities, verbatim:

> "Automate repetitive actions in the different pages via Resolve's scripting engine"
> "Generate and modify custom LUTs and DCTLs (color transforms) from a simple description"
> "Save your favorite workflows as reusable scripts that can be triggered on demand"

Read as a scope statement this is narrow and self-consistent: **automation of repetitive actions via
the scripting engine, plus colour-transform authoring.** Nothing in Blackmagic's own copy claims
editorial judgement, media analysis, or a tool catalog. The phrase "via Resolve's scripting engine"
is the architecture stated outright — the product is the scripting engine with an MCP front door.

The `run_script` description is Blackmagic drawing the boundary itself: [on-box]

> "NEVER use for tasks unrelated to interacting with DaVinci Resolve."

### The CEO quote [press]

> "With AI assistant support, our customers can now ask Claude or ChatGPT to handle repetitive tasks
> such as create highlight edits, organizing media or batch rendering, which frees them up to focus
> on creative decisions," — attributed to Grant Petty, Blackmagic Design CEO.

Source: VP Land,
<https://www.vp-land.com/stories/davinci-resolve-21-1-introduces-claude-and-chatgpt-assistants-that-edit-across-post-throug>
(verbatim in the page's raw HTML, fetched 2026-09-15). Reads as press-release boilerplate and is
almost certainly Blackmagic's own PR text, but **it could not be confirmed against a Blackmagic
page.** Note it repeats the manifest's framing: *repetitive tasks*, freeing the human for *creative
decisions*. If that is Blackmagic's actual position, it is the same split this project already
asserts — the server does the mechanical part, the human (or the agent) decides.

### On Python scripting becoming Studio-only [press, with on-box corroboration]

CineD quotes what it says is the free-edition 21.1 release notes:

> "The Python API was being used to hack studio features into the free version. DaVinci Resolve
> relies on studio license sales to pay for the engineering team."

Source: <https://www.cined.com/davinci-resolve-21-1-released-ai-assistant-integration-via-mcp-individual-hdr-trims-and-python-scripting-moves-to-studio/>
(published 2026-09-09). **Press-only** — CineD's transcription of a page that could not be opened
directly.

The shipped 21.1 scripting README does **not** say "Studio-only" in those words. What it says
is: [on-box, `%PROGRAMDATA%\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting\README.md`]

> "In DaVinci Resolve Studio, Preferences > System > General, you can configure: External scripting,
> i.e. whether external scripts can connect to Resolve (None, Local, or Network)."

and, in "Studio and AI Scripting APIs":

> "API calls can return with a False status (or an appropriate error status) when: the function
> references a Studio function from the free DaVinci Resolve version."

So the gating is **implicit in the shipped docs** (the external-scripting preference is described as
a Studio preference) and **explicit only in press**. Mark any downstream claim accordingly: the free
edition losing external scripting is well-corroborated but not verified at a primary source here.

### The 21.1 scripting changelog [on-box]

`Support\Developer\Scripting\CHANGELOG.md`, "Last Updated: 1 Sep 2026", section `## 21.1`:

> "Python 2 is no longer supported."
> "Built-in Python support for script menu and console scripts."
> "Overloaded function signatures are deprecated."

The press "20 new scripting APIs" list (CineD, digitalproduction.com) matches this changelog's
*Added* section in substance — presets, multicam create/flatten/SmartSwitch, auto-align, transitions,
fades, speed, audio normalisation, `GetTranscription` with speaker and per-word timing, media clone,
audio mappings, output blanking. The count "20" is press framing; the changelog does not number them.

### A gap worth naming: the changelog tool cannot describe 21.1 [on-box]

The native server's own `get_whats_new` — whose purpose is "to learn about features and changes
introduced after your training cutoff" — returns entries up to **21.0.4 (2026-08-05)** and then:

> "No changelog entries found after version=21.0.4 date=21.0.4. You are up to date with DaVinci
> Resolve 21.1."

There is no 21.1 entry. An agent that follows the server's own instruction ("ALWAYS call
`get_whats_new`") learns nothing about the release it is running on, including the MCP server's own
existence. This is a first-week defect, not a design statement — but it is evidence that the native
surface is young.

---

## c. Patterns for coexisting with the native server

### Two simultaneous attachments to one Resolve instance work. [on-box — decisive]

This is the load-bearing question for any "beside" option, and it was tested directly rather than
searched for. Through the native server's `run_script_unsafe`, a subprocess of this repo's
python.org 3.12 venv attached to Resolve via `fusionscript.dll` while the native server held its own
live handle:

```
{"native_before": {"version": "21.1.0.17", "product": "DaVinci Resolve Studio"},
 "external_stdout": "21.1.0.17\nmcp-tests-zinc",
 "rc": 0,
 "native_after": "21.1.0.17"}
```

Both handles were live across the same call: the external interpreter read the version and the
current project name (`mcp-tests-zinc`), and the native server's handle still answered afterwards.
**Resolve 21.1 tolerates concurrent external script clients.** Nothing here proves it is safe under
*write* contention — two servers mutating the same timeline at once was not tested and remains an
open risk — but the attach itself is not exclusive.

Corroborating mechanism, from the shipped README: [on-box]

> "DaVinci Resolve Studio and Fusion Studio scripting listens on port 1144 (registered with IANA for
> this purpose). On successful connection, the return connection is dynamically allocated in the
> 49152..65535 range."

A listening port with dynamically allocated return connections is a multi-client design. No document
found states a concurrency limit. [not-found]

### The native server does not hold a persistent attach. [primary]

From `server/index.js` inside `DaVinciResolve.mcpb`:

```js
// Thin MCP wrapper that proxies to the ResolveMCP binary.
// The binary is only spawned on the first tools/call, and killed after an idle timeout.
const IDLE_TIMEOUT_MS = 5 * 60 * 1000; // Kill binary after 5 min of inactivity
const INIT_TIMEOUT_MS = 30 * 1000;
```

The wrapper spawns `ResolveMCP.exe` lazily and kills it after five idle minutes. The native server is
therefore an intermittent client, not a permanent one — which is why a second long-lived server is
unlikely to collide with it at connect time.

### The native tool list is generated, not hardcoded. [on-box]

The `.mcpb` wrapper builds its advertised tools by running the binary:

```js
const result = spawnSync(BINARY_PATH, ['--dump-tools'], { timeout: 10000 });
```

`ResolveMCP.exe --help` confirms the flag set — `--test`, `--dump-tools`, `--pretty` — and
`--dump-tools` returns exactly **14 tools**: `launch_resolve`, `get_resolve_status`, `get_whats_new`,
`get_scripting_api`, `search_scripting_api`, `run_script`, `run_script_unsafe`, `get_scripting_docs`,
`list_dctls`, `list_luts`, `update_dctl`, `delete_dctl`, `delete_lut`, `generate_lut`. **This is the
only verification of the "14 tools" figure found anywhere** — no press source states it, and no
Blackmagic page could be read to confirm it. The tool list moving with the binary means it can grow
in a point release without any announcement; a future session should re-run `--dump-tools` rather
than trust this document's list.

Binary locations, from the same wrapper: `%PROGRAMFILES%\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe`,
`/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolveMCP`,
`/opt/resolve/bin/ResolveMCP`, overridable by `RESOLVE_MCP_PATH`.

### Measuring the two shapes: catalog vs escape hatch [on-box — no prior art found]

The ticket asks whether anyone has measured the agent-experience difference between a tool catalog
and a documented escape hatch. **No such measurement was found for Resolve or for MCP generally.**
[not-found] So it was measured here, on the one axis that is cheap and objective — the context an
agent pays at connect, before doing any work:

| Surface | Tools advertised | Schema payload (minified JSON) |
| --- | --- | --- |
| Native `ResolveMCP.exe` (`--dump-tools`) | 14 | 11,001 bytes |
| This project (`build_server()`, 43 `@tool`s) | 43 | 67,473 bytes |

Roughly **6x the connect-time context for 3x the tools** — this project's schemas are richer per
tool, not just more numerous. The escape hatch's cost is deferred instead: `get_scripting_api`
returns the full `DaVinciResolveScript.pyi`, which is **119,237 bytes** on disk (~30k tokens), and
`get_scripting_docs` can return a 33,346-byte README. That deferred cost is exactly why
`search_scripting_api` exists — its own description says so:

> "Searches the scripting API stubs and returns the matching types and functions with their
> descriptions, so the whole stub does not have to be fetched with `get_scripting_api`."

The honest reading: a catalog front-loads a fixed cost and caps the ceiling of what can be done; an
escape hatch charges near-nothing at connect and a variable, potentially larger, amount per task —
and it charges it *again* in every session that does not cache. Neither number says which produces
better edits. That is the measurement nobody has, here included.

---

## d. Documented pitfalls

Each of these is a constraint a "rebuild on the native server" option would inherit.

**The 60-second ceiling is hard.** [on-box] Both `run_script` and `run_script_unsafe` declare:

> `"timeout": {"type": "integer", "description": "Timeout in seconds (default: 10, max: 60)"}`

Sixty seconds is the maximum, not a default to raise. Every analysis path in this repo — stem
separation, transcription, beat grids — exceeds it by orders of magnitude, so any native-substrate
design needs its own out-of-band job model regardless. `launch_resolve` carries a separate 60-second
wait, and the `.mcpb` wrapper fails queued requests if the binary does not initialise in 30 seconds.

**The sandbox removes builtins, not just modules.** [on-box] Probed directly inside `run_script`:

| Probe | Result |
| --- | --- |
| `os`, `sys`, `pathlib`, `subprocess`, `socket` | `PermissionError` |
| `json`, `math`, `re`, `time`, `datetime` | available |
| `open` | `NameError` — absent from builtins |
| `numpy` | absent (no `pip` in ResolvePython) |

`PermissionError` on import is a deliberate, catchable signal; `open` being a `NameError` means the
sandbox strips builtins rather than guarding them. `run_script_unsafe` lifts all of it and is the
documented route to the filesystem, network and subprocesses.

**The bundled interpreter has no package manager.** [on-box, shipped README]

> "DaVinci Resolve 21.1 and above includes a custom Python 3.14 distribution... `import
> DaVinciResolveScript` works out-of-box. No need to set `RESOLVE_SCRIPT_API`, `RESOLVE_SCRIPT_LIB`
> or `PYTHONPATH`." / "`pip`, `TK` and `IDLE` are not supported."

This cuts both ways for ADR 0001. The attach-crash problem that ADR exists for is solved by
Blackmagic shipping an interpreter that attaches out of the box — but that interpreter cannot install
numpy, torch, faster-whisper or anything else this repo's analysis depends on. External interpreters
(python.org 3.6+, with the three environment variables) remain documented and, per the coexistence
test above, still work in 21.1.

**Studio gating.** [on-box + press] See section (a). The external-scripting preference is documented
as a Studio preference, and the free edition is reported to have lost the Python API in 21.1.
Supporting the free edition is already out of scope for #263.

**No `run_script` retry or handle-death semantics are documented.** [not-found] Nothing in the
manifest, the tool schemas or the shipped README describes what happens to the injected `resolve` and
`project` variables when Resolve restarts mid-session. This repo's `tools/envelope` handle-death
retry has no native counterpart that could be found.

**No account of the native server failing in practice exists yet.** [not-found] Across the press and
hands-on searches, **no source mentioned the 60-second timeout, the sandbox, connection problems, or
running the native server alongside another MCP server.** Every pitfall above is first-hand from this
box, not corroborated by anyone else's experience.

---

## e. Positioning options

Stated as options with the evidence each way. No recommendation — that is #263's to make.

The standing constraint from #263, in the human's words: *"not married to the architecture, only the
concept; rework must earn its place by deleting code or unlocking a workflow, not by matching
coverage claims."* Every option below is scored against that, not against tool counts.

### Option 1 — Beside: keep this server, let the native one own the scripting hatch

**For.** Coexistence is proven, not assumed: two attachments to one 21.1 instance were live
simultaneously [on-box], and the native server is an intermittent client that dies after five idle
minutes [primary], so it does not hold the instance. The native server's declared scope — "automate
repetitive actions... via Resolve's scripting engine" plus LUT/DCTL authoring [on-box manifest] —
does not overlap this project's centre of gravity at all: stem separation, transcription, beat grids,
correlation and the cut/titles document contracts have no native counterpart and could not have one
inside a 60-second sandbox. Blackmagic's own framing ("repetitive tasks... frees them up to focus on
creative decisions" [press]) puts *decisions* outside its server, which is where this project lives.
This option deletes nothing, so it also risks nothing.

**Against.** It deletes no code, which is precisely what the standing preference asks rework to do.
It leaves this project carrying its own attach, interpreter guard and connection lifecycle — the
fragile parts per ADR 0001 — while Blackmagic now ships an interpreter that attaches out of the box
[on-box]. Two servers mutating one timeline concurrently was **not** tested, and nobody has published
an account of doing it [not-found]; the coexistence proof covers reads, not write contention. And the
native tool list is generated by the binary [on-box], so it can grow into this project's territory in
a point release with no announcement — a roadmap risk that cannot be sized, because no roadmap
statement exists [not-found].

### Option 2 — On top: rebuild this project's logic to run through the native server

**For.** It deletes the most dangerous code in the repo — the direct attach, `interpreter.py`, the
loader path resolution and ADR 0001's whole problem class — because Blackmagic's bundled Python 3.14
attaches with no environment variables at all [on-box]. It inherits a self-documenting API surface
(`search_scripting_api`, `get_scripting_docs`, a 119 KB stub kept current by Blackmagic) that this
project would otherwise have to track by hand through every release. `run_script_unsafe` can
subprocess this repo's 3.12 venv — verified in #263 and again here, where it ran an external attach
in a single call — so the heavy analysis need not live inside the sandbox.

**Against.** The 60-second ceiling is hard and applies to *both* script tools [on-box]; every
analysis path here exceeds it, so the job model survives the rebuild and now spans a process boundary
it did not before. The native server is one week old and already has a visible defect — its own
changelog tool cannot describe 21.1 [on-box]. Depending on it means depending on a surface with no
published roadmap, no compatibility promise, and a tool list that is regenerated per build. The
`.mcpb` wrapper kills the binary after five idle minutes [primary], which is a lifecycle this
project's long-running jobs would have to survive. And no one anywhere has published an account of
building on it [not-found] — this would be first-mover on a one-week-old proprietary surface.

### Option 3 — Instead: retire this server, use the native one directly with agent docs

**For.** Maximum deletion: the entire `src/` tree in exchange for `docs/agents/*` teaching Claude to
drive `run_script`. Blackmagic's stated capabilities plus a general Python hatch can reach every
Resolve API this project wraps — the 43 tools here are, at the API level, wrappers over calls the
hatch can make directly. The connect-time context cost drops 6x, from 67,473 bytes to 11,001
[on-box].

**Against.** It deletes the parts that are not wrappers: stem separation, faster-whisper
transcription, beat grids, applause detection, framing-delta catalogs — none of which are Resolve API
calls, none of which fit in 60 seconds, and none of which ResolvePython can even import, having no
pip and no numpy [on-box]. The measured "6x cheaper" is only the connect-time half of the ledger; the
escape hatch defers cost to `get_scripting_api` (119,237 bytes) and repays it per session
[on-box]. The concept — *the server measures; Claude decides* — is the measuring half, and the
measuring half is exactly what has no native path.

### Option 4 — Beside, narrowed: cede the API-wrapper tools, keep only what measures

**For.** This is the only option that both deletes code and keeps the concept. The wrapper tools —
timeline reads, media operations, marker read/write, session/project wrappers — are the ones
`run_script` can do directly through a self-documenting API; the analysis, correlation and document
contracts are the ones it cannot. Cutting the catalog toward the measuring half shrinks the 67,473-byte
schema payload [on-box] and reduces the surface that must track Blackmagic's API changes. Coexistence
is proven [on-box], so the two servers can be used in one session, with the native hatch called for
API work and this one for measurement.

**Against.** The split is not clean: `tools/envelope`'s handle-death retry, the cut and titles
document contracts and `correlate_timeline` all sit on top of the wrapper layer, so ceding the
wrappers means either re-expressing them as native `run_script` calls (inheriting the 60-second
ceiling and the process boundary) or keeping the attach anyway and deleting far less than it looks.
It also asks an agent to hold two servers' conventions in one session, and **no evidence exists about
how agents behave with two overlapping MCP servers connected** [not-found]. The boundary — which
tools are "wrappers" — is a judgement this research cannot settle.

---

## What a later session should re-check

- **`ResolveMCP.exe --dump-tools`** — the tool list is generated per build; 14 is a 2026-09-15 reading.
- **The `.mcpb` manifest date** — it was already updated 2026-09-10, two days after release.
- **The forum, in a browser** — `forum.blackmagicdesign.com` 403s every agent fetch. Threads
  `t=239823` and `t=221680` are the two found by search and are unread. A staff roadmap post, if one
  exists, is there.
- **blackmagicdesign.com release notes, in a browser** — the Studio 21.1 readme
  (`.../support/readme/baf7c071c0524fbf8ccc961925c9f443`) is the primary source for the Studio-gating
  language currently held only on press.
- **Write contention** — two servers mutating one timeline at once is untested here and undocumented
  anywhere.
