# Concert style profile

The concert domain: a jazz set shot on 2–3 cameras and cut to a master mix the
director hands over. Read on top of `base.md`; where the two speak to the same
thing, this file wins.

A claim belongs here only if it would be **wrong** in another domain. Anything
that also holds for a studio session or a vlog is a base claim — and a measured
claim that turns out to hold across contexts graduates to `base.md` (#21
policy 3).

Sections follow the schema settled in #13. Every claim carries a provenance
tag; the vocabulary is in `docs/agents/style-layer.md`.

## 1. Cut placement — transients, not the beat grid

The base transient philosophy applies as written. What is concert-specific is
that the grid is a live band's grid, so it moves.

- **Rubato is not measurable and not evidence.** Where the beat confidence
  drops — free intros, out-of-time codas — cut placement there says nothing
  about taste and is gated out of the evidence rather than averaged in.
  `[stated principle]` (#13, analysis workflow) — and the pass that applies it
  shows the principle is not a rounding error: the gate refuses every cut on
  three of the six corpus timelines and a third of them on a fourth.
- **Cuts land close to a transient without landing on one.** The median cut sits
  a few frames from the nearest transient and almost never on it: 33 ms on the
  anchor (2 of 360 cuts on a transient), 41 ms on Monkfish, 34 ms on Freefall,
  29 ms on Scullers, 19 ms on Mercies, 17 ms on Sunshine. Six timelines, three
  projects, four rooms, four years apart, and the median never leaves 17–41 ms
  — inside two frames at 24fps every time, inside one in four of them. That is
  the stated principle showing up as a number: near, not on.
  `[measured — 3 projects, n=1043 cuts, concert]` (`corpus.md` entries 1–3, 5)
- **There is no direction rule.** Anchor 195 early / 163 late; Freefall 25/18;
  Sunshine 22/26; Mercies 17/19; Scullers 141/185; Monkfish 118/110. Three of
  six tip late, three early, none decisively — and the two largest samples
  disagree with each other. What is measured is the *absence* of a habit like
  "always cut just before the hit", not the presence of one: the placement is
  about distance, not side.
  `[measured — 3 projects, n=1043 cuts, concert]`
- **Cuts skew to the first beat of the bar — where the grid can be trusted at
  all.** 49.0% of gated cuts land on beat 1 and 71.6% on beats 1–2, against a
  uniform 25%: 223 / 103 / 63 / 66 over beats 1–4. The skew is not the gate's
  doing and not an artefact of the broken grids — ungated, over the same three
  timelines, beat 1 held 53.1%, so refusing a third of the beats moved it four
  points. Read it as a tendency and not a rule: half the cuts still land
  somewhere other than beat 1.
  `[measured — 3 projects, n=455 cuts, concert]` (`corpus.md`, the gated pass)

  **Three of the six corpus timelines can say nothing here at all**, including
  the anchor. Their grids report `meter: 1` and the gate refuses them whole
  rather than filter them, since keeping only a meter-1 grid's position-1 beats
  would leave a histogram reading 100% beat one *by construction*. The
  histograms those three used to show — anchor 171:111:44:30, Mercies 25:6:2:3
  — were never evidence, and the positions 5, 6 and 7 that Scullers and Mercies
  reported are gone from every surviving row. This claim therefore rests on
  Freefall, Monkfish Main and Concert Full Cut: one timeline from each of the
  three projects, which is the thinnest a three-project claim can be.

  One confound the gate cannot address, and it is the reason this reads
  `[measured]` rather than settled: `beat_this` places downbeats from the same
  mix the cuts were made to, and a downbeat detector keys on strong onsets —
  roughly where cuts land. Part of the skew could be the detector agreeing with
  the director about where the strong moments are. Separating the two needs a
  grid from an independent source (a click track, a hand-tapped grid), which
  this corpus does not have.

- Beat-grid position patterns **conditioned on context** — by tune section, by
  energy, in beat-fraction rather than whole positions. *Open. The unconditioned
  histogram above is what the gated pass could support; conditioning it splits
  455 cuts across three timelines into samples too small to defend.*
- **The phrase is the placement unit; transient distance is its residue.** The
  #46 review round said why the near-not-on number exists: the director's notes
  move cuts to "after the sax's phrase", "between phrases", "now that the
  phrase has ended" — ten-plus notes in one tune, against one that moves a cut
  *off* a big hit ("didn't like it right on the drum hit, too abrupt"). A cut
  placed at a phrase boundary lands near the following downbeat's transient
  without landing on it, which is exactly the 17–41 ms signature — so
  optimising distance-to-transient directly reproduces the number while missing
  the mechanism, and the failure shows up as cuts that sit two frames off a hit
  *mid-phrase*. The audience model behind it, in the director's words: "if you
  were in the audience you might look over to the drums naturally now that the
  sax phrase has ended". No phrase detector exists in the analysis stack;
  phrase boundaries are currently readable only from the music itself.
  `[review feedback, 2026-08]`
- **Trading inverts the transient rule.** When the band trades (sax and drums
  exchanging bars), the switch itself is the event, and cutting *on* the
  transient of each entrance — repeatedly, as a committed run — is the
  director's own move: "more dramatic… it becomes more of a 'thing'",
  "playful instead of distracting". The near-not-on principle governs flow;
  structural exchange is emphasis, and emphasis lands on the hit.
  `[review feedback, 2026-08]` (base: emphasis runs commit)

## 2. Energy — the master concept

- The band is the source of energy and the mix is a proxy for it, so a concert
  cut follows interactions and expressions first and the loudness curve second.
  `[stated principle]` (base: "energy is where the attention is")
- v1 evidence for chasing energy is audio analysis + angle labels + targeted
  frame grabs. Per-angle visual-energy metrics are post-v1, which means claims
  in this section rest on less than the timing claims do and are tagged
  accordingly. `[stated principle]` (#13, scope)

## 3. Shot rhythm

- **Shots are long, and their spread is enormous.** Medians run 6.3–12.0 s
  (Monkfish 6.3, Freefall 6.8, anchor 7.3, Scullers 8.6, Mercies 11.5,
  Sunshine 12.0) and every timeline carries a hold far out past its median:
  71 s on the anchor, 94 s on Sunshine, 146 s on Freefall, 184 s on Mercies,
  239 s on Scullers. A concert cut here is not a montage, and the long hold is
  not an outlier to be smoothed away — it appears in all six.
  `[measured — 3 projects, n=1080 shots, concert]`
- **The mean sits well above the median, every time**: 10.4 vs 7.3, 15.5 vs
  6.8, 18.3 vs 12.0, 18.5 vs 11.5, 16.2 vs 8.6. The distribution is skewed by
  design — most shots middling, a few very long. Averaging shot length would
  describe a cut that was never made.
  `[measured — 3 projects, n=847 shots, concert]` — Monkfish is left out of
  this one on purpose: its mean is 22.5 s against a 6.3 s median, but three
  fifths of that timeline is passages nobody has cut yet, so its skew is a fact
  about how far the edit got rather than about how the director holds a shot.
- **The skew has an address: it is the operated camera.** Splitting the same
  shots by role, the locked cameras are nearly symmetric (drums 5.5 s median
  against a 6.6 s mean, over the 206 shots in this Monkfish-excluded set)
  while the operated ones carry the whole tail
  (`soloist-moving` 14.4 s median against a 24.6 s mean). The long holds in the
  bullet above are not scattered across the cut — every one of them is on a
  camera somebody was running. See §4.
  `[measured — 3 projects, n=847 shots, concert]`
- Duration distributions conditioned on section type and energy band. *Open;
  the conditioning needs the structure analysis, which has not been run over
  the corpus.*
- **The drum cam's frequent-and-short shape needs internal variety too.** The
  #46 recut kept the drum shots short (median 3.3 s against the agent cut's
  4.5 s) but deliberately broke their sameness: one held to 8 s "since the drum
  cam shots tend to all be the same length, and feel monotonous", another ran
  11.3 s because the interaction on screen earned it. Frequent-and-short is the
  angle's shape, not a fixed duration. `[review feedback, 2026-08]`
- The variation instinct and "spectacle earns a hold" are base claims, and a
  concert changes nothing about them — they carry their tags in `base.md` and
  are named here only so this section is not read as silent on them.
- Whether shots shorten through a solo's build and lengthen through a head.
  *Open.*

## 4. Angle roles

- **Prioritise the soloist, but keep moving.** Sitting on the soloist for the
  whole solo is not the same as serving it — the band's interactions are part
  of what the solo is. `[stated principle]`
- **Soloist doing something worth seeing → show him. Drummer lighting up at
  the same time → cut between them.** The two are not in competition; the
  exchange is the content. `[stated principle]`
- **There is a home angle, and it holds roughly three-quarters of the cut.**
  Anchor 68.5%, Freefall 75.5%, Sunshine 76.3%, Mercies 74.4%, Scullers 74.3%.
  Five timelines, three projects, and the share never drops below two thirds.
  This one needs no labels — it is about the shape of the distribution, not
  about which camera — and it was measured on entries 3 and 5 before either had
  a sidecar. When the labels arrived, not one of these numbers moved.
  `[measured — 3 projects, n=847 shots, concert]` — Monkfish is excluded
  (89.3%), not because it disagrees but because share is time and three fifths
  of its time is uncut passage sitting on one angle.
- **The home angle is the operated camera, and framing does not predict it.**
  On all six labelled timelines the angle holding the cut is the one somebody is
  running, and **no locked-off camera is ever the home angle**. Framing says
  nothing: a wide holds 68.5% on the anchor and 25.7% on entry 3. What makes
  this checkable is entry 3, the only rig whose fixed camera is a *wide* —
  everywhere else "wide" and "locked off" name the same cameras, so no evidence
  could separate them.
  `[measured — 3 projects, n=847 shots, concert]` (`corpus.md`, "What the
  labels settled") — Monkfish agrees but its share is excluded as ever; it is
  the home angle there by cut count too.
- **The moving camera serves two masters: the soloist, and visual interest.**
  It generally goes to whoever is soloing, but it also roams — different angles
  on the band, movement within the shot — because that is where interesting
  shots come from. Its subject is soloist-*primary*, not soloist-only, and that
  is a design choice rather than a lapse.
  `[stated principle]` (director, 2026-08-07) — corroborated on entry 2, the one
  night whose frame grabs show the camera on tenor, on piano hands, and wide on
  tenor-and-bass at different moments.
- **The locked camera is spent on the drums so the operated one is free.** With
  two or three cameras, the fixed angle goes to the drums because the drums are
  the rhythm section's most watchable instrument — which buys the operated
  camera permission to stay with the soloist and to roam.
  `[stated principle]` (director, 2026-08-07). The corpus fits and names the
  exception: three of four rigs put the locked camera on the kit, and the
  fourth (entry 3) has no drum camera at all, so its locked camera is a wide.
- **An operated camera can hold; a locked one cannot.** Shot length splits on
  operation, hard:

  | Role | n | median | >30 s | longest |
  | --- | --- | --- | --- | --- |
  | `drums-tight` (locked) | 314 | 5.25 s | **0 (0.0%)** | **21.5 s** |
  | `ensemble-wide`, locked instances | 206 | 7.3 s | 2 (1.0%) | 57.4 s |
  | `ensemble-wide`, the one operated instance (anchor) | 188 | 10.7 s | 17 (9.0%) | 71.1 s |
  | `soloist-moving` | 242 | 14.4 s | 58 (24.0%) | 238.9 s |

  **No drummer shot anywhere in the corpus reaches 22 seconds** — 314 shots,
  six timelines, four years, no exceptions. The mechanism is the director's:
  a locked shot has given everything it has within a few seconds, while an
  operated camera renews itself by moving. The cleanest evidence is *within*
  one role — the anchor's operated `ensemble-wide` behaves like the moving
  cameras (9.0% past 30 s) while the four locked `ensemble-wide`s behave like
  drum cams (1.0%). Same role name, opposite behaviour, and operation is what
  differs.
  `[measured — 3 projects, n=847 shots, concert]` — the `drums-tight` row is
  all six timelines (n=314) and every other row excludes Monkfish, because
  Monkfish's uncut passages sit on `soloist-moving` and so compromise that role
  without touching its drum camera. Its 108 drum shots are clean, and their
  longest is 13.7 s.
  *Caveat: in this corpus the operated camera is always also the home angle, so
  "operated" and "home" cannot be separated. What is separable, and separated
  above, is framing.*
- **The second angle is cut to, not lived on — whatever it is pointing at.**
  Near-equal cut counts, a fraction of the hold: anchor drums 178 cuts for
  31.5%, Freefall 13 for 10.1%, Sunshine 10 for 6.7%, Mercies 5 for 4.0%,
  Monkfish drums 108 cuts against the home angle's 125, and **Scullers 167
  against 182 on a camera that is a wide, not a drum cam**. Frequent-and-short
  is a property of being the other angle.
  `[measured — 3 projects, n=1080 shots, concert]` — the shape is what
  generalised here; that it is usually the *drummer* being cut to is a fact
  about the rig, and one night in the corpus had no drum camera at all. The
  hard version of this is the 22-second ceiling below: the second angle is not
  merely held less on average, it is **never** held long.
- **A second wide is a garnish, not a role.** Mercies is the only timeline with
  one (`room-wide`, from a seat rather than of the stage): 5 cuts, 5.2%.
  `[believed, unverified]` — one timeline.
- **An angle's role is what its framing holds, not its name.** The Monkfish
  drum cam is a drummer-and-bassist two-shot — both readable in every grabbed
  frame — and the #46 recut uses it as the *interaction* angle: an 11-second
  hold on it during the bass solo because "the drums and bass interact lots,
  and this angle shows both of them", with the director's explicit verdict
  that "showing the interaction is more important than only showing the
  soloist". The same reading runs the other way: a moving-cam frame with the
  bassist prominent "works as a bass cam", and its ending framing (drummer
  small and defocused behind the sax) is *not* drum coverage however much kit
  is technically in frame. Sidecars should record what a framing actually
  contains, because that is what decides which cuts it can carry.
  `[review feedback, 2026-08]`
- **The moving camera is a second editor.** Its reframings substitute for
  cuts: the recut deletes agent cuts because "the moving camera quickly
  focuses on the drums, so it has the same effect", rides a single shot from
  sax hands to bass hands to the bassist's face instead of three cuts, and
  waits out pans rather than cutting into them. The corollary is planning in
  pairs — one note cuts away *because* "the next shot will come back on the
  drummer, so it's a smooth transition": the camera's own journey decides
  when a cut is needed at all. `[review feedback, 2026-08]` (base: visual
  motivation, all of it)
- **Follow the audience's gaze at structure changes.** When the front
  changes, the moving camera settles on the new soloist first, and only then
  is another angle safe to cut to — mirroring where a listener would look:
  "you naturally look at the bass in this shot now that it's soloing".
  `[review feedback, 2026-08]`
- Wide-as-reset patterns and transition habits between roles — which role
  follows which. *Open. All six timelines are labelled now; the shot records
  hold the sequence, and nothing has read it yet.*
- **The rig is not a constant, so the role vocabulary is per night.** Spec #22
  describes a static wide, a drummer cam and a roaming soloist cam. No night in
  the corpus runs exactly that: the anchor runs two cameras with the *wide*
  operated and the drummer cam locked; entry 2 runs three (and four on Mercies,
  with a second wide from a seat); entry 3 runs two with **no drummer camera at
  all**; and entry 5, in the same project as the anchor five months earlier,
  puts a *moving* camera where the anchor puts a wide — same angle number, same
  role in the cut, different framing. Read a night's sidecar; never assume the
  rig.
  `[measured — 3 projects, n=6 timelines, concert]`

## 5. Event-reactive moves

Per event type: the response, and its **timing signature** — the lead/lag
distribution measured by correlating detected events against actual cut points.

- **Drum fill** — arrive on the drummer around the start of the fill, ride
  through the transition, and leave after the new section has settled.
  `[believed, unverified]` — seed pattern recorded as such in #13; the lead/lag
  numbers are exactly what a corpus pass is for.
- **Fills are one member of the class; the class is visible drum events.** In
  the #46 recut only 12 of 25 drum-cam arrivals sit near a *detected* fill
  (the agent cut it revised: 22 of 29 — an overfit to the detector). The
  director's own arrival reasons: "this drum snare thing", "this rhythmic
  drum thing, interaction with soloist", "a musical response to what happened
  before", a phrase-end glance — and one arrival placed to catch a fill's
  *end*, "the drummer goes 'ah' and the next cut catches that", because the
  resolution is the watchable part. `detect_drum_fills` finds a subset of the
  motivation, and treating its output as the complete cue list shows up as a
  measurable habit. `[review feedback, 2026-08]`
- **A structural fill is a visual transition.** The big snare fill that ends
  one solo carries the cut into the next soloist — the same event serves the
  music and the edit at once. `[review feedback, 2026-08]`
- **Solo change** — the front changing is structural, and it is the first thing
  that decides which angle a passage lives on. `analyze_structure` names when
  the front changed, never who it is; the sidecar's `role` closes that gap.
  `[believed, unverified]` — follows from how #38 and the sidecar are built;
  the corpus has not been read yet for how strictly past edits follow the front.
- **Section boundary**, **build** — response and timing signature. *Open.*

## 5b. Openings and endings

Both ends of a tune are staged, not covered. All of this landed in one review
round on one tune, which the tags say — but every device below was the
director's own move, unprompted, on top of an agent cut that had simply
covered both ends.

- **Do not open on the camera finding its shot.** The tune opened on the
  moving camera mid-pan; the director restaged it — a breath of black, a
  short settled moving-cam shot overlaid on a second video track, then the
  static angle carrying the pan's whole duration, returning to the moving
  camera only after it had landed. A static angle that already contains the
  pan's destination pre-announces it, and the return reads as continuation.
  `[review feedback, 2026-08]`
- **End inside the performance.** The recut deletes the applause tail
  entirely and ends 10 s before the agent cut did: black beats any frame in
  which the players have visibly stopped playing. `[review feedback,
  2026-08]`
- **Black is a device.** A false ending gets literal black ("this sounds like
  it could be the end of the tune") so the surprise stinger lands as a
  surprise, a "landing" shot shows the pickup, and the final crash cuts to
  black *on* the hit, before any release. The agent schema builds butt-joined
  V1 segments only; the recut uses gaps and a second video track as
  first-class material. `[review feedback, 2026-08]`

## 6. Observations

Free prose, first-class.

- The concert corpus (#21) is ~7 concert-context timelines, anchored on
  **Zinc - Set 2 Main** as the strongest current-taste exemplar. Full-set
  timelines carry many tunes, so per-cut sample counts run into the hundreds —
  the thin-corpus risk here is *project* count, not cut count, which is why the
  tag format names both.
- **Six of seven concert timelines are measured, across three projects**:
  the anchor, the three Judson's tunes (entry 2), the Scullers full cut
  (entry 3) and Monkfish Main (entry 5). Only Stablemates is left, and it is
  left for a reason — no render and no defensible clock. Timing claims above
  therefore carry `[measured — …]`. **Every measured timeline is now labelled**
  (the director supplied entries 3 and 5 on 2026-08-07), so role claims rest on
  the same three projects the timing claims do. What is still thin is narrower
  and worth naming exactly: claims about what a camera *points at* rest on
  entry 2 alone, the only night whose moving camera anybody has actually
  looked at.
- **Every timeline agrees about transients and none agrees about the beat
  grid.** Six timelines, three projects, four years: the median cut sits
  17–41 ms from the nearest transient every time — inside two frames, without
  exception. Over the same six, the beat-offset mean runs two to fifteen times
  its own median and three of the bar histograms contain positions that cannot
  exist in 4/4. The half of the analysis that does not depend on a grid is the
  half that replicates, and that is also the half the director's own stated
  principle is about.
- **Not every timeline's numbers mean the same thing, and the corpus row says
  which.** Monkfish is three fifths uncut, so its cuts count and its shot mean
  and angle share do not. A corpus that pooled everything measurable would have
  reported a 22.5 s mean shot and an 89% home angle, both of them artefacts of
  an unfinished edit.
- The most useful thing each pass produced was not a number but a correction.
  Entry 1: transitions counted as shots, and multicam angles sharing one name.
  Entry 2: a second dissolve shape that swapped real shots for transitions
  while keeping the count right, and an alignment that no timeline clip could
  supply. Entry 3: a master mix the analyser could not read at all (#110).
  Entry 5: a reason to distrust the beat-1 skew, since the two soundest grids
  in the corpus produce the flattest histograms.
  Everything above is measured with those fixed; nothing measured before them
  should be believed.
- **The labelling pass moved no number, and that was the useful part.** Entries
  3 and 5 were re-measured with their new sidecars attached and came back
  identical — same cuts, same offsets, same shot lengths, same shares under new
  names. A label is supposed to add names and nothing else, and re-running was
  cheaper than trusting that it had.
- **One tie the corpus broke, and one a conversation did.** Two labels arriving
  from a sentence settled a question five timelines of measurement could not:
  the home angle is the operated camera, not the wide, because entry 3 is the
  only rig whose fixed camera is a wide and so the only place the two readings
  disagree. The neighbouring question — *why* — could not be measured at all,
  and was simply answered: the locked camera is spent on the drums so the
  operated one is free for the soloist and free to roam. Worth remembering
  which kind of gap each source closes, because they are not interchangeable.
- **The #46 review round produced the first agent-vs-director paired
  comparison** — the director hand-recut an agent-built tune (Monkfish tune 2)
  and annotated all 55 changes, and his recut was then measured with the same
  instrument that reviews agent cuts. Same tune, same grid, same gate:
  transient-offset median 34 ms with 1 of 48 on a transient (the phrase
  mechanism leaving exactly the corpus's residue), but **32 early / 15 late** —
  a 2:1 early lean where the corpus shows no direction habit, consistent with
  phrase-boundary cuts landing just before the next entrance. His bar
  histogram reads 18/9/5/8 (45% beat 1) where the agent build on the *same
  gated grid* read 16/11/5/12 — which corrects an observation recorded on #46
  during the build: the flat histogram was a fact about the agent's cut, not
  about sound grids, and a mild beat-1 skew survives the soundest grid in the
  corpus. Report: `MCP-Monkfish-Tune-02-v3---Director-186049ae51cc.correlate.json`.
  `[measured — 1 project, n=48 cuts, concert]` — one tune; the paired design,
  not the sample size, is what it has over a corpus row.
- **What the round could not measure is where the recut lives.** The
  director's two most distinctive devices — literal black and V2 overlay
  shots — sit outside both the cut schema (butt-joined V1 only) and
  `correlate_timeline` (reads one track). The measured numbers above describe
  the V1 skeleton of a cut whose character is partly in what the measurement
  cannot see; a number-only reading of the round would have missed the black
  entirely.
- **The best result of this pass came from testing what the director said
  rather than recording it.** His account predicts that an operated camera can
  sustain a shot and a locked one cannot — so the shots were split by role, and
  no drum shot in 314 reaches 22 seconds while the moving camera runs to 238.
  The prediction was not obvious in advance and it held at the extreme, which
  is worth more than the agreement of averages. Ask why, then check whether the
  why leaves a mark.
