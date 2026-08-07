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
- Offset-to-nearest-strong-transient distribution across the concert corpus,
  signed, with direction bias. *Open — the measurement is
  `correlate_timeline`'s `transient_offsets`; see `corpus.md`.*
- Beat-grid position patterns, in frames and in beat-fraction, conditioned on
  context. *Open — `beat_offsets` and the bar histogram.*

## 2. Energy — the master concept

- The band is the source of energy and the mix is a proxy for it, so a concert
  cut follows interactions and expressions first and the loudness curve second.
  `[stated principle]` (base: "energy is where the attention is")
- v1 evidence for chasing energy is audio analysis + angle labels + targeted
  frame grabs. Per-angle visual-energy metrics are post-v1, which means claims
  in this section rest on less than the timing claims do and are tagged
  accordingly. `[stated principle]` (#13, scope)

## 3. Shot rhythm

- Duration distributions conditioned on section type and energy band. *Open;
  see `corpus.md`.*
- The variation instinct and "spectacle earns a hold" are base claims and apply
  here unchanged.
- Whether shots shorten through a solo's build and lengthen through a head.
  *Open.*

## 4. Angle roles

- **Prioritise the soloist, but keep moving.** Sitting on the soloist for the
  whole solo is not the same as serving it — the band's interactions are part
  of what the solo is. `[stated principle]`
- **Soloist doing something worth seeing → show him. Drummer lighting up at
  the same time → cut between them.** The two are not in competition; the
  exchange is the content. `[stated principle]`
- Wide-as-reset patterns, transition habits between roles, and hold length per
  role. *Open — `roles` and `shot_seconds` per role across the corpus.*
- The role vocabulary a given night supports is whatever its sidecar says;
  concert rigs here are a static wide, a drummer cam, and a roaming soloist
  cam. `[believed, unverified]` — from the rig described in spec #22; the
  sidecars are what make it fact per project.

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
- Nothing in this file has been measured yet. Every claim above is either a
  stated principle or a seed belief, and the first corpus pass is what turns
  the open sections into numbers.
