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
  anchor (2 of 360 cuts on a transient), 34 ms on Freefall, 19 ms on Mercies,
  17 ms on Sunshine. Four timelines, two projects, two rooms, four years apart,
  and the median never leaves 17–34 ms — under a frame at 24fps in three of
  them. That is the stated principle showing up as a number: near, not on.
  `[measured — 2 projects]` (`corpus.md` entries 1 and 2)
- **The direction bias is close to even, tipped early.** Anchor 195 early / 163
  late; Freefall 25/18; Sunshine 22/26; Mercies 17/19. Early leads overall, but
  two of the four tip late — so what is measured is the *absence* of a rule like
  "always cut just before", not the presence of one.
  `[measured — 2 projects]`
- Beat-grid position patterns, in frames and in beat-fraction, conditioned on
  context. *Open, and blocked rather than merely unmeasured. Every timeline so
  far skews hard to the first beat of the bar — anchor 171:111:44:30, Mercies
  25:6:2:3 — which would be a real finding if the grids were trustworthy, and
  they are not: the anchor's reports `meter: 1` at 214 bpm over a jazz set, and
  Mercies puts a cut in a bar 6. Rubato gating comes first, and until it lands
  a consistent-looking histogram is the most misleading thing here, because it
  looks like evidence.*

## 2. Energy — the master concept

- The band is the source of energy and the mix is a proxy for it, so a concert
  cut follows interactions and expressions first and the loudness curve second.
  `[stated principle]` (base: "energy is where the attention is")
- v1 evidence for chasing energy is audio analysis + angle labels + targeted
  frame grabs. Per-angle visual-energy metrics are post-v1, which means claims
  in this section rest on less than the timing claims do and are tagged
  accordingly. `[stated principle]` (#13, scope)

## 3. Shot rhythm

- **Shots are long, and their spread is enormous.** Medians run 6.8–12.0 s
  (anchor 7.3, Freefall 6.8, Mercies 11.5, Sunshine 12.0) and every timeline
  carries a hold far out past its median: 71 s on the anchor, 94 s on Sunshine,
  146 s on Freefall, 184 s on Mercies. A concert cut here is not a montage, and
  the long hold is not an outlier to be smoothed away — it appears in all four.
  `[measured — 2 projects]`
- **The mean sits well above the median, every time**: 10.4 vs 7.3, 15.5 vs
  6.8, 18.3 vs 12.0, 18.5 vs 11.5. The distribution is skewed by design — most
  shots middling, a few very long. Averaging shot length would describe a cut
  that was never made. `[measured — 2 projects]`
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
  Anchor 68.5%, Freefall 75.5%, Sunshine 76.3%, Mercies 74.4%. Four timelines,
  two projects, and the home angle's share never drops below two thirds.
  `[measured — 2 projects]`
- **The home angle is whichever camera is on whoever is playing — not the
  wide.** This is the claim the anchor got wrong on its own. With two cameras
  the held angle *was* the wide (68.5%), which reads as a preference for the
  wide; with an operated camera available, the wide drops to 14–17% and the
  roaming `soloist-moving` camera takes 74–76%. The constant across both is not
  the framing, it is the subject: the cut lives on the music being made and
  visits everything else. `[measured — 2 projects]` — and the anchor is
  re-read by it rather than contradicted: with nothing closer available, the
  wide is where the soloist is.
- **The drummer cam is cut to, not lived on.** It takes near-equal cut counts
  and a fraction of the hold: anchor 178 cuts for 31.5%, Freefall 13 for 10.1%,
  Sunshine 10 for 6.7%, Mercies 5 for 4.0%. Frequent and short is the shape.
  `[measured — 2 projects]`
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
- One of seven concert entries is measured (the anchor). Every number above
  therefore carries `[believed, unverified]` rather than `[measured — …]`, not
  because the sample is small — 360 cuts is not small — but because one project
  cannot tell this director's taste apart from this night's room. Entry 2
  onward is what changes the tags.
- The most useful thing the first pass produced was not a number but a
  correction: two defects in `correlate_timeline` that only real footage
  exposes (transitions counted as shots, multicam angles sharing one name).
  Everything above is measured with those fixed; nothing measured before them
  should be believed.
