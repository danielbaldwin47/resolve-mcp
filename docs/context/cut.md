# `cut/` — cut-file schema v1

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

`cut/` — cut-file schema v1: `document` (read off disk), `resolution` (the
optional **delivery resolution**: `timeline.resolution`, one reading of
`{width, height}` for both the rules and the build — omitted means the timeline
is created at the project's default, which on the corpus project is 4K against
1080p deliverables, #187), `schema`
(verbatim, served by `get_cut_schema`), `layout` (**where every entry lands** —
pure, documents in and positions out: `positions`/`placements`/`total_frames`/
`overlay_positions` are the one derivation the rules judge, the build places
against and `virtual_transcript` reads back, #218), `validate` (12 errors + W1,
W2, W8, W9 — W3-W7 are `virtual_transcript`'s over the same document — shared by
dry run and build pre-flight; W9 is the one that reports a rule that *could
not run*, where Resolve named no media bounds for E5 or E7 to check a range
against, #186; E11 is the exception it cannot answer, raised in
`resolve/build` where a live locked track can be observed), `tail` (the optional
**tail** device: one reading of `{type, duration_frames, audio_fade_frames}` for
both the rules and the build), `otio` (**the tail's document surgery** — pure,
no Resolve import: `inject` cuts the dissolve and the audio fade into an
exported OTIO document and reports what did *not* get one, `transitions` reads
them back, and the span/rate arithmetic under both counts every track in the
*timeline's* rate rather than each clip's own; `resolve/tail` is the only
caller, #221 — not `tests/otio.py`, which hand-builds documents for the live
tier). A `segments` entry is a shot or a **gap**
(`{"id", "gap": <frames>}`, literal black); `is_gap`/`entry_duration`/
`overlay_track` are the `layout` accessors every walker of that array shares.
