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
- **Cuts land close to a transient without landing on one.** On the anchor, the
  median cut sits 33 ms from the nearest transient (mean 46 ms, max 345 ms) and
  exactly **2 of 360 cuts land on one**. That is the stated principle showing up
  as a number: near, not on. `[believed, unverified]` — n=360 cuts but a single
  project, so #21 policy 4 downgrades it; `corpus.md` entry 1 has the evidence.
- **The direction bias is close to even, tipped early**: 195 cuts early against
  163 late. Not the lopsided distribution a rule like "always cut just before"
  would leave. `[believed, unverified]` — same single project.
- Beat-grid position patterns, in frames and in beat-fraction, conditioned on
  context. *Open, and blocked rather than merely unmeasured: the anchor's grid
  does not fit its music (`meter: 1` at 214 bpm over a jazz set), so the bar
  histogram and the beat offsets from it are not yet evidence. Rubato gating
  comes first — see `corpus.md` entry 1.*

## 2. Energy — the master concept

- The band is the source of energy and the mix is a proxy for it, so a concert
  cut follows interactions and expressions first and the loudness curve second.
  `[stated principle]` (base: "energy is where the attention is")
- v1 evidence for chasing energy is audio analysis + angle labels + targeted
  frame grabs. Per-angle visual-energy metrics are post-v1, which means claims
  in this section rest on less than the timing claims do and are tagged
  accordingly. `[stated principle]` (#13, scope)

## 3. Shot rhythm

- **Shots are long, and their spread is enormous.** On the anchor: median
  7.3 s, mean 10.4 s, from 0.46 s to 71 s. A concert cut here is not a montage
  — half the shots run longer than seven seconds — and the 71-second hold says
  the variation instinct has a very wide range to play in.
  `[believed, unverified]` — n=366 shots, one project (#21 policy 4).
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
- **The wide is the home angle; the drummer cam is cut to, not lived on.** On
  the anchor the wide holds 68.5% of screen time across 188 cuts (13.8 s a
  shot) against the drummer cam's 31.5% across 178 cuts (6.7 s a shot):
  near-equal cut counts, roughly double the hold. Two angles traded evenly
  would not look like this. `[believed, unverified]` — the direction is no
  longer in doubt (the director confirmed the angle mapping on 2026-08-07, so
  the wide really is the held angle), but it is still one project: this is
  how one room was cut, not yet a demonstrated habit.
- Wide-as-reset patterns, transition habits between roles, and hold length per
  role. *Open — the anchor is labelled now, but a role-transition claim needs
  more than one night to be worth stating.*
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
