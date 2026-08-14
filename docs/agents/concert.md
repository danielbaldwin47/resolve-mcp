# The concert pillar

How a style-driven concert cut runs end to end: what the director hands over, the
analysis prep you kick off before planning, the song-by-song loop, the self-review that
has to pass before the director sees anything, and the review rounds that follow.

This is P3 of spec #22 (stories 40–50), settled in #16. It is the other shape from the
rough-cut pillar: the substrate is one continuous master mix under stacked synced
angles, the evidence is the analysis suite, and the taste you cut with is
`styles/concert.md` — that profile holds the numbers (transient offsets, shot rhythm,
angle roles, event moves); this doc holds the process. Neither repeats the other.

## What the director hands you

Three inputs, all theirs to make. Session preconditions — ask, never derive:

- **The sync-reference timeline.** Angles waveform-synced by hand in the GUI, stacked
  on one timeline. Read each angle's offset off it with `inspect_timeline` and record
  it as `sync_offset` on the cut file's source aliases — informational only, the server
  never computes or validates with it. You plan in the common clock; sync math stays
  the director's.
- **The master-mix file.** An explicit audio file that *is* the mix — every analysis
  runs on it directly, and it is the continuous audio clip your cut file names in its
  `audio` block. Its record position on the sync-reference timeline is the
  audio→timeline mapping.
- **The angle sidecar.** `styles/angles/<project>.json`, subject×character labels per
  angle. If it does not exist yet, build it by the labelling flow in
  `docs/agents/style-layer.md` — grab, propose, director confirms once — and it is
  never re-asked.

## Session-start prep

Kick off the fixed suite first and label angles while it runs — jobs are background,
and the cache makes a rerun free, so there is no per-session picking:

1. `analyze_music` on the master-mix file path — beats, downbeats, energy — and
   `separate_stems` two-pass, together. Stems feed `detect_fills` and the solo half of
   structure. `separate_stems` takes `scope`, not a path — the mix must sit in the
   media pool, so `import_media` it first if the director handed a bare file. One
   extra call, deliberate: stems are keyed to pool media.
2. `analyze_structure` on the mix path when the stems land, `solos=true` with the
   stems directory — tunes from applause, solo changes from the stems. Keep the file
   paths every job returns: the self-review consumes them verbatim.
3. **`songs.json`** is shared prep, not titling-owned: applause analysis proposes the
   song starts, the director confirms the blue markers (one per song key, T7), you
   author the file. Whichever pillar runs first writes it; format and ownership live
   in `docs/agents/rough-cut.md` §songs.json.

Rubato regions are excluded from cut-placement evidence by beat-confidence gating — a
grid fitted to free time measures nothing (`styles/concert.md` §1).

## Planning

One cut file per concert, planned **song by song** — analysis slices stay digestible,
the file grows a song at a time, and `build_timeline` materializes the whole set as one
versioned timeline. Per-song deliverables are range renders of the final version, not
separate timelines.

- Candidate angles you genuinely weighed go on the segment as `alternates[]` — that is
  what makes a review-round angle note a `swap_take` instead of a rebuild.
- Visual evidence is **targeted frame grabs at event-nominated moments**: the audio
  analysis nominates (fills, solo changes, builds, candidate holds), `grab_frames`
  answers only the visual questions the profile actually asks. Band interaction is
  primarily an audio signal — read it from stem activity and onsets; frames verify,
  they do not discover.
- **Obstruction is a veto, not a preference.** `analyze_occlusion` over the range you
  are cutting returns per-sample block scores and the unusable windows; a candidate
  angle whose frame is blocked at the moment you want it is out, whatever the role
  chart says. It samples rather than reads every frame, so treat its windows as where
  to look and confirm the marginal ones with `grab_frames`.
- **A song's ending is authored, not left.** The last segment takes the cut file's
  `tail` — `dissolve_to_black` with `duration_frames`, or `hard_to_black` — plus
  `audio_fade_frames`, so the picture's release and the mix's are one decision instead
  of a hard out that fades nothing. What shape an ending takes is `styles/concert.md`
  §5b; that it is decided at all is this step.
- Every taste call — where the cut lands relative to the transient, how long shots
  run, who deserves the frame — comes from `styles/concert.md`. When the profile is
  silent, that is an observation to add, not a licence to improvise silently.

## The self-review

**Mandatory, per song, before the director sees anything**: run `correlate_timeline`
on the cut you just built — beats, tunes, solos files from prep, the master-mix path as
`audio` (that is what makes the transient column real), and the sidecar's labels passed
as `angles`. This is the concert counterpart to the rough-cut pillar's
`virtual_transcript`, and the same tool the corpus was measured with, so your cut is
measured on exactly the axes the profile's `[measured]` claims stand on.

