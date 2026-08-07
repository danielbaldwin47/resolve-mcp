# Concert style profile

Read on top of `base.md`. Everything here is about cutting a jazz concert shot
on 2–3 cameras — wide static, drummer cam, roaming soloist cam — to a master
mix the director hands over.

A claim belongs in this file only if it would be **wrong** in a studio-session
edit. Anything that would also be true there is a base claim.

Every claim carries a provenance tag (`docs/agents/style-layer.md`).

## Timing against the music

- **Near-beat, not on-beat.** Cuts sit close to the grid without landing on the
  attack that the grid is made of. `[director]` — spec #22, story 45: "cutting
  that avoids transient-triggered cuts (near-beat, not on-beat)."
- The offset window that "near" actually means in this director's edits — the
  distribution of signed transient offsets across the concert corpus, early
  against late. *Awaiting the corpus pass; see `corpus.md`.*
- Where in the bar cuts land: whether the corpus favours the bar line, the back
  half, or is flat. *Awaiting the corpus pass.*

## Reacting to the band

- **Drum fills are a cue to cut, not noise to avoid.** A fill is the band
  telling the room something is about to change, and the edit answers it.
  `[director]` — spec #22, story 45: "reacts to drum fills."
- **Solo changes are structural.** Who is out front is the first thing that
  decides which angle a passage lives on; the structure analysis names when the
  front changed, not who it is, and the sidecar's `role` closes that gap.
  `[believed, unverified]` — follows from how `analyze_structure` and the
  sidecar are built (#38, #45); the corpus has not yet been read for how
  strictly past edits actually follow the front.
- Whether shots get shorter through a solo's build and longer through a head.
  *Awaiting the corpus pass.*

## Angles

- The concert role vocabulary is `wide`, `drums`, `soloist`, plus whatever a
  given night's third camera was. `[believed, unverified]` — from the rig the
  director describes (spec #22 problem statement: "wide static, drummer cam,
  roaming soloist cam"); the sidecars are what will make it fact per project.
- Angle share per tune, and which angle a tune opens on. *Awaiting the corpus
  pass.*

## The set as one thing

- **A set is one continuous cut over one master-audio clip**, planned
  song-by-song but never assembled as separate timelines. `[director]` — spec
  #22, story 44.

## Self-review

- **Every build is measured before the director sees it**, and every outlier is
  either fixed or written down with a reason. An outlier here means a shot
  whose offset or duration falls outside what this profile claims — which is
  only possible because the claims above are numbers, not adjectives.
  `[director]` — spec #22, story 47.
