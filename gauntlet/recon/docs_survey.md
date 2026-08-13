# Concert pillar docs survey

Sources read: `docs/agents/concert.md` (full), `docs/agents/style-layer.md` (full),
`styles/base.md`, `styles/concert.md`, `styles/corpus.md` (all full), the three
`styles/angles/*.json` sidecars (skim), `docs/adr/*.md` titles, repo root listing
for `projects/`.

## (a) Concert pillar end-to-end workflow, as prescribed

1. **Director hands over three inputs** (session preconditions, never derived by
   the agent):
   - Sync-reference timeline: angles hand-synced in the GUI, one stacked
     timeline. Agent reads each angle's offset via `inspect_timeline` and
     records it as `sync_offset` on the cut file's source aliases —
     informational only.
   - Master-mix file: the explicit audio file every analysis runs on; it's
     the `audio` block of the cut file. Its record position on the
     sync-reference timeline is the audio→timeline mapping.
   - Angle sidecar (`styles/angles/<project>.json`): subject×character label
     per angle. Built via the style-layer labelling flow if absent — director
     confirms once, never re-asked.

2. **Session-start prep** (background jobs, kicked off together, cache makes
   reruns free):
   - `analyze_music` on the mix (beats/downbeats/energy) + `separate_stems`
     two-pass (needs media-pool import first — stems key to pool media, not a
     bare path).
   - `analyze_structure` on the mix once stems land, `solos=true` (tunes from
     applause, solo changes from stems). Keep every returned file path — the
     self-review consumes them verbatim.
   - `songs.json`: applause analysis proposes song starts, director confirms
     blue markers (one per song, T7), agent authors the file. Shared with
     rough-cut pillar; whichever runs first writes it.
   - Rubato regions excluded from cut-placement evidence by beat-confidence
     gating throughout.

3. **Planning** — one cut file per concert, built **song by song**:
   - Candidate angles considered go on the segment as `alternates[]` (makes a
     later angle note a cheap `swap_take` instead of a rebuild).
   - Visual evidence = targeted frame grabs at event-nominated moments only
     (fills, solo changes, builds, candidate holds) — audio analysis
     nominates, frames verify, they don't discover.
   - Every taste call (transient offset, shot duration, who gets the frame)
     comes from `styles/concert.md`; profile silence is an observation to add,
     not license to improvise.
   - `build_timeline` materializes the whole set as one versioned timeline;
     per-song deliverables are range renders of the final version, not
     separate timelines.

4. **Mandatory self-review, per song, before the director sees anything**:
   run `correlate_timeline` on the built cut (beats/tunes/solos from prep,
   mix path as `audio`, sidecar labels as `angles`). Compare inline gist
   against `styles/concert.md`'s measured claims (transient-offset dist.,
   shot-duration runs, role shares/transitions, event responses). Every
   outlier is fixed (edit JSON, rebuild) or justified by name in the report.
   Check `alignment.mode` before trusting a run.

5. **Cut report** delivered to director: built timeline + markers (GUI
   markers are uncertainties-only, via `set_markers`) + per-song report (key
   moves, event responses, every self-review deviation kept with
   justification). Report is a teaching surface — critique feeds the style
   profile, not just the cut. Markers survive rebuilds (ADR 0006, keyed to
   mix frame).

6. **Review round** (5 conventions, general not concert-only):
   - Two note channels, equal weight: GUI markers + chat critique.
   - Revision unit is the round: batch of notes → JSON edits → one rebuild →
     next `<name> v<N>`. Next report answers each note (change or pushback).
   - In-place exception: pure angle-swap notes are `swap_take` immediately
     (JSON updated same breath); structural notes wait for the round rebuild.
   - Every note sorted two ways: cut-level → JSON edit; style-level → new
     `[review feedback, YYYY-MM]` entry in `styles/concert.md` in the *same*
     round, plus the cut fix. Agent proposes the sort, director can override.
   - Done is when the director says done; final version renders per-song
     range-render deliverables.

