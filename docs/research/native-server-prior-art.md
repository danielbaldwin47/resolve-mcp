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
existence — while the shipped `CHANGELOG.md` on the same disk does have a `## 21.1` section.
**Independently found by someone else in the same week** — `samuelgursky/davinci-resolve-mcp` issue
[#207](https://github.com/samuelgursky/davinci-resolve-mcp/issues/207): *"The official MCP's
`get_whats_new` response omitted 21.1 while the installed scripting changelog includes it; do not
treat that response as the complete release inventory."* [primary] A first-week defect, not a design
statement — but evidence that the native surface is young, and that its self-documentation cannot yet
be trusted as complete.

---

## b. Third-party servers and their stance after 21.1

Surveyed 2026-09-15 via `gh api search/repositories` (`q='davinci resolve mcp'`, sorted by push
date) and direct repo reads. Tool counts are marked **[counted]** where taken from source or a
`--dump-tools`-style listing, **[claimed]** where taken from the repo's own README.

| Repo | Tools | Stars / last activity | Stance post-21.1 |
| --- | --- | --- | --- |
| [samuelgursky/davinci-resolve-mcp](https://github.com/samuelgursky/davinci-resolve-mcp) | 37 compound / 387 granular [claimed] | 2,820★, pushed 2026-09-14 — **v3.2.2 → v4.5.2 in six days** | **Engaged, explicit, accelerating.** Quoted below. |
| [OSideMedia/oside-resolve-mcp](https://github.com/OSideMedia/oside-resolve-mcp) | fixed pipeline tools, not a catalog | 0★, 16 commits since 2026-09-01 | **Explicitly complementary** — the one project that has drawn the division of labour in its own README. Quoted below. |
| [DigitalWorkflowCompany/resolve-mcp](https://github.com/DigitalWorkflowCompany/resolve-mcp) | 88 tools + 20 resources [**counted** — `@mcp.tool`/`@mcp.resource` decorators, exact match to its claim] | 2★, pushed **2026-06-06** — three months *before* 21.1 | **Silent.** Source of the figure the press misattributes to Blackmagic (below). |
| [fat-tire/resolve](https://github.com/fat-tire/resolve) (Linux container scripts, not a server) | n/a | 298★; issue [#86](https://github.com/fat-tire/resolve/issues/86) opened 2026-09-08 | **Documented it on day zero** — published the `--dump-tools` recipe and the 14-tool list. The only external source found stating the correct count. |
| [ismael-joffroy-chandoutis/comfyui-cinema-pipeline](https://github.com/ismael-joffroy-chandoutis/comfyui-cinema-pipeline) | n/a (pipeline docs) | `docs/12-davinci-resolve-native-mcp.md`, 2026-09-09 | **Wrote a coexistence rule** — capability table plus an explicit division of labour. Quoted in (c). |
| [RajanthaR/resolve-mcp-burn-bench](https://github.com/RajanthaR/resolve-mcp-burn-bench) | n/a (benchmark) | 0★, created and pushed 2026-09-14 | **Measured the native server's cost** instead of competing. The one published measurement found; quoted in (c). |
| [barckley75/resolve-claude-mcp](https://github.com/barckley75/resolve-claude-mcp) | 52 [claimed] | 360★, no commits since 2026-08-01 | **Silent.** |
| [apvlv/davinci-resolve-mcp](https://github.com/apvlv/davinci-resolve-mcp) | README says 48; source has **14** tools + 7 resources [counted] | 77★, pushed 2026-04-07 | **Silent.** |
| [hoyt-harness/davinci-mcp-professional](https://github.com/hoyt-harness/davinci-mcp-professional) | "6 kernel + 289 domain" [claimed] | 25★, pushed 2026-09-03 — *before* 21.1 | **Silent.** |
| [flamexnreal/davinci-resolve-ai-bridge-mcp](https://github.com/flamexnreal/davinci-resolve-ai-bridge-mcp) | 44–45 [counted] | 23★, pushed 2026-09-07 — the day before | **Silent.** |
| [Tooflex/davinci-resolve-mcp](https://github.com/Tooflex/davinci-resolve-mcp) | 32 [counted, matches claim] | 18★, pushed 2026-07-25 | **Silent.** |
| [mhadifilms/dvr](https://github.com/mhadifilms/dvr) | README says "39+"; source has **84** registrations [counted] | 14★, pushed 2026-09-08 | **Silent** — three releases on 21.1's release day, none referencing it. |
| [wassermanproductions/unofficial-davinci-mcp](https://github.com/wassermanproductions/unofficial-davinci-mcp) | 37 [claimed] | 34★, pushed 2026-07-20 | **Silent.** |
| Long tail (~18 repos, 0–7★: `lordhoell` 440+, `CiprianSpiridon` 334, `Airta-Admin` 218, `2sem` 163, `MDizzleZA` 162, …) | claimed counts only | mostly pre-21.1 | **Silent.** Several new repos appeared in the week after 21.1; none with traction. |

Two cautions the survey itself produced. **Claimed tool counts are unreliable in both directions** —
`apvlv` advertises 48 and ships 14; `mhadifilms/dvr` advertises "39+" and ships 84. And **tool count
is the axis this ecosystem competes on**: five repos advertise 150+ tools, one advertises 440+.
Against that, `DigitalWorkflowCompany`'s 88 is unremarkable, which makes the press's choice of it as
"the" figure arbitrary as well as wrong.

**The pattern at one week.** Of roughly 31 repos found, **three have taken a public position** — one
large project engaging directly, one small project declaring itself complementary, one benchmarking
the native server — and **every other has said nothing**. No project was archived, deprecated, or
announced a pivot; none has raised an issue asking what the native server means for it. The dominant
response to a vendor shipping into your category is, so far, silence.

### The two projects that took a position, quoted

**`samuelgursky/davinci-resolve-mcp` — coexistence on the same API, and don't trust the native
changelog.** From [issue #207](https://github.com/samuelgursky/davinci-resolve-mcp/issues/207):
[primary]

> "The official MCP and community server can access the same underlying Resolve API. A missing
> dedicated action is different from an impossible workflow... Proposed native replacements need
> behavior tests, not just method detection."

> "The official MCP's `get_whats_new` response omitted 21.1 while the installed scripting changelog
> includes it; do not treat that response as the complete release inventory."

The second is **independent corroboration of the `get_whats_new` gap measured on this box** (see (a))
— two parties found it separately within a week. The project's answer was to absorb rather than
retreat: PRs #204–#233 added 21.1 scripting coverage (multicam, transitions, DCTL encryption), and a
2026-09-13 release is titled *"ask the server what the native Resolve API contains"* — it built its
own version of the native server's `search_scripting_api`. Its README also documents the Studio
gating with a measurement and an honest caveat: [primary]

> "**Resolve 21.1 moved Python scripting to Studio.** On free 21.1 the Scripts menu no longer lists
> `.py` files at all (reported on Fedora 44 in #203...). Whether the Console still runs Python there
> is unconfirmed, so treat the bridge as a 21.0.x path until that is measured."

**`OSideMedia/oside-resolve-mcp` — the explicit "not general remote control" position.** From its
README: [primary]

> "This is deliberately NOT general Resolve remote control. Blackmagic ships its own MCP server with
> Studio 21.1... The two do different jobs. Reach for Blackmagic's for API lookup, the changelog past
> your training cutoff, and ad-hoc read-only probing. Reach for THIS one for the handoff — a fixed
> pipeline gets fixed tools..."

This is the "narrow to what the hatch does badly, cede the rest" position, already stated by someone
else, one week in, with no audience (0★). It is evidence that the position is *reachable*, not that
it works.

### The "88 tools, 20 resources" misattribution, traced

The figure's only primary home is `DigitalWorkflowCompany/resolve-mcp`'s own GitHub description,
read verbatim on 2026-09-15: [primary]

> "MCP server providing full API coverage for DaVinci Resolve 21 — 88 tools and 20 resources for
> project management, timeline editing, media import, color grading, rendering, export, and
> composite workflows"

A 2★ repo last touched 2026-06-06. Press outlets reattributed it to Blackmagic's native server:

- **byteiota** — <https://byteiota.com/davinci-resolve-21-1-mcp-server/> — *"Resolve 21.1's MCP
  server exposes 88 tools across every major workspace... There are also 20 read-only resources
  covering live system state."* No citation. **Wrong.** [press]
- **explainx.ai** — <https://explainx.ai/blog/davinci-resolve-21-1-mcp-server-ai-agents-2026> —
  hedges it as *"Community documentation for Resolve's MCP layer describes on the order of 88
  callable tools"*, and elsewhere correctly separates Blackmagic's server from community ones.
  **Partly self-correcting.** [press]
- **cutsio** — HTTP 403 to every fetch; **unverified either way**, and should not be listed as part
  of the cluster without a browser check. [not-found]
- **kompozy** — fetched successfully; the page does **not** contain the figure. **Drop it from the
  known-wrong list.** It is explicit that AI control is Studio-only and calls the MCP workflows
  "early-stage". [press]

Neither CineD nor digitalproduction.com carries the figure. The correct count is 14, verified by
running the binary (see (c)) and independently listed by `fat-tire/resolve#86`.

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
found states a concurrency limit — external scripting is a three-way preference (None / Local /
Network), not a connection count. [not-found] (Note for anyone carrying older notes: the scripting
attach port is **1144**, not 15000; 15000 belongs to Remote Grading / collaboration.)

**The one documented failure mode is a wedge, not contention.**
[`samuelgursky/davinci-resolve-mcp#172`](https://github.com/samuelgursky/davinci-resolve-mcp/issues/172):
a headless (`-nogui`) instance that never became scriptable "holds the singleton, so the GUI cannot
start either" — `scriptapp('Resolve')` returns `None` indefinitely and recovery needs `kill -9`.
[primary] So the attach point behaves like a singleton *resource* even though it accepts multiple
clients: the risk is a stuck holder, not two healthy clients.

### Someone has already published a division of labour. [primary]

`ismael-joffroy-chandoutis/comfyui-cinema-pipeline`, `docs/12-davinci-resolve-native-mcp.md`, dated
2026-09-09 — a side-by-side capability table of the native server against
`samuelgursky/davinci-resolve-mcp` 2.223.0, ending in a rule:

> "A working rule: use the native server for exploration, short scripts, DCTL and LUT work, and for
> reading the documentation of the version you actually run. Use the community server when you want
> a typed tool with argument checks, media analysis, batch jobs or `.drx` files. Keep anything
> repeatable in a versioned script; neither server replaces that."

Its table assigns to the community server exactly the columns this project occupies — "Guardrails on
destructive operations, operation traces", "Media analysis, batch CLI, headless edit loop" — and
marks the native server "no" on both. Its one corroboration is `OSideMedia/oside-resolve-mcp`'s
README (quoted in (b)), which reaches the same split independently. **Two authors, no audience, one
week** — this is the strongest published evidence for a coexistence split, and it is thin.

The same document reports the counter-position, secondhand: [press]

> "It's built in and maps everything in Resolve and will be updated with each release of Resolve.
> I'll take that over an external dependency every day of the week."

attributed to Brent Schooley, dated 2026-09-09, and described as someone who "had built his own
164-action Resolve bridge with Codex before 21.1". If accurate, that is a third party abandoning his
own catalog for the native hatch — the clearest datapoint *against* building a catalog at all. It
could not be traced to a primary post and is quoted here at press tier only.

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
`list_dctls`, `list_luts`, `update_dctl`, `delete_dctl`, `delete_lut`, `generate_lut`.

**No press source states 14, and no Blackmagic page could be read to confirm it.** The one external
corroboration is [`fat-tire/resolve` issue #86](https://github.com/fat-tire/resolve/issues/86),
opened 2026-09-08 on Linux, which publishes the same recipe and the same fourteen names: [primary]

> "Also-- MCP!  You can get the full tool list with: `/opt/resolve/bin/ResolveMCP --dump-tools >
> ~/tools.json`"

The tool list moving with the binary means it can grow in a point release without any announcement;
a future session should re-run `--dump-tools` rather than trust this document's list.

Binary locations, from the same wrapper: `%PROGRAMFILES%\Blackmagic Design\DaVinci Resolve\ResolveMCP.exe`,
`/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolveMCP`,
`/opt/resolve/bin/ResolveMCP`, overridable by `RESOLVE_MCP_PATH`.

### Measuring the two shapes: catalog vs escape hatch

The ticket asks whether anyone has measured the agent-experience difference between a tool catalog
and a documented escape hatch. Three things exist, and they do not agree.

**1. Anthropic's own argument, for the hatch.** "Code execution with MCP: Building more efficient
agents", 2025-11-04, <https://www.anthropic.com/engineering/code-execution-with-mcp>: [primary]

> "Tool definitions occupy more context window space, increasing response time and costs."

with a worked example claiming a reduction "from 150,000 tokens to 2,000 tokens—a time and cost
saving of 98.7%". This is Anthropic's own illustrative figure, not an independent benchmark, and it
measures tokens, not whether the agent did the job well. The MCP client guidance agrees on the
direction — <https://modelcontextprotocol.io/docs/develop/clients/client-best-practices>: [primary]

> "Loading every tool definition into the model's context window upfront wastes tokens, increases
> latency, and degrades model performance."

It names progressive discovery (a `search_tools` meta-tool, full schemas on demand, with a "1%–5% of
the context window" threshold) and code-mode calling as the two remedies. **The native Resolve server
is a textbook implementation of both**: 14 tools, `search_scripting_api` as the discovery tool, and
`run_script` as code mode.

**2. The one real measurement of the Resolve hatch, and it points the other way.**
[`RajanthaR/resolve-mcp-burn-bench`](https://github.com/RajanthaR/resolve-mcp-burn-bench), created
2026-09-14 — a reproducible benchmark of how fast an agent burns its usage allowance driving Resolve
through the native server. Its stated sample result: [primary, but **n=1, self-reported, 0★**]

> "**Sample result (2026-09-14):** 46% of a 5-hour Codex limit consumed in **8m39s** (~5.3% / min)
> to complete a 7-phase, 40-tool-call edit. In other words, **a full 5-hour limit ≈ 19 minutes of
> MCP-driven editing.**"

And its diagnosis names the escape hatch as the cause:

> "Blackmagic's MCP server doesn't expose tidy 'edit the timeline' tools. It exposes `run_script`
> (Python), `search_scripting_api`, `get_scripting_api`, `generate_lut`, etc. So the agent spends its
> budget **discovering the API and writing Python**, not rendering. That makes MCP editing unusually
> token-hungry."

It also reports that "retries dominate cost. Failed tool calls get fully re-reasoned. A run with
many retries can cost 2–3× a clean run for the same output." Treat every number as one unreplicated
run by an unknown author — but note that it is the *only* end-to-end measurement anyone has
published, and it contradicts the connect-time argument by moving the cost to where the work happens.

**3. What nobody has.** No independent, controlled benchmark of *task accuracy* — as opposed to token
cost — for a narrow catalog against a code-execution hatch, in Resolve or in MCP generally.
[not-found] The question the ticket asks is genuinely open.

Against that, the connect-time half of the ledger was measured here, because it is cheap and
objective — the context an agent pays before doing any work:

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

The honest reading, with the burn bench alongside: a catalog front-loads a fixed cost (here 67,473
bytes) and caps the ceiling of what can be done; an escape hatch charges ~6x less at connect and then
charges a variable, apparently much larger, amount per task — the agent pays in API discovery and
Python authoring, and pays again on every retry, in every session that does not cache. **The 6x
saving at connect is real and small; the burn bench suggests the per-task cost dwarfs it.** Neither
number says which produces better edits.

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

**ADR 0001's crash is now documented by other people, with a cause.** [primary] This is new
corroboration for a decision this repo made from a single crash:

- [`samuelgursky/davinci-resolve-mcp#158`](https://github.com/samuelgursky/davinci-resolve-mcp/issues/158)
  — *"Blackmagic's `fusionscript.dll` on Windows is compiled against Python 3.10–3.12 C ABI. When
  imported under Python 3.13, Windows immediately triggers an Access Violation (0xC0000005)."* No
  traceback; the process dies. Corroborated independently by `Tooflex/davinci-resolve-mcp#4`.
- [`samuelgursky/davinci-resolve-mcp#26`](https://github.com/samuelgursky/davinci-resolve-mcp/issues/26)
  — Resolve's loader picks the highest registry-registered Python even when a correct venv is active,
  causing the same crash.
- `WheheoHu/pybmd#11` — **uv-managed venvs lack `python3.dll` on the search path**, producing the
  same access violation; the reported fix is setting `PYTHONHOME`.

The shipped README asks only for "Python >= 3.6 64-bit" and says nothing about the 3.13 ABI break.
The gap between Blackmagic's documented requirement and observed reality is exactly what ADR 0001's
pre-load interpreter guard exists to cover, and the guard's rationale is stronger than when it was
written. It also means a native-substrate rebuild would delete a guard that is still correct for
anyone attaching externally.

**Handle staleness is documented only against third-party bridges.** [primary]
`samuelgursky/davinci-resolve-mcp#112` — *"Free-edition bridge orphans on Windows: `os.getppid()`
never changes, so `serve()` can never detect Resolve exiting."* `barckley75/resolve-claude-mcp#2` —
every call returns "DaVinci Resolve is not running" while Resolve is open. Both are third-party
lifecycle bugs, not native-server behaviour; see the next item for what is unknown about the native
server.

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

### Shared evidence: what third parties did when a vendor shipped into their category

Resolve is not the first. Four cases where a vendor shipped a native MCP server after third parties
already had one, checked directly: [primary]

| Host app | Third party (first) | Vendor's server | What the third party did |
| --- | --- | --- | --- |
| Blender | `ahujasid/blender-mcp`, 2025-03-07, ~22–29k★ | Blender Lab MCP, 2026-04-28 (<https://www.blender.org/lab/mcp-server/>), needs Blender 5.1+ | **Kept going; renamed defensively.** Commit `33de875`, 2026-09-01: *"rename: change name to mcp for blender to avoid confusion with official blender foundation."* Scope unchanged. |
| Figma | `GLips/Figma-Context-MCP` ("Framelink"), 2025-02-13, ~14.4k★ | Dev Mode MCP server, beta 2025-06-04 (<https://www.figma.com/blog/introducing-figma-mcp-server/>), behind a paid Dev seat | **Kept competing**, positioned as the free path. Still shipping (v0.13.2, 2026-06); changelog never mentions the official server. |
| Unreal | `kvick-games/UnrealMCP`, `chongdashu/unreal-mcp`, both ~2025-03 | Unreal MCP plugin, **Experimental** in UE 5.8 (<https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor>) | **Kept competing** — the official plugin is experimental and 5.8-only, leaving 5.3–5.7 unserved. README never mentions Epic's. |
| Photoshop | `alisaitteke/photoshop-mcp`, 118 tools, ~2026-01 | "Adobe for Creativity" connector, GA 2026-04-28, 50+ tools (<https://blog.adobe.com/en/publish/2026/04/28/adobe-for-creativity-connector>) | **Kept competing on tool count** — pushed as recently as 2026-09-15, advertising 118 against Adobe's ~50. |

**The meta-finding: in every confirmed case the third party kept going, and none pivoted scope.** The
only defensive action observed anywhere was cosmetic (Blender's rename). No case of dormancy was
found — but all four vendor servers are ≤5 months old, so "nobody has folded yet" may only mean
"not yet". Useful negative control: no vendor-native Ableton MCP exists as of 2026-09-15 despite
`ahujasid/ableton-mcp` running since 2025-03-19.

Read carefully, this evidence is weaker than it looks for *this* decision. Every one of those
third-party projects is a **tool catalog competing with another tool catalog**, and in three of the
four cases the vendor's offering was gated (paid seat), experimental, or version-limited — a gap to
sit in. Blackmagic's server is none of those: it ships free with Studio, is not experimental, and is
updated with the app. The precedents say what catalogs do; they do not say what a measuring layer
should do.

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
statement exists [not-found]. The analogy cases all show third parties surviving a vendor's entry —
but every one of them had a gap to sit in (a paid seat, an experimental plugin, a version floor) that
Blackmagic's free, shipping, app-updated server does not leave.

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
project's long-running jobs would have to survive. The one published cost measurement is against it:
the burn bench attributes heavy token burn precisely to an agent "discovering the API and writing
Python" through this surface, and reports retries costing 2–3× a clean run [n=1]. And no one anywhere
has published an account of *building* on it [not-found] — this would be first-mover on a one-week-old
proprietary surface. Note also that the deletion is smaller than it first appears: the interpreter
guard ADR 0001 describes is now *better* evidenced than when it was written (`#158`, `#26`,
`pybmd#11`), so deleting it is only safe if nothing external attaches any more.

### Option 3 — Instead: retire this server, use the native one directly with agent docs

**For.** Maximum deletion: the entire `src/` tree in exchange for `docs/agents/*` teaching Claude to
drive `run_script`. Blackmagic's stated capabilities plus a general Python hatch can reach every
Resolve API this project wraps — the 43 tools here are, at the API level, wrappers over calls the
hatch can make directly. The connect-time context cost drops 6x, from 67,473 bytes to 11,001
[on-box]. It has the one named human precedent: Brent Schooley, reported to have built a 164-action
Resolve bridge with Codex before 21.1, is quoted abandoning it — *"It's built in and maps everything
in Resolve and will be updated with each release of Resolve. I'll take that over an external
dependency every day of the week."* [press, untraced to a primary post]. "Updated with each release"
is the real argument: Blackmagic's stubs track its own API for free, and this repo's wrappers do not.

**Against.** It deletes the parts that are not wrappers: stem separation, faster-whisper
transcription, beat grids, applause detection, framing-delta catalogs — none of which are Resolve API
calls, none of which fit in 60 seconds, and none of which ResolvePython can even import, having no
pip and no numpy [on-box]. The measured "6x cheaper" is only the connect-time half of the ledger; the
escape hatch defers cost to `get_scripting_api` (119,237 bytes) and repays it per session
[on-box], and the burn bench puts a number on that repayment: 46% of a five-hour Codex allowance for
a single 40-call edit, ~19 minutes of editing per limit [n=1]. The concept — *the server measures;
Claude decides* — is the measuring half, and the measuring half is exactly what has no native path.

### Option 4 — Beside, narrowed: cede the API-wrapper tools, keep only what measures

**For.** This is the only option that both deletes code and keeps the concept. The wrapper tools —
timeline reads, media operations, marker read/write, session/project wrappers — are the ones
`run_script` can do directly through a self-documenting API; the analysis, correlation and document
contracts are the ones it cannot. Cutting the catalog toward the measuring half shrinks the 67,473-byte
schema payload [on-box] and reduces the surface that must track Blackmagic's API changes. Coexistence
is proven [on-box], so the two servers can be used in one session, with the native hatch called for
API work and this one for measurement. **Two independent parties have already published this exact
split**: `comfyui-cinema-pipeline`'s working rule assigns "media analysis, batch CLI, headless edit
loop" and destructive-operation guardrails to the community server and marks the native server "no"
on both, and `OSideMedia/oside-resolve-mcp` states it as policy — *"deliberately NOT general Resolve
remote control... The two do different jobs."* The burn bench supports the same shape from the cost
side: the agent's budget goes to *"discovering the API and writing Python"*, which is exactly the
work a narrow measuring tool removes.

**Against.** The split is not clean: `tools/envelope`'s handle-death retry, the cut and titles
document contracts and `correlate_timeline` all sit on top of the wrapper layer, so ceding the
wrappers means either re-expressing them as native `run_script` calls (inheriting the 60-second
ceiling and the process boundary) or keeping the attach anyway and deleting far less than it looks.
It also asks an agent to hold two servers' conventions in one session, and **no evidence exists about
how agents behave with two overlapping MCP servers connected** [not-found]. The boundary — which
tools are "wrappers" — is a judgement this research cannot settle. And the one project that has
actually taken this position, `OSideMedia/oside-resolve-mcp`, has 0 stars a week in: the position is
reachable, but nothing yet shows it is right.

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
- **The silent majority** — 28 of ~31 third-party repos have said nothing about 21.1. Whether that
  becomes engagement or dormancy is the single most informative thing a re-survey in a month would
  show. `samuelgursky/davinci-resolve-mcp` (release cadence) and `OSideMedia/oside-resolve-mcp`
  (whether the complementary position attracts anyone) are the two to watch.
- **`resolve-mcp-burn-bench`** — the one end-to-end cost measurement is n=1 by an unknown author. If
  it gains contributed data points it becomes the best evidence in this document; if it stays at one
  run, discount it accordingly.
