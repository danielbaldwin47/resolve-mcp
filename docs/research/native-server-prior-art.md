# Prior art since Resolve 21.1: how the ecosystem is positioning around the native server

Research for [#266](https://github.com/danielbaldwin47/resolve-mcp/issues/266), part of the
[#263](https://github.com/danielbaldwin47/resolve-mcp/issues/263) wayfinder. Written 2026-09-15,
seven days after DaVinci Resolve Studio 21.1 shipped (2026-09-08). **The window is one week wide.**
Several questions this ticket asks have no answer yet, and "no evidence" is the finding — recorded
as such rather than filled with inference. Where an answer does exist it is usually one project's
or one run's, and is labelled accordingly.

The short version: Blackmagic has stated no roadmap and documented the server in exactly one
place with any substance. The ecosystem has barely reacted — one repository repositioned in
prose, most said nothing, and the project the press credits with "88 tools" has been dead since
June. The two servers that *did* move went opposite ways on the central question: Blackmagic
sells a sandboxed escape hatch, and the largest community server deleted its own escape hatch on
principle three days later. The one thing two unrelated projects converged on is the gap the
native server leaves — measured behaviour, because a type signature cannot express a lie.

Evidence labels used throughout:

- **[on-box]** — measured on this machine against Resolve Studio 21.1.0.17 on 2026-09-15. The
  strongest tier here: it is the product itself, not a description of it.
- **[primary]** — a first-party artefact read directly (a shipped file, a repository's own source).
- **[primary, proxy-retrieved]** — first-party content that could only be reached through a
  third-party text-extraction proxy because the host blocks agent fetches. The words are the
  source's; the delivery path is not. Used only for `forum.blackmagicdesign.com`.
- **[press]** — a write-up asserting something with no evidence shown. Treat as a lead, not a fact.
- **[not-found]** — searched for and not located. Named explicitly, because absence is the result.

This file was written in two passes on the same day. The second pass reached the Blackmagic forum
and the third-party repositories, so a few of the first pass's `[not-found]` results are now
answered and several `[press]` quotes are now `[primary]`. Where that happened the correction is
in place rather than appended, and the route that worked is recorded so it can be repeated.

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
- **forum.blackmagicdesign.com returns HTTP 403 to non-browser fetches** (AWS WAF; direct `curl`
  gets a 202 challenge page). Direct fetching failed on every thread tried, and there are no Wayback
  copies. **A second pass reached the forum through the `r.jina.ai` text-extraction proxy and the
  threads were read** — see "Blackmagic staff on the forum" below. The *content* is Blackmagic's
  (author names, staff rank flags, post timestamps and permalink IDs intact); the *retrieval path*
  is third-party, so those quotes are marked **[primary, proxy-retrieved]**. `search.php` and
  `feed.php` refuse even through the proxy, so the board index (`viewforum.php?f=21`) was
  enumerated by hand: thorough for that board, not exhaustive across all boards.

Reading the forum did not turn up a roadmap statement either — it turned up two staff posts that
are substantive about *what shipped*, and silence about what comes next. The searches that came
back empty (`future`, `roadmap`, `next release`, `we will/plan/are working`, `coming`, `more
tools`, `additional tools`, `expand`) are recorded here so a later session does not repeat them.
The one forward-looking claim in circulation — "Blackmagic will close these gaps in future
releases" (explainx.ai, byteiota) — has **no Blackmagic utterance behind it**. [not-found]

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

### Blackmagic staff on the forum [primary, proxy-retrieved]

Three MCP-relevant threads exist on the DaVinci Resolve board. Two carry staff posts.

**Peter Chamberlain** (rank flag "Blackmagic Design"; Resolve product manager), the official 21.1
release-notes thread, Tue Sep 08 2026 4:33 am —
<https://forum.blackmagicdesign.com/viewtopic.php?f=21&t=239823>. The thread is one post with no
replies, so there is no staff follow-up in it. The entire release-notes treatment of the server,
under "General Improvements", is nine words:

> "Native MCP server for interacting with AI assistants."

And under "Notes", the statement that the press paraphrased — here at its source, in full:

> "We have moved the ability to script in Python to the Studio version. The Python API was being
> used to hack studio features into the free version. DaVinci Resolve relies on studio license
> sales to pay for the engineering team. Unlike subscriptions, which have become normal but lock
> people's work up unless you pay monthly, we want to offer a differentiation from charging
> monthly license fees."

**Rohit Singhal** (rank flag "Blackmagic Design"; signature "DaVinci Resolve Software
Development"), thread "AI Assistant - how to?", Tue Sep 08 2026 9:02 am —
<https://forum.blackmagicdesign.com/viewtopic.php?p=1233151#p1233151>. This is the single richest
human statement Blackmagic has made about the server, and it contains two facts that appear in no
other first-party source:

> "To use AI assistants with Resolve, first install the AI tool on your OS, then run "File > Setup
> AI Assistants" in Resolve. It will detect any supported tools already installed and configure
> them automatically.
>
> Supported tools:
> - Claude Code (Anthropic's CLI)
> - Claude Desktop
> - Antigravity (Google)
> - Codex (OpenAI)
> - Grok Build (xAI)
>
> Once set up, your AI assistant can directly interact with your Resolve project — querying
> timelines, running scripts, and more.
>
> Any other tools which support MCP can also be set up by setting up the ResolveMCP executable
> path if needed."

**Five** supported clients, not the press release's three. And the last line is Blackmagic
explicitly blessing arbitrary MCP clients pointed at the binary — the closest thing to an
architectural commitment on the record. **Dwaine Maggart** (staff) confirms in the same thread on
Wed Sep 09 5:51 pm that he verified Google Antigravity against Resolve 21.1 on Windows 10.

A third thread, "DaVinci mcp info" (`t=240062`), is one user pasting the explainx.ai blog whole,
including its "88 tools" claim. **No staff reply and no correction** — so the 88 figure now sits
uncorrected on Blackmagic's own forum. In the same "AI Assistant - how to?" thread a
non-staff user connecting a third-party client to the `ResolveMCP` path reports "it's not working
completely — **only 14 tools**", with a screenshot captioned "14 tools exposed by mcp". User
observation, not a Blackmagic statement, but it corroborates the manifest from a second angle.

### The reference manual is the only real documentation [primary, local]

`C:\Program Files\Blackmagic Design\DaVinci Resolve\Documents\DaVinci Resolve.pdf`, chapter 201
"Workflow Integrations", pp. 4317–4318 — a page and a half, and the only Blackmagic document that
explains the feature at all:

> "You can now control DaVinci Resolve using plain language prompts with your favorite AI assistant
> and the built-in MCP server. DaVinci Resolve Studio supports integration with AI assistants like
> Claude Desktop and Claude Code. This lets you control Resolve through natural language
> conversation. You can ask your AI assistant to organize timelines, create custom looks, create and
> run scripts, and have those actions reflected directly in DaVinci Resolve."

The setup section carries Blackmagic's only published statement about the trust model, and it is
the **opposite** of a sandbox claim:

> "**NOTE:** Any scripts created by the AI assistant share the same access permissions to disk,
> network and other resources as the AI assistant."

The manual's worked prompts are worth reading as a scope statement — "Give me a breakdown of the
current Resolve timeline, with clip count, total duration, offline media, markers", "Add different
colored markers for all cuts per track and export all markers to CSV", "Reframe this timeline for
9:16 vertical". Inventory, settings, conform, delivery. **Not one of them is an editorial
decision.** [primary, local]

Searched and absent from the full extracted manual text: "Model Context Protocol", "ResolveMCP",
"run_script", "run_script_unsafe", any sandbox description, any tool count, and any statement that
Python scripting is Studio-only. [not-found]

### The CEO quote [primary]

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

## b. How third-party servers are positioning

Fourteen third-party Resolve MCP servers were checked by GitHub API metadata, commit history since
2026-09-08, README text and issue threads. **The headline is that the ecosystem has barely
reacted.** Exactly one repository has repositioned itself against the native server in prose. The
two highest-profile projects went opposite ways, and neither said so in its README.

### The "88 tools, 20 resources" project is dead, and was dead before 21.1 shipped [primary]

`DigitalWorkflowCompany/resolve-mcp` — the source of the figure that press coverage attributes to
Blackmagic — as of 2026-09-15:

| | |
| --- | --- |
| Stars | **2** |
| Last push | **2026-06-06**, three months before the native server |
| Commits, ever | **2** |
| Releases | none |
| Activity after 2026-09-08 | **none** — no commit, issue, release or README change |

Its README line 3 is where the number comes from, verbatim:

> "An MCP server providing full API coverage for DaVinci Resolve 21. Exposes 88 tools and 20
> resources for project management, timeline editing, media import, color grading, rendering,
> export, AI analysis … and composite workflows like automated dailies creation."

It targets Resolve **21**, not 21.1, is macOS-only, and never mentions the native server. The
repo has never acknowledged the press attention. This project was already in the #8 survey
(2026-08-04) at ~5 stars, unlicensed, macOS-only, filed under "learn only" — it has not moved
since. **Any downstream use of "88 tools" should carry this provenance**: a two-commit, two-star,
macOS-only repo that stopped moving in June, quoted as if it described Blackmagic's server, and
now sitting uncorrected on Blackmagic's own forum (see `t=240062` above).

### The dominant project raced the release — and removed its own escape hatch [primary]

`samuelgursky/davinci-resolve-mcp`: **2,820 stars** (from ~1,990 in the #8 survey six weeks
earlier), 323 forks, last push 2026-09-14. In the seven days after 21.1 shipped it produced
**142 commits and 53 releases**, v2.212 → v4.5.2. Tool count went 367 → **387 granular, 37
compound**, plus 18 offline. This is not a project going quiet.

Two distinct moves, and they pull in opposite directions:

**1. Race to wrap the new 21.1 scripting API as typed tools.** A contributor (`legionsound`) filed
a wave of tickets on 2026-09-09, the day after release: native multicam creation and flattening
(#211), transitions (#209), speed and fade setters (#208), DCTL validation (#215) and encryption
(#216), output blanking (#213), audio normalisation (#214), twelve read-only controls (#206).
These map one-to-one onto the 21.1 changelog entries — including `MediaPool.CreateMulticamClip`
and `TimelineItem.AddTransition`, the two ceilings the #8 survey recorded as *not exposed*
("native multicam clip creation is NOT exposed", "No transitions API"). **Both of those ceilings
are gone in 21.1**, and the community server had them wrapped within twenty-four hours.

**2. Delete the escape hatch, three days after Blackmagic shipped one.** Release **v3.0.0**
(2026-09-11), published as security advisory `GHSA-vh75-g46q-hgcw`, titled "the server no longer
executes caller-supplied code, and every plugin write is gated". Verbatim:

> "**`script_plugin run_inline`** ran a caller's source directly. Python ran as a subprocess on the
> host, with the user's privileges and a live Resolve handle. Lua ran inside Resolve's Fusion
> engine with `os` and `io` in scope, so `os.execute` reached the shell. … Both actions shipped in
> v2.5.0 as documented features, and the agent guidance recommended `run_inline` for conversational
> queries. They are removed under **the maintainer's policy that this server does not execute
> caller-supplied code, in any form**."

The one place it names the native server as a contrast is `docs/reference/lut-file-controls.md`,
under the heading `## Not ported from the official MCP`:

> "Its `generate_lut` takes a Python function body from the caller and executes it on every lattice
> point. **This server does not execute caller-supplied code**, so authoring here is limited to
> declarative operations already implemented in `utils/cube_lut.py` … `capabilities` says so
> explicitly rather than leaving a caller to guess why `generate` is missing."

**So the two servers have diverged on the central design question of this wayfinder.** Blackmagic
sells the escape hatch with a sandbox around it. The largest community server deleted its escape
hatch on principle and doubled down on a typed catalog with safety gating. Neither is hedging.

### The community server's stated theory of differentiation [primary]

Issue #207, "Track Resolve 21.1 native API and official MCP coverage contributions", opened
2026-09-09 and still open — the clearest positioning statement anyone in this ecosystem has
written down:

> "The official MCP and community server can access the same underlying Resolve API. A missing
> dedicated action is different from an impossible workflow: generic scripting and existing
> alternatives may already cover it. **Proposed native replacements need behavior tests, not just
> method detection.**"

> "The official MCP's `get_whats_new` response omitted 21.1 while the installed scripting changelog
> includes it; **do not treat that response as the complete release inventory.**"

The differentiator it lands on is **measured behaviour, not tool count** — which is the same claim
this project makes with *the server measures; Claude decides*, arrived at independently. Release
v4.0.0 (2026-09-13) is that theory shipped: `api_truth.py` records "behaviours of the Resolve API
measured against a live build rather than read off a signature — calls that return `True` having
done nothing, settings keys silently rejected, methods that are not there at all", and surfaces
them at the callsite. `TimelineItem.CopyGrades` now **refuses** unless the caller acknowledges it:

> "`TimelineItem.CopyGrades` replaces the target's grade wholesale rather than merging, returns
> `True` while doing it, and creates **no** grade version. Applied to a clip carrying hand-work,
> that is unrecoverable loss reported as success — the caller cannot tell it apart from having
> worked."

That is a fact the native `get_scripting_api` stub cannot express, because a type signature cannot
express a lie. It is the strongest existing evidence that "knowledge beside the hatch" is a real
position and not a consolation prize.

It also treats the native server as an **instrument** rather than a rival:
`docs/reference/resolve-211-typed-api.md` records its provenance as "Collected September 9, 2026
from the installed **official MCP's** `get_scripting_api(as_file=True)` on macOS, DaVinci Resolve
Studio 21.1.0.14" — and issue #229 adds `search_api`/`describe_api`/`api_surface` tools whose body
says plainly: "**Your MCP's `search_scripting_api` is the equivalent.**" Every result carries
`referenced_in_this_server`, "so a lookup doubles as a parity check: does the native API have it,
and do we wrap it?" Mirror the native tool, add the coverage metadata the native one cannot have.

### The only repo that names the two-writers hazard [primary]

`OSideMedia/oside-resolve-mcp` (0 stars, 4 tools) repositioned **on launch day** — commit
`df7f875`, 2026-09-08T15:28Z, "docs: point 'what this is NOT' at Blackmagic's own MCP, and say what
it does not replace". Verbatim from its README:

> "**Blackmagic ships its own MCP server** with Studio 21.1 … version-matched to the Resolve you are
> running, and so the owning source for call SHAPES. The two do different jobs. Reach for
> Blackmagic's for API lookup, the changelog past your training cutoff, and ad-hoc read-only
> probing. Reach for THIS one for the handoff — a fixed pipeline gets fixed tools so it runs
> identically every time, and the acceptance gate is the part a general tool cannot give you. The
> first-party stubs document call shapes but none of the BEHAVIOURAL lies this server is built
> around (`AppendToTimeline` still promises 'the list of appended timelineItems' over a silent
> drop) … **While a build is running, treat the general server as read-only: two writers, one
> Resolve.**"

And from the same commit's `CLAUDE.md`:

> "**It does not make this server redundant, and it is not a safe substitute for it.** Its
> `run_script` makes every mistake in the ANTI-PATTERNS below available fresh, and its stubs
> document NONE of them — a type signature cannot express a lie. … It also has no gate … while a
> handoff runs, treat it as read-only — **two writers, one Resolve, and `run_script_unsafe` has
> filesystem and process access with no contract.**"

Two independent projects, with no visible contact, converged on the same sentence: *a type
signature cannot express a lie.* That is the shape of the gap the native server leaves.

### The field, as of 2026-09-15 [primary]

| Project | ★ | Last push | Activity after 09-08 | Names the native server? |
| --- | --- | --- | --- | --- |
| samuelgursky/davinci-resolve-mcp | 2820 | 09-14 | **142 commits, 53 releases** | Not in README; yes in #207 + 2 docs |
| barckley75/resolve-claude-mcp | 360 | 2026-05-14 | none — quiet since May | no |
| hoyt-harness/davinci-mcp-professional | 25 | 09-03 | none | no |
| flamexnreal/davinci-resolve-ai-bridge-mcp | 23 | 09-07 | none | no |
| mhadifilms/dvr | 14 | 09-08 | none | no |
| Iamkewl/Davinci-MCP | 5 | 09-13 | 12 commits | no |
| 2sem/davinci-resolve-lite-mcp | 4 | 08-24 | none | no |
| DigitalWorkflowCompany/resolve-mcp | 2 | **06-06** | none | no |
| sakethramanujam/video-harness | 0 | 09-12 | created 09-09 | no |
| OSideMedia/oside-resolve-mcp | 0 | 09-13 | 22 commits | **yes — full section** |
| RajanthaR/resolve-mcp-burn-bench | 0 | 09-14 | created 09-14 | **yes — is about it** |

**No repository has been archived or deprecated since 2026-09-08**, and only three new ones were
created. Neither a stampede nor a collapse: mostly silence. Every project surveyed uses the same
direct `fusionscript` attach this one does — the native server changed nobody's architecture.

### What 21.1 broke, and nobody has fixed [primary]

Five projects exist specifically to dodge the Studio paywall with an in-Resolve Scripts-menu
bridge (`flamexnreal`, `Airta-Admin`, `2sem`, `MDizzleZA`, `sakethramanujam`). `MDizzleZA` stakes
its whole pitch on it: *"Every other DaVinci Resolve MCP server requires the $295 Studio version…
a feature Blackmagic locks behind the paywall."* **None of the five has updated for the Studio
gating**, and `flamexnreal` (23★) still advertises "Free Version and Studio" on a README last
touched the day before 21.1 shipped.

Only `samuelgursky` measured the breakage, in issue #203 (2026-09-09, Fedora 44, free 21.1) — a
first-hand practitioner report:

> "**Workspace > Scripts** (in Resolve itself) only ever lists `resolve_bridge_canary`. The three
> `.py` files never appear in the menu at all — not grayed out, just absent."

The reporter ruled out Python version, the `Automatic scripted actions` setting, `PYTHONHOME`
contamination and folder placement, and concluded it "smells more like an edition-gating behavior
specific to Python scripts." The maintainer's diagnosis, on issue #219 (2026-09-10):

> "**Resolve 21.1 moved Python scripting to the Studio edition** (issue #203). On free 21.1 the
> Scripts menu no longer lists `.py` files at all, no matter which Python is installed … In that
> case there is currently **no in-app bridge path on free 21.1**."

He closed #203 as *"there was nothing left for this project to do."* **The free-edition segment of
this ecosystem has no path forward and mostly does not know it yet.** Irrelevant to this project
(Studio throughout) but it explains why the field looks quiet: a third of it is dead and
un-updated rather than deliberately silent.

### No official listing exists, for anyone [not-found]

| Directory | Result |
| --- | --- |
| `modelcontextprotocol/servers` README | No DaVinci Resolve or Blackmagic entry |
| Official MCP registry, query `blackmagic` | `"servers": []` — zero results |
| Official MCP registry, query `davinci` / `resolve` | Only community bridges (`2sem`, `Airta-Admin`) |
| Claude connectors directory (`claude.com/connectors`) | No Resolve or Blackmagic listing among 811 |

Blackmagic ships the `.mcpb` inside the installer and publishes it nowhere. Distribution is via
**File > Setup AI Assistants** only. A loose end for a later session: `modelcontextprotocol/servers`
PR #1013 (merged 2025-04-27) did add a Resolve entry to the community list, and the current README
has none — trimmed or migrated, not verified.

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

### Measuring the two shapes: catalog vs escape hatch [on-box, plus prior art]

The ticket asks whether anyone has measured the agent-experience difference between a tool catalog
and a documented escape hatch. **Prior art exists, and it points both ways.** A first pass found
none; a second pass found one Resolve-specific measurement and a body of general MCP evidence.
Both are recorded below the on-box numbers, because the on-box axis is the cheap and objective one
— the context an agent pays at connect, before doing any work:

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
better edits.

#### The one Resolve-specific measurement [primary — single run, unreplicated]

`RajanthaR/resolve-mcp-burn-bench`, created 2026-09-14, six days after the release. Not a server —
a benchmark of the native one, and the only measurement of this question in the domain. Its
diagnosis is the ticket's question restated:

> "Blackmagic's MCP server doesn't expose tidy 'edit the timeline' tools. It exposes `run_script`
> (Python), `search_scripting_api`, `get_scripting_api`, `generate_lut`, etc. So the agent spends
> its budget **discovering the API and writing Python**, not rendering. That makes MCP editing
> unusually token-hungry."

Headline result, verbatim: "**46% of a 5-hour Codex limit consumed in 8m39s** (~5.3% / min) to
complete a 7-phase, 40-tool-call edit. In other words, **a full 5-hour limit ≈ 19 minutes of
MCP-driven editing.**" The call mix is the interesting part: of 40 calls, **22 were
`run_script`/`run_script_unsafe` and 8 were API discovery** — three quarters of the budget spent
learning and writing Python rather than editing.

**Caveats that matter more than the number.** One run, one agent (Codex, medium effort), no
replication, no control arm against a tool catalog, no hardware or Resolve build recorded, and the
author notes retries inflated the cost. It measures one side of the comparison, not the
comparison. Treat it as a well-posed question with a first data point, not a result.

#### The general MCP evidence points the other way [primary]

Every controlled measurement of catalog-vs-code-execution outside this domain finds the escape
hatch *cheaper*, at equal success:

- **arXiv:2602.15945**, "From Tool Orchestration to Code Execution: A Study of MCP Design Choices"
  (Felendler, Gandhi, Habler, Elovici, Shabtai; submitted 2026-02-17). Evaluated on MCP-Bench
  across 10 representative servers. Verified at source. From the abstract: "while CE-MCP
  significantly reduces token usage and execution latency, it introduces a **vastly expanded attack
  surface**." On quality: "the CE-MCP's task fulfillment, tool selection accuracy, and parameter
  accuracy are **comparable to those of the MCP** for most configurations" — the efficiency comes
  from turn count: "The MCP often requires dozens of turns due to its reasoning–tool–reasoning
  loop, whereas the CE-MCP aggregates most tasks into a single execution turn." The paper contains
  **no mention of DaVinci Resolve or any creative application.**
- **Anthropic, "Code execution with MCP"** (2025-11-04): a rewrite "reduces the token usage from
  150,000 tokens to 2,000 tokens — a time and cost saving of 98.7%".
- **Anthropic, "Writing effective tools for AI agents"** (2025-09-11), stated outright: "**More
  tools don't always lead to better outcomes.**" Its accuracy claims are shown as graphs without
  numbers in the text — cite the principle, not a figure.
- Independent replications (AIMultiple, 2026-08-14: 78.5% fewer input tokens at 100% success on
  both arms, but 2.2× more *output* tokens; Bifrost/Maxim: ~92.8% input-token reduction at 500
  tools) agree on direction. The Bifrost figures are a vendor benchmark for its own product —
  **secondary**.

**The two bodies of evidence do not actually conflict; they measure different things.** The general
benchmarks hold the API knowledge constant and measure execution. The Resolve benchmark measures a
cold agent that must *discover* a 410-method API before it can execute — and that discovery is
where three quarters of its budget went. The escape hatch is cheap once you know the API and
expensive while you are learning it. **That is the gap a measuring server sits in**, and it is the
one number nobody has: whether a server that hands over measured facts beats both shapes. Nobody
has run the comparison on Resolve, or on any creative application, here included.

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

**Nobody else has written about the 60 s cap, the sandbox, or `run_script_unsafe`.** [not-found]
A dedicated sweep of press, blogs and forum threads found no source discussing any of them. Every
limit in this section is first-hand from this box. The press coverage (CineD, byteiota, explainx,
digitalproduction, note.com) mentions none of it, which is the same silence Blackmagic's own prose
keeps: these facts exist only in machine-readable tool schemas.

The following pitfalls *are* corroborated by other projects, and are worth harvesting:

**Two writers, one Resolve is a real hazard, and one project gates on it.** [primary]
`OSideMedia/oside-resolve-mcp` is the only repository in the ecosystem that names it: "while a
handoff runs, treat it as read-only — **two writers, one Resolve, and `run_script_unsafe` has
filesystem and process access with no contract.**" Coexistence being *possible* (proven on-box in
section c) is not the same as coexistence being *safe*: nothing arbitrates two agents writing to
one timeline. Whatever topology wins, this is a discipline the agent docs must carry, not a
property the substrate provides.

**The native server is stdio-only, with no network transport.** [primary] From
`fat-tire/resolve` issue #87 (2026-09-09), reporting on Linux: "when you run `ResolveMCP`, it's
actually running `ResolvePython` which in turn runs the contents of `ResolveMCP` which is a `.zip`
file containing the server, tool definitions, and supporting files. … That means the MCP server
uses `stdin` and `stdout`. If we want it to be available to a local LLM running perhaps in another
container or another machine, the davinci-resolve MCP server needs to be proxied to a port." Note
this is the *MCP* transport; Resolve's own scripting still listens on port 1144 (section c).

**`get_whats_new` is not a release inventory.** [primary] Independently observed by
`samuelgursky` issue #207: "The official MCP's `get_whats_new` response omitted 21.1 while the
installed scripting changelog includes it; do not treat that response as the complete release
inventory." Same defect as recorded on-box in section (a), found by someone else on macOS — so it
is the shipped data, not this install.

**Two 21.1 API calls are already known-broken by contributors.** [primary, contributor-validated
on Studio 21.1.0.14] `resolve.ValidateDCTL` is source-layout sensitive: a multiline identity
returns `None` (success) while the same function on one line returns `DCTL Error: main DCTL
function does not have return value.` And the native audio-mapping setters "returned true and
getters reflected the requested values, but immediate exports remained sample-identical to the
unchanged baseline" — correct only after a save/close/reopen cycle. Both from issue #207. A
rebuild on the native substrate inherits these; neither is visible in the stubs.

**The wrong-interpreter crash is not unique to this repo.** [primary] `hoyt-harness`
documents ADR 0001's failure independently: "DaVinci Resolve locates Python through the Windows
registry and loads `python3.dll` by full path… A uv-managed or user-only Python install uses a
different DLL and will cause a two-runtime crash at connection time." Worth noting for the ADR 0001
amendment the map lists as unspecified — the native server sidesteps this entirely by shipping its
own interpreter, which is the strongest single argument in Option 2's favour.

**WMIC removal broke process detection on Windows 11 build 26200+.** [primary]
`samuelgursky` #210: every tool refused with "Resolve is not running" while Resolve was on screen;
fixed in v2.218.1. **This box is Windows 11 build 26200.** Any process-detection code this project
writes should not reach for WMIC.

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

**And it is the only option with outside corroboration.** Two unrelated projects independently
adopted exactly this split in the week after 21.1, and both justified it with the same argument:
`OSideMedia` — "Reach for Blackmagic's for API lookup … Reach for THIS one for the handoff … the
first-party stubs document call shapes but none of the BEHAVIOURAL lies"; and `samuelgursky`'s
`api_truth` layer, which refuses `CopyGrades` because its "verified behaviour destroys existing
work" while the native stub reports the same call as returning `True`. Both landed on the phrase
*a type signature cannot express a lie*, with no visible contact between them. That is convergent
evidence for where the native server's boundary actually falls (section b).

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
- **The forum, for a roadmap statement.** `t=239823` and `t=239831` have now been read (via the
  `r.jina.ai` proxy — direct fetches still 403) and contain none. Forum *search* is still
  unreachable, so only the `f=21` board was swept; a scripting-specific board may carry more. A
  browser session could run the search this could not.
- **Whether Blackmagic ever corrects the "88 tools" claim** sitting on its own forum (`t=240062`),
  and whether the free-edition `ReadMe.html` carries the Python-to-Studio note the Studio one omits.
- **Write contention** — two servers mutating one timeline at once is still untested here.
  `OSideMedia` gates on it by convention ("treat the general server as read-only"), which is
  evidence the hazard is taken seriously, not evidence of what actually happens.
- **`samuelgursky/davinci-resolve-mcp` velocity.** 53 releases in seven days; anything quoted from
  it here is a 2026-09-15 reading and will be stale within days. Its `docs/reference/api_truth`
  entries are the part worth re-reading — measured behaviours are the one artefact this project
  would otherwise have to re-derive.
- **`RajanthaR/resolve-mcp-burn-bench`** — whether anyone replicates it, adds a control arm against
  a tool catalog, or it goes unmaintained. One run is not a result.
- **Whether a Resolve entry appears in the official MCP registry or the Claude connectors
  directory** under Blackmagic's name. None exists as of 2026-09-15.