7. **Titling** (separate side track): `titles.json` authored from
   `songs.json`, applied via `apply_titles` after every rebuild (owns its own
   track — rebuild-then-reapply is the whole recovery). Placement/fade timing
   is agent judgment, phrase-aware, energy-scaled ("would a phone going off
   be rude here?"). Schema ships ranges not defaults on purpose.

## (b) Style vocabulary that exists

**Provenance tags** (`docs/agents/style-layer.md`) — every claim in
`base.md`/`concert.md` carries exactly one:
- `[stated principle]` — director said so; the default.
- `[measured — N projects, n=<cuts>, <context>]` — corpus evidence, row in
  `corpus.md`; sample size + context are part of the tag.
- `[review feedback, YYYY-MM]` — landed from a cut-review round, dated.
- `[believed, unverified]` — thin evidence (one project, a handful of cuts,
  or reasoning from the instrument).

Four honesty rules: measurement never overwrites a stated principle (both
stay, conflict written down); thin support downgrades regardless of how clean
the number looks; context is attributed and a claim graduates from a domain
doc to `base.md` only once contexts agree; taste beats recency for corpus
*membership*, recent work breaks *ties* between conflicting readings.

**Layering**: `base.md` (cross-domain: transparency, transients, energy,
variation, angle roles in the abstract, visual motivation) +
`concert.md` (domain-specific: numbers on transient offset, beat-grid skew,
shot rhythm, angle roles with rig-specific mechanism, event-reactive moves,
openings/endings). A claim belongs in the domain doc only if it would be
*wrong* elsewhere; a claim true across domains graduates to base.

**Principles currently on record** (selection, not exhaustive):
- Transparency: nothing sticks out unless meant to; transients (not the beat
  grid) are the fourth-wall risk; downbeat/grid position is fine, the
  *attack* is the risk.
- Energy: proxied by loudness/onset density but never *defined* by them —
  attention is band interaction, expression, fills.
- Variation: avoid runs of similar shot length; spread (stddev) is the
  instrument, not the median; spectacle earns a hold, especially on a moving
  camera.
- Angle roles: subject × character axes; concert-specific mechanism —
  operated camera = home angle always, locked camera never is; locked camera
  goes to drums so the operated one is free to follow the soloist and roam;
  operated cameras can hold >30s, locked/drum cameras structurally cannot
  (hard ceiling: no drum shot in the corpus reaches 22s).
- Visual motivation (landed whole from the #46 director recut, all tagged
  `[review feedback, 2026-08]`): no cut mid-camera-move; never cut away on
  the arrival frame; a subject-changing move is a sequence not a shot (don't
  cut inside it); cut on the performer's own action; blocking is a hard veto;
  composition outranks focus for *holding*, not for *entering*; hands
  substitute for a face on dexterity events; emphasis runs commit (one
  deliberate on-the-hit cut licenses the next).
- Concert §1 cut placement: phrase boundary is the real placement unit,
  transient-distance is its residue (17–41 ms median across 6 timelines);
  no direction habit (early vs late split flips depending on a 1-frame clock
  correction); beat-1 skew (~49%) is real but thin (rests on only 3 of 6
  timelines whose grids are usable — the other 3 report `meter: 1` and are
  gated out whole) and confounded (the downbeat detector keys on the same
  strong onsets a director might cut to).
- Concert §5/§5b: drum-fill response is only `[believed, unverified]` — the
  detector catches a documented *minority* of what the director actually
  responds to (12 of 25 arrivals in the #46 recut sit near a detected fill);
  openings/endings staging (no landing-on-a-pan opens, end inside the
  performance not on applause, black as a deliberate device) is entirely
  `[review feedback, 2026-08]` from a single round on a single tune.

## (c) Per-project assets for Zinc specifically

There is **no `projects/` directory in this repo** — glob and root listing
both come back empty; concert "projects" are DaVinci Resolve project files
that live on a media drive outside version control, not repo paths. What the
repo actually holds for Zinc:

- `styles/angles/2026-06_Zinc_and_Monkfish.json` — the angle sidecar covering
  **two** corpus timelines in one project: Zinc - Set 2 Main (entry 1, the
  corpus **anchor** — "strongest current-taste exemplar") and Monkfish Main
  (entry 5, partial cut). Confirmed by director 2026-08-07. Contains both the
  per-night multicam angle labels (Video 1 / Video 2, different roles on each
  night — Video 1 is a *wide* at Zinc, a *moving* camera at Monkfish) and a
  third, source-clip-keyed labelling for a separate "MCP Monkfish Main"
  reference timeline (plain tracks, not multicam), including a measured
  av-sync correction for the moving camera (~+4 frames late, corrected
  2026-08-08).
- `styles/corpus.md` measured rows for both Zinc timelines: Zinc - Set 2 Main
  (366 cuts, `audio_clip` matched alignment, 33 ms median transient offset,
  meter-1 grid so **no usable beat-position claim**) and Monkfish Main (233
  cuts, `audio_clip` matched, 41 ms median offset, only partly cut — 3 of 5
  segments are uncut passages so its mean shot length / angle share are
  flagged not-comparable).
- Both Zinc timelines carry gated-pass and visible-edit-reader (#142)
  re-measurements in `corpus.md`, plus a corrected `zero_frame` (PR #121,
  ADR-adjacent fix to a 1-frame `GetSourceStartFrame` bug).
- No `songs.json`, no cut-file JSON, no cut report, and no titles.json for
  Zinc are visible anywhere in this repo tree — only the analysis/sidecar
  layer is checked in. (Result files like
  `…/analysis/Zinc---Set-2-Main-1cc8ebd14cb3.correlate.json` are referenced
  by name in `corpus.md` but live in an analysis cache, not under version
  control here.)

## (d) Top 5 gaps vs. "final-delivery quality edit"

1. **Beat-grid evidence is mostly unusable, and that's load-bearing on the
   flagship project.** Zinc - Set 2 Main — the corpus's *own designated
   anchor* — reports `meter: 1` and is refused whole by the rubato/steadiness
   gate, so it contributes zero beat-position evidence; only 3 of 6 corpus
   timelines produce a trustworthy grid at all, and the resulting "49% of
   cuts land on beat 1" claim carries an admitted, unresolved confound (the
   downbeat detector may just be agreeing with the director about where the
   loud moments are, not detecting an independent grid). A final-delivery
   process this confident about "musicality" is standing on the transient
   numbers alone; the doc is honest about this, but a delivery bar built
   from these docs has real trouble picking a beat-aware cut point on the
   anchor project specifically.

2. **The vocabulary that decides subject-following cuts is deliberately
   unmeasurable.** "Soloist-primary, not soloist-only" for the moving camera
   is `[stated principle]`, and the docs say outright that *no analysis in
   the corpus can see who is on screen* — so nothing catches a cut that stays
   on the wrong player, follows the wrong solo entrance, or misses a handoff.
   This is arguably the single most visible failure mode in a live "did they
   cut to the right person" review, and the self-review loop
   (`correlate_timeline`) structurally cannot flag it — a human always has
   to.

3. **The event-response layer editors actually cut *to* is thin or absent.**
   Drum-fill response is `[believed, unverified]` and the corpus shows the
   fill detector missing more than half of what the director actually
   responds to (12/25 in the #46 recut). Solo-change response is
   `[believed, unverified]`. Section-boundary and build response are both
   flatly "Open." A style-driven pillar whose entire premise is "cut to the
   music's events" has its event-response taste layer mostly un-derived —
   the self-review has almost nothing to check these against besides eyeballing.

4. **The two devices the director used most in the one hand-recut on record
   are outside what the pipeline can build or measure.** Literal black and a
   V2 overlay shot (§5b openings/endings) are explicitly called out as
   outside both the cut-JSON schema (butt-joined V1 only) and
   `correlate_timeline` (single-track reader as of the writing, later
   partially addressed by #149's visible-edit reader for overlays/black, but
   the openings/endings staging vocabulary itself — false-ending black,
   landing shots, crash-cut-to-black-on-the-hit — has no schema support
   named in `concert.md`/`concert.md` beyond prose). A "final delivery" cut
   that never opens/closes the way the director's own best-documented recut
   did is missing a device the docs themselves flag as high-value and
   currently unbuildable by the schema as described.

5. **Corpus breadth is thin exactly where "final delivery" judgment differs
   most: 3 projects, and per-shot subject is attributable on effectively
   one night.** All quantitative claims in `concert.md` ultimately rest on
   3 distinct projects (7 timelines, 2 of which share a project with the
   anchor); claims about what the moving camera is actually pointed at
   moment-to-moment lean on entry 2 (Judson's) alone, since every other
   entry's subject labels are either stated-not-seen or unlabelled framing
   only. A style profile this concentrated is well-calibrated for repeat
   work with the *same* few bands/rigs and much less validated as a general
   "final delivery" bar for a first-time project or an unusual rig (e.g. no
   drum camera, more than 3 angles, single-camera passages) — several such
   cases are explicitly named as edge cases the corpus had to special-case
   rather than generalize (Monkfish's uncut passages, Scullers' no-drum-cam
   rig, Mercies' extra room-wide).

## Incidental notes

- `docs/adr/` titles (skim only, not read in full): 0001 interpreter must be
  a registered CPython install (attach-crash risk); 0002 analysis models
  injected at a seam; 0003 stems fingerprinted (path is a content hash); 0004
  editor-state getters only answer for the current timeline; 0005 a shot's
  source frames read off left offset, not source start; 0006 markers ride
  the mix across a rebuild (no marker sidecar).
- `gauntlet/` already contains prior-session scratch (`STATE.md`,
  `recon/p1..p5.scratch.json`) not read in depth here — out of scope for this
  survey, flagged only because a Grep for "Zinc" surfaced them.
