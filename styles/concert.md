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
  `[stated principle]` (#13, analysis workflow)
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
- Beat-grid position patterns, in frames and in beat-fraction, conditioned on
  context. *Open, and blocked rather than merely unmeasured. Every timeline so
  far skews hard to the first beat of the bar — anchor 171:111:44:30, Scullers
  203:55:38:27, Mercies 25:6:2:3 — which would be a real finding if the grids
  were trustworthy, and they are not: the anchor's reports `meter: 1` at 214 bpm
  over a jazz set, Mercies puts a cut in a bar 6, and Scullers puts cuts in bars
  5, 6 and 7. There is no such position in 4/4, so the grid is demonstrably
  wrong where those cuts are — and it is the same grid the other 96% were
  scored against.

  There is now a reason to think the skew is partly the detector rather than
  the director. Two timelines have structurally sound grids (Freefall and
  Monkfish: strictly 1–4, `meter: 4`) and they are the flattest — Monkfish puts
  67% on beats 1–2 where every broken-grid timeline puts 78–81%. The
  measurement gets *less* dramatic exactly where the instrument gets more
  trustworthy, which is the wrong direction for a real finding. Rubato gating
  comes first, and until it lands a consistent-looking histogram is the most
  misleading thing in this file, because it looks like evidence.*

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
- Duration distributions conditioned on section type and energy band. *Open;
  the conditioning needs the structure analysis, which has not been run over
  the corpus.*
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
  about which camera — which is why entry 3 supports it while supporting
  nothing else in this section.
  `[measured — 3 projects, n=847 shots, concert]` — Monkfish is excluded
  (89.3%), not because it disagrees but because share is time and three fifths
  of its time is uncut passage sitting on one angle.
- **The home angle may be the camera on whoever is playing rather than the
  wide.** On entry 2 the wide holds 14–17% while a roaming operated camera
  holds 74–76%, so there the home angle is chosen by *subject*, not framing.
  `[believed, unverified]` — entry 2 only, and the reason is worth stating: the
  anchor cannot corroborate this even though its numbers look like they should.
  Its home angle is its wide *and* its operated camera at once, so it has no
  case where the two disagree and cannot tell which one the director was
  following. Entry 3 is measured but unlabelled and cannot speak to it either.
  One project deciding between two readings is exactly what #21 policy 4 is
  about — so this is written down as the better reading, not as a finding, and
  entry 4 or 5 is what would settle it.
- **The drummer cam is cut to, not lived on.** It takes near-equal cut counts
  and a fraction of the hold: anchor 178 cuts for 31.5%, Freefall 13 for 10.1%,
  Sunshine 10 for 6.7%, Mercies 5 for 4.0%. Frequent and short is the shape.
  `[measured — 2 projects, n=498 shots, concert]` — two, not three: entry 3 is
  unlabelled, so its 167 second-angle cuts cannot be attributed to a drummer.
- **A second wide is a garnish, not a role.** Mercies is the only timeline with
  one (`room-wide`, from a seat rather than of the stage): 5 cuts, 5.2%.
  `[believed, unverified]` — one timeline.
- Wide-as-reset patterns and transition habits between roles — which role
  follows which. *Open. Four labelled timelines is enough to start; the shot
  records hold the sequence, and nothing has read it yet.*
- The role vocabulary a given night supports is whatever its sidecar says. Spec
  #22 describes the rig as a static wide, a drummer cam and a roaming soloist
  cam — but the anchor night ran **two** cameras and the other way round: the
  wide is the operated one and the drummer cam is locked off.
  `[believed, unverified]` — one night, from its frame grabs. Worth watching
  across the corpus rather than assuming either way.

## 5. Event-reactive moves

Per event type: the response, and its **timing signature** — the lead/lag
distribution measured by correlating detected events against actual cut points.

- **Drum fill** — arrive on the drummer around the start of the fill, ride
  through the transition, and leave after the new section has settled.
  `[believed, unverified]` — seed pattern recorded as such in #13; the lead/lag
  numbers are exactly what a corpus pass is for.
- **Solo change** — the front changing is structural, and it is the first thing
  that decides which angle a passage lives on. `analyze_structure` names when
  the front changed, never who it is; the sidecar's `role` closes that gap.
  `[believed, unverified]` — follows from how #38 and the sidecar are built;
  the corpus has not been read yet for how strictly past edits follow the front.
- **Section boundary**, **build** — response and timing signature. *Open.*

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
  therefore carry `[measured — …]`. Claims that
  need *labels* to state — which role holds the cut — rest on two projects,
  because entries 3 and 5 are measured but unlabelled; claims that separate
  framing from subject rest on entry 2 alone, because the anchor's home angle
  is both its wide and its operated camera and so cannot tell the two apart.
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
