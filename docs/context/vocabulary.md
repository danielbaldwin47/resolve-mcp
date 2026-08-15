# Vocabulary

Narrative moved out of `CONTEXT.md` (#247) so the map stays short; read this
ranged, or Grep it, when you are about to work in this area. `CONTEXT.md`
holds the module → test → seam map itself.

- **cut file** — agent-authored JSON describing a timeline (schema v1,
  `cut/schema.py`); validated then materialised, never edited in place.
- **titles file** — agent-authored JSON of Text+ title events, applied to
  one owned track.
- **songs file** — `projects/<project>/songs.json`, song key → title +
  personnel; agent-authored, never read by server code — the facts behind
  the titles file (`docs/agents/rough-cut.md`, #132).
- **job** — background compute (analysis, stems, scenes) with one JSON
  record on disk; disk is the only source of truth.
- **envelope** — the shared tool-result shape every MCP tool returns
  (`tools/envelope.py`). A tool that hands back a job record replies
  `{"job": record}`; the decorator recognises the record and wraps it, so no
  starter builds that key itself (#219).
- **spill** — oversized results written to disk for the agent to grep
  instead of truncating. Every listing that can outgrow a reply is capped by
  `spill.capped`, so `truncated` and `spilled_to` mean one thing everywhere and
  a spilled file is the same reply carrying all of it (#224).
- **bin path** — a media pool folder, slash-separated from the root. To a
  tool addressing one clip by name: omitted is the whole pool, a name is
  that bin and everything nested inside it, `""` is the root folder alone
  — the value `list_media` reports for a root clip, so a listing reads
  back verbatim (#122); `""` is never the whole pool — that is the
  omitted form. Each media tool taking a bin — `list_media`,
  `inspect_clip`, `relink_media`, and per item on `set_clip_metadata` and
  `organize_media`'s `move_clips` — also takes `recursive`, false meaning
  that bin's own clips alone: the address of a copy a subfolder shadows
  (#134). The analysis and video tools resolve a clip by name too but take
  no flag, so their refusals never offer the shallow form.
- **the seam** — `resolve/connection.py` singleton, substituted by
  `tests/fakes/` via `set_connection()`; the only place fakes attach.
- **fake tier / live tier** — `pytest -m 'not live'` against fakes (the
  default) vs `-m live` against a running Resolve Studio. See CLAUDE.md.
- **stem** — separated audio (mix → vocals/drums/bass/other; drums →
  kick/snare/toms/ride/crash), path is a content hash (ADR 0003). The drum
  model writes `hh` too; it is not collected (#125).
- **wind / comp** — the two halves of the opt-in third pass over `other`
  (#153). `wind` is horns and reeds; `comp` is accompaniment — piano,
  guitar, vibes, percussion, and the bass line itself on a capture whose
  `bass` stem came back near-silent (#126). Where both are on disk they
  replace `other` as voices in `solos` — `other` *is* their sum, and
  measuring all three counts the residual twice (#157).
  _Avoid_: "piano stem" as a name for `comp` — it is accompaniment, and
  nothing may name it otherwise (#126).
- **bar map** — `analysis/bars.py`'s reading: one record per bar, each with its
  downbeat time, its length, the grid beat it starts on and its `in_group`
  position in the four-bar group. Every map says its `source` — `model` when the
  beat model committed to a meter and the map takes it at its word, `inferred`
  when it was recovered from the accents, `refused` when neither reading was
  worth having. The last is the point: the failure it ends is a grid quietly
  reporting `meter: 1` and callers doing bar arithmetic on it (#180).
  _Avoid_: reading `in_group` as a phrase — it is hypermeter, saying a bar line
  is a plausible place for a phrase to turn over, never that one did.
- **tactus** — the pulse the bars are counted in, and the thing a bar map folds
  a subdivision-scale grid down to. Not the grid's own beat: on the corpus
  anchor the grid is swung eighths and the tactus is every second one of them.
- **phrase** — the cut-placement unit (#46, `styles/concert.md` §1): a
  stretch of the soloist's line between two endings. `analysis/phrases.py`
  reports the **boundaries**, each with two times — `measured_t`, where the
  line actually stopped, and `t`, the beat inside the rest that a cut is
  placed on. Not the `phrase` factor inside `fills`, which is only "how far
  into a four-bar group does this land".
- **style layer** — `styles/` at the repo root: layered Markdown style
  profiles (`base.md` + `concert.md`), the corpus record (`corpus.md`) and
  per-project angle sidecars (`angles/*.json`). Agent-authored,
  director-editable, and **never touched by server code** — see
  `docs/agents/style-layer.md`, guarded by `tests/test_style_layer.py`.
- **provenance tag** — what every style claim ends with, saying how well it is
  known. The vocabulary is settled in #13: `[stated principle]`,
  `[measured — N projects, n=…, context]`, `[review feedback, YYYY-MM]`,
  `[believed, unverified]`.
- **angle sidecar** — one JSON file per Resolve project labelling each camera
  by subject × character; `correlate_timeline` reads `role` and `subject`
  (falling back to the subject half of a `subject-character` role) plus the
  optional `voice`, which says what the solo map calls that subject, and it
  arrives as a mapping the agent lifted, never as a path.
  _Avoid_: `camera_sidecar` for this — that module reads a camera model off
  the card's own XML (#94) and is not an angle sidecar.
- **super** — a burned-in graphic: a lower third, a title card, a bug. Read off a
  render (`video/supers.py`), never off a timeline, because by then it is pixels.
  Two shapes: a **card** holds the whole frame, an **overlay** sits on the picture.
  The reading is what two frames whose footage has moved on still agree about, and
  it is believed only where the *same* pixels agree twice — on a dark stage of
  locked-off cameras a lit music stand carries across a reading as well as
  lettering does, but never twice in the same place (#183).
  _Avoid_: reading a **straddle** — a cut with a graphic up either side of it — as
  a fault on its own. The human deliverables hold a lower third across cuts all
  night.