The tool reports and never judges — the bar is the profile. Compare the inline gist
against `styles/concert.md`'s measured claims: the transient-offset distribution
(fourth-wall risk, §1), shot-duration runs (§3), role shares and transitions (§4),
event responses (§5). With a solo map and a sidecar that names subjects, the
`on_soloist` block answers the core concert question — what share of the
solo-window screen time went to the player out front, to the ensemble, and to a
player who was not soloing. Read three of its lines next to the share:
`unlabelled_seconds` (a high share measured over a quarter of the cut is a claim
about a quarter of the cut), `black_seconds`, and
`soloist_seconds_by_follow_camera`, which is the part of the soloist line a
camera's label asserted rather than the solo map measured. **Every outlier is either fixed — edit the cut JSON, rebuild —
or justified by name in the cut report** ("spectacle earns the hold"). Nothing lands
off-style silently. Check `alignment.mode` before trusting a run, per
`docs/agents/style-layer.md`.

Two readings in that report are **blockers, not deviations you may justify**:
`shot_rhythm.reads_metronomic` — the cutting has acquired a pulse of its own, a long
strict A/B alternation or shot lengths piled into one bin — and `gears.one_speed`, the
same cut rate carried through loud and quiet where the music has an arc. Either one
firing means edit the cut JSON and rebuild before the director sees it; both are things
blind critics named unprompted in the rounds that went against us. The server only
warns — this gate is yours. And when the cut has also been rendered and scanned, check
scene-detect count against timeline item count before believing any of it
(`styles/concert.md` §6): the two disagreeing is an alarm about the measurement, not a
note about the edit.

## The cut report

What the director gets is the built timeline, markers on it, and a per-song report:
the key moves, the event responses taken, and every self-review deviation you kept,
with its justification. Markers are **uncertainties only** — `set_markers` on the
frames you want eyes on, nothing else; the GUI is not wallpaper. Reviewing a song
should take minutes.

The report is a teaching surface: the director critiques the reasoning
editor-to-editor, and that critique feeds the style profile, not just this cut.

Markers survive rebuilds here: `build_timeline` carries the previous version's markers
onto the new one by the frame of the master mix under them (ADR 0006) — the mix is the
shared axis a rough cut lacks.

## The review round

Five conventions (#16, and they are the general feedback-loop conventions, not
concert-only):

1. **Two note channels, equal weight**: GUI markers left while watching
   (`list_markers` is your queue) and chat critique of the cut report.
2. **The revision unit is the round, not the note.** A batch of notes → cut-JSON
   edits → one rebuild → the next `<name> v<N>`. The next report answers each note:
   what changed, or pushback with reasoning — editor-talk goes both ways.
3. **The in-place exception**: an angle-swap-only note is `swap_take` immediately,
   with the cut JSON updated in the same breath (`sync` on the tool call) — the file
   stays authoritative, no drift. Structural notes wait for the round's rebuild.
4. **Every note is sorted two ways.** Cut-level ("this cut is late") → cut JSON edit.
   Style-level ("too choppy in ballads") → a `[review feedback, YYYY-MM]` entry in
   `styles/concert.md` **now**, in the same round, and the cut fix — the profile
   learns the moment the note lands, not at the next corpus pass. You propose the
   sort; the director can override.
5. **Done is when the director says done.** The final version renders the per-song
   deliverables as range renders.

When a cut is **judged** rather than reviewed — a blind round, an A/B pack against
another edit — two further rules hold. The close is a **majority of three fresh,
independent critics** on the same sealed pack; fresh means no earlier round of this
piece, so a 1–1 split is never closed by asking the same judge again. And the director
rules what a critic is told to **ignore**: colour and grade differences are out of
scope (ruling of 2026-08-13) and every critic brief must say so — an ungraded render
losing on look is not a note about the edit. The ignore list is his to set, not the
critic's to decide.

## Titling on a concert cut

Titles ride the same timeline but never the cut file: author `titles.json` from
`songs.json` and re-apply with `apply_titles` after any rebuild — it owns its track,
so rebuild-then-reapply is the whole recovery. Placement and fade timing are yours,
phrase-aware and energy-scaled — the test is "would a phone going off be rude here?":
land titles where the music has room, scale fades to the energy around them. The
schema deliberately ships ranges, not defaults (`titles/schema.py` §3) — a title
placed mechanically reads mechanical.
