# The style layer

`styles/` is the agent's half of the project: angle sidecars and style
profiles, both authored by Claude and editable by the director, both invisible
to server code. Nothing under `src/resolve_mcp/` reads or writes anything in
this directory — that is story 59 of spec #22 and the guard test
`tests/test_style_layer.py` holds it.

The server measures; these documents decide. `correlate_timeline` reports that
a shot starts 47 ms after the nearest transient; whether 47 ms is musical is a
sentence in `styles/concert.md`.

## Layout

```
styles/
├── base.md              claims that hold for every kind of edit
├── concert.md           claims that hold for concert style-cuts (overrides base)
├── corpus.md            what was measured, in what order, with what context tags
└── angles/<project>.json   one angle sidecar per Resolve project
```

Profiles are **layered**: `concert.md` is read on top of `base.md`, and where
the two speak to the same thing, concert wins. A claim only belongs in
`concert.md` if it would be wrong in a studio-session edit; otherwise it is a
base claim and every pillar gets it.

## Provenance tags

**Every claim carries a tag.** A profile whose claims cannot be told apart by
how well they are known is a black box with extra steps — the tags are the
whole reason this is a document rather than a prompt.

| Tag | Means | Earned by |
| --- | --- | --- |
| `[measured: <corpus>, n=<cuts>]` | The corpus says so, and the numbers are in `corpus.md` | a `correlate_timeline` pass over named timelines |
| `[believed, unverified]` | Held on thin evidence — a handful of cuts, one timeline, or reasoning from the instrument rather than from the corpus | anything the corpus has not yet confirmed |
| `[director]` | The director said so in a review round | a review note, quoted in the claim |

Two rules keep the tags honest:

- **Thin claims downgrade.** A claim measured over fewer than ~30 cuts, or over
  a single timeline, is `[believed, unverified]` regardless of how clean the
  number looked. Corpus breadth is what a measured tag asserts.
- **A rerun can demote.** When a corpus pass contradicts a `[measured]` claim,
  the claim moves down to `[believed, unverified]` with the disagreement noted
  — it does not quietly keep its tag, and it does not quietly vanish.

`[director]` outranks both: a director note lands in the profile the round it
arrives (story 49), and a later corpus pass that disagrees with it gets written
up as a disagreement rather than overwriting it.

## Angle sidecars

One JSON file per Resolve project, named for the project, holding what each
camera actually is. This is what stops the agent re-deriving "which one is the
drummer cam" from frame grabs every session.

```json
{
  "project": "Jaded Symphony - The Sinclair",
  "context": "concert",
  "labelled": "2026-08-06",
  "method": "frame grabs at 3 times per angle, read by the agent",
  "angles": {
    "A001_C012.mov": {
      "role": "drums",
      "subject": "drum kit, three-quarters from house left",
      "character": "reaction",
      "framing": "medium",
      "motion": "static",
      "confidence": "high",
      "evidence": ["frames/A001_C012_00-01-30.jpg"]
    }
  }
}
```

`role` is the only key `correlate_timeline` consumes; the rest is for the agent
and the director. The keys mean:

- **`role`** — the short handle the measurement groups by (`drums`, `wide`,
  `soloist`, `piano`, `bass`, `audience`). Keep the vocabulary small and reuse
  it across projects, because cross-project claims are grouped on this string.
- **`subject`** — what the camera is pointed at, in words. The half of
  "subject × character" that says *who*.
- **`character`** — what the angle is *for* in a cut: `establishing`,
  `hero` (carries a solo), `reaction`, `detail` (hands, sticks, keys),
  `texture` (audience, room, atmosphere). The half that says *why cut to it*.
- **`framing`**, **`motion`** — `wide`/`medium`/`tight`, `static`/`roaming`.
- **`confidence`**, **`evidence`** — how sure the label is and the frame grabs
  it was read from; a `low` here is why a corpus claim about that angle is
  thin.

An entry with no `role` is dropped by `correlate_timeline` rather than refused,
so a half-labelled project still measures — its shots land under `unlabelled`.

Pass a sidecar to the tool by lifting the `angles` object straight through:

```python
angles = json.loads(Path("styles/angles/<project>.json").read_text())["angles"]
correlate_timeline(beats=..., timeline=..., angles=angles)
```

## Labelling a project

1. `inspect_timeline` for the distinct clip names on the angle tracks (or the
   sidecar's own last pass, when you are only filling gaps).
2. `grab_frames` on each clip at three well-separated times — one is a lens cap
   or a black frame more often than you would think.
3. Read the grabs. Write the sidecar. Every angle gets `subject` **and**
   `character`; a camera you cannot identify gets `confidence: "low"` and says
   what it looks like, rather than a guess that reads as fact.

## Running a corpus pass

The corpus is **ordered** — timelines newest-last — because taste drifts and a
claim that holds only in the last three edits is a different claim from one
that holds across all fifteen. Each timeline is **context-tagged** (`concert`,
`studio-session`) so base and concert claims can be told apart.

Per timeline:

1. Get a WAV and run `analyze_music` on it for the beat grid. A concert cut to
   a director-supplied master mix takes that file directly; otherwise the
   timeline's own mix has to be exported — `separate_stems(scope="timeline")`
   acquires it on the way, and its job result names the acquired audio.
2. `correlate_timeline(beats=…, timeline=…, angles=…)` with the project's
   sidecar.
3. Record the result path and its gist in `styles/corpus.md`, with the
   timeline's order index and context tag.

Then read across the results — not one at a time — and write the profile.
Aggregate before claiming: the number that matters is the distribution over the
corpus, and the tag you are entitled to is set by how many cuts and how many
timelines stand behind it.

**`alignment.mode` decides whether a result is usable at all.** `audio_clip`
means the times were read through the audio the analysis was run on;
`timeline_start` means the timeline said nothing about where the mix sits and
the times count from its own first frame instead. A whole file measured against
the wrong zero looks exactly like a whole file measured against the right one,
so a corpus pass records the mode per timeline and excludes `timeline_start`
readings from every timing claim.
