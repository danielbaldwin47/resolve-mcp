# 0006 — Markers ride the mix across a rebuild, and there is no marker sidecar

**Status:** accepted, 2026-08-08 (#130)

## Context

A titles file survives a rebuild because every event time is an offset from the blue
marker naming its song, never an absolute frame. The markers themselves did not survive:
`build_timeline` creates an empty timeline, so each rebuild dropped every hand-placed
marker and a human had to re-mark every song boundary before the same titles file could be
re-applied. #42 called that the one manual step left in the re-apply loop.

#130 offered two routes: persist marker positions to a sidecar and re-apply them through
`set_markers` on rebuild, or write the hand procedure down as a deliberate v1 limitation.
The sidecar was preferred **if the position source is stable across rebuilds**.

## The measurement that decides it

Nothing a build writes into Resolve carries a segment id or a source frame. A built
timeline's content is record frames, and record frames are exactly what a rebuild moves:
re-time one segment and everything after it slides. So a marker's record frame is not a
position that can be persisted anywhere and re-used — not in a sidecar, not anywhere else.

One coordinate does hold still. A concert cut lays the master mix as a *single continuous
clip* under the whole timeline, and nobody re-times it. Which frame of the mix sits under a
given record frame therefore means the same thing on every version of the cut, and the two
versions' readings of it differ by one constant:

    zero_frame = record_in - source_in          (per version, read off the timeline)
    new_record = old_record + (zero_new - zero_old)

`analysis/correlate` already derived exactly this constant to turn timeline positions into
seconds of the analysed mix.

## Decision

**Neither route as written. Markers are carried, and there is no sidecar file.**

`build_timeline` reads the markers off the version it is superseding and writes them onto
the version it just made, moved by the frame of the mix each one sat over. `carry_markers`
(default on) turns it off. The reading of where the mix sits is `resolve/mix.py`, shared
with `correlate` so the two cannot disagree about it.

A sidecar was rejected on three counts, in order of weight:

1. **It would duplicate a source that already exists and is already durable.** A build
   never touches an earlier version — that is this file's oldest guarantee — so the
   superseded timeline *is* the persisted copy of its own markers, maintained by Resolve,
   correct by construction.
2. **It would go stale silently.** Markers are placed by hand in the GUI, which no server
   code observes. A sidecar written at marker-set time would diverge from the timeline the
   moment a human dragged one, and nothing would say so.
3. **It would be machine-local.** The only place server-written JSON lives is
   `Config.cache_dir` under `%LOCALAPPDATA%`. A rebuild on another box would find no
   sidecar and silently carry nothing. The repo's one deliberate project-scoped sidecar —
   the angle sidecar in `styles/` — is agent-owned data that server code is
   test-enforced never to read, so it is a precedent against this, not for it.

## The limitation this leaves, deliberately

**A cut with no master mix under it shares no axis with its previous version, and its
markers are not carried.** That is a rough cut (P4), which has no continuous mix by
definition. There is no honest derivation available there: without the mix the only
anchor left is a segment id, and cut files keep no per-version copies, so the previous
version's document is not on disk to compare against.

Nothing is guessed in that case. The build reports `markers.reason` naming the version
whose markers were left behind and how many there were, and re-marking by hand before
applying titles remains the procedure. The same refusal covers a previous version whose
audio shots disagree about where the mix starts.

**The carry is exact for a marker that means a musical moment and approximate for one that
means a picture moment.** A blue marker names a song and rides the mix exactly. A
director's coloured note was put over a *shot*, and a rebuild is precisely what moves
shots — so it lands on the same music, which may no longer be the same picture. Both are
carried, because a note at the right moment of the performance beats a note deleted; the
report splits the count by colour (`markers.by_color`) so an agent can re-read the notes
without re-reading the anchors.

## Measured, not assumed

One append of a multi-channel clip comes back as **one timeline item per channel**: an
8-channel MXF laid on a built timeline reads as eight audio shots on A1–A8, same clip, same
record frame, same source frame (Studio 21.0.3.7, Windows 11, live tier). The first
implementation asked for exactly one audio item and refused every real concert mix. The
rule is therefore *agreement*, not arity — `mix.anchor` answers only when every shot of the
named clip puts the mix's frame 0 on the same record frame, which one placement always does
however many channels it has, and two placements at different offsets never do.

No fake could have found this: it is Resolve's own answer to an append.
