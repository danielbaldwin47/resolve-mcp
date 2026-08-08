# Base style profile

Cross-domain taste: what holds whatever is being cut. Claude loads this plus
the relevant domain doc (`concert.md` in v1) at cut time; where the two speak
to the same thing, the domain doc wins.

Every claim carries a provenance tag — `[stated principle]`,
`[measured — N projects, n=…, context]`, `[review feedback, YYYY-MM]`,
`[believed, unverified]`. The vocabulary and the rules for earning one are in
`docs/agents/style-layer.md`. **A claim with no tag is a bug in this
document**: it means a preference got written down without saying how it is
known, which is the black box this file exists to replace.

This is principles plus evidence, not a rulebook. Feel is subjective; the
measurements exist to absorb it from the corpus, not to reduce it to rules. Where
a measurement disagrees with a principle below, both stay and the disagreement
is written down — a measured deviation is a fact about the edits, the principle
is a fact about the intent, and dropping either loses the interesting part.

Sections that are still open name what would settle them. That is not "no
opinion" — it is "not yet measured", and `corpus.md` is where it gets filled.

## Transparency

- **Nothing sticks out unless it is meant to.** The edit is not the
  performance; a viewer who notices a cut was either meant to notice it or was
  pulled out of the room. Everything else in this document is downstream of
  this. `[stated principle]`
- Emphasis is the exception that proves it: a cut that *is* meant to be felt is
  a deliberate move, and the profile says so where it makes one.
  `[stated principle]`

## Transients

- **Transients, not the beat grid, are the fourth-wall risk.** A cut landing
  directly on a big transient — an obvious snare hit — reads as the *sound
  having triggered the edit*, and that is the thing that breaks transparency.
  `[stated principle]`
- **The downbeat itself is fine**, and rhythmic grid positions (beat 2, a
  mid-beat) are good when the music grooves. The risk is the attack, not the
  grid. `[stated principle]`
- Cutting on a big moment is an emphasis move, made on purpose or not at all.
  `[stated principle]`
- The offset distribution the corpus actually shows — signed, early against
  late, and how it conditions on context. *Open here on purpose. The concert
  entry has numbers (`concert.md` §1), but a claim only graduates to this layer
  once contexts agree (#21 policy 3), and no studio-session timeline has been
  measured.*

## Energy

- **Energy is where the attention is** — band-member interactions,
  expressions, fills, the moment it simply feels right. `[stated principle]`
- **Loudness and onset density are proxies for energy, never its definition.**
  A number that correlates with attention is not attention, and a cut that
  chases the meter instead of the room is the failure mode this distinction
  exists to name. `[stated principle]`

## Variation

- **Avoid runs of similar shot lengths.** Sameness is itself a thing that
  sticks out, which puts this downstream of transparency rather than beside it.
  `[stated principle]`
- **The spread is the instrument, not the median.** When the director recut an
  agent timeline (#46 round), the median shot barely moved (5.9 s → 6.1 s)
  while the standard deviation nearly doubled (5.0 s → 8.8 s) and the longest
  hold went 21.7 s → 48.9 s: the correction was almost entirely about
  variance, in both directions at once — shorter punctuation and longer holds.
  Matching a target median reproduces none of it.
  `[review feedback, 2026-08]` — one timeline, but it is a direct
  agent-vs-director paired comparison, which no corpus row is.
- **Spectacle earns a hold** — and especially so on a moving camera, where the
  move is the reason to stay. `[stated principle]`
- Shot-duration distributions, conditioned on section type and energy band.
  *Open; see `corpus.md`.*

## Angle roles

- **Every angle carries two axes: subject × character.** Subject is who or
  what (drummer, keys, ensemble); character is `wide`, `tight` or `moving`.
  This document speaks in roles; which camera *is* which role is a fact about a
  project and lives in that project's sidecar. `[stated principle]`
- Wide-as-reset, and hold lengths per role. *Open; see `corpus.md`.*

## Visual motivation

Cuts are visually motivated as well as musically, and the visual evidence is
frame grabs — this section is what to read them for. It landed whole from the
#46 review round, where the director recut an agent-built concert timeline and
annotated every change; each rule below was then checked against the frames it
cites (grab paths in the round's record). The rules are about legibility and
motion, not about concerts, which is why they live here.

- **A camera move is a no-cut zone until it lands.** Landing means the subject
  is legible and the framing static again; mid-move frames are unreadable blur,
  and a cut into or out of one gives the viewer nothing to parse. Cutting
  *into* an unresolved move was the single most common defect the director
  fixed. `[review feedback, 2026-08]`
- **Never cut away on the arrival frame.** The travel is spent buying the
  arrival; a cut at the exact frame a move resolves discards the payoff just
  paid for. Ride the landing. `[review feedback, 2026-08]` — the recut deleted
  an agent cut placed on the very frame a tilt arrived at the bassist's hands.
- **A shot that changes subject on its own is a sequence, not a shot.** A move
  from the soloist's face, down to his hands, over to the next player's hands
  and up to that player's face replaces three cuts, and cutting inside it
  breaks a transition the camera already made better. The strongest internal
  links are matched content — hands to hands, instrument to instrument.
  `[review feedback, 2026-08]`
- **Cut on action.** A performer's own movement across the cut point — a head
  dropping, a body turning into the next phrase — carries the cut the way the
  music otherwise would. `[review feedback, 2026-08]`
- **Blocking is a hard veto.** When a foreground figure occludes the subject
  (a back filling the frame), the angle carries no information and must be
  covered, whatever the musical logic says. In-frame is not coverage either: a
  subject that is small and defocused in an angle's background does not make
  that angle coverage of the subject. `[review feedback, 2026-08]`
- **Composition outranks focus — for holding, not for entering.** A
  well-composed frame going soft mid-shot is not a reason to leave, but a cut
  should not *enter* an unresolved focus pull: after a reveal, the extra
  frames until sharpness are worth waiting. Stated by the director as
  "composition matters more than if something is perfectly in-focus"; the same
  round shows him waiting ~1.4 s past a well-composed soft frame before
  cutting in. `[review feedback, 2026-08]`
- **Hands substitute for a face when the musical event is dexterity.** A
  virtuosic run is watchable at the fingers; framing that shows the mechanism
  beats framing that shows the expression while it happens.
  `[review feedback, 2026-08]`
- **Emphasis runs commit.** One deliberate on-the-hit cut licenses the next;
  a run of them reads as a dramatic device where an isolated one reads as an
  accident. (Downstream of the transparency principle: meant-to-be-noticed
  needs to keep being meant.) `[review feedback, 2026-08]`

## Observations

Free prose, first-class, anywhere in this document. Nothing here is waiting for
a number to be allowed to be said.

- One concert timeline is measured and no studio session is, so nothing has
  graduated to this layer yet and every distribution here is still open. That
  is the intended state for a first pass (#21 policy 4: no minimum-n gate
  blocks the first analysis run), but it does mean this file still runs on
  stated principle alone.
- The first measured entry did not contradict any principle above — the anchor
  puts the median cut 33 ms off the nearest transient with 2 of 360 landing on
  one, which is the transient philosophy behaving as stated. A principle
  surviving its first contact with the corpus is worth recording; it is not yet
  worth re-tagging.
