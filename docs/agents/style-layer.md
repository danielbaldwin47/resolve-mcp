# The style layer

`styles/` is the agent's half of the project: style profiles and angle
sidecars, both authored by Claude, both reviewed by the director, both
invisible to server code. Nothing under `src/resolve_mcp/` reads or writes
anything in this directory — that is story 59 of spec #22, and
`tests/test_style_layer.py` holds it.

The server measures; these documents decide. `correlate_timeline` reports that
a shot starts 47 ms after the nearest transient; whether 47 ms is musical is a
sentence in `styles/concert.md`.

The decisions behind everything here: **#13** (what a style profile is, its
section schema, the analysis workflow) and **#21** (which projects form the
corpus, and the corpus policies). Read those before changing a format; this
file is their working form, not a second opinion.

## Layout

```
styles/
├── base.md                 cross-domain taste
├── concert.md              the concert domain (v1 authors this one only)
├── corpus.md               which timelines were measured, in what order
└── angles/<project>.json   one angle sidecar per corpus project
```

Profiles are **layered**: Claude loads `base.md` plus the relevant domain doc
at cut time. Base holds cross-domain taste — the transparency principle,
transient philosophy, variation instinct, pacing taste. A claim belongs in a
domain doc only if it would be wrong outside that domain. Per-band or
per-person variants live as sections inside a domain doc until they outgrow it.
Git history is the style-evolution record, which is why these are committed
files and not cache.

Future domains (vlog, talking-head) are the same structure and out of v1 scope.

## Provenance tags

**Every claim carries a tag.** A profile whose claims cannot be told apart by
how well they are known is a black box with extra steps — the tags are the
whole reason this is a document rather than a prompt.

| Tag | Means |
| --- | --- |
| `[stated principle]` | The director said so. Principles are the defaults. |
| `[measured — N projects, n=<cuts>, <context>]` | The corpus says so; the evidence row is in `corpus.md`. Sample size and context are part of the tag (#21 policy 4). |
| `[review feedback, YYYY-MM]` | Landed from a cut-review round, dated. |
| `[believed, unverified]` | Held on thin evidence — reasoning from the instrument, one project, or a handful of cuts. |

Four rules keep the tags honest:

- **Measurement never sands off a principle.** Where the corpus disagrees with
  a stated principle, both stay and the conflict is written down as a conflict
  (#13). A measured deviation is a fact about the edits; the principle is a
  fact about the intent, and losing either one loses the interesting part.
- **Thin support downgrades.** Few instances, or support from a single
  project, means `[believed, unverified]` with the partial evidence noted —
  however clean the number looked. No minimum-n gate blocks a first analysis
  run; the tag carries the weakness instead (#21 policy 4).
- **Context is attributed, agreement graduates.** A measured claim names its
  context (`concert` or `studio-session`). A claim that holds across both
  graduates to `base.md` (#21 policy 3).
- **Taste beats recency, and recent work breaks ties.** Membership is "would
  you cut it the same way today?", not a date cutoff; where old and new work
  disagree, the profile resolves toward the recent (#21 policy 2).

## Angle sidecars

One JSON file per corpus project, labelling each camera on **two axes**:
**subject** (who or what — drummer, keys, ensemble) × **character** (`wide`,
`tight`, `moving`). The style profile speaks in roles; the camera→role mapping
for a given project lives here, with the project, not in the profile (#13).

#13 says that mapping "lives with the project, not the style doc". That is read
here as *one file per project, keyed to the project, kept out of the profile* —
not as a file sitting beside the Resolve project on the media drive. A sidecar
next to the footage is outside version control, and on an archived project it
is on whichever drive that project was archived to; the labels are the agent's
own document and their history is worth as much as the profiles'. **The
director confirmed this reading on 2026-08-07** — sidecars stay in the repo.

```json
{
  "project": "2026-06_Zinc_and_Monkfish",
  "context": "concert",
  "labelled": "2026-08-06",
  "confirmed_by_director": false,
  "angles": {
    "A001_C012.mov": {
      "role": "drums-tight",
      "subject": "drums",
      "character": "tight",
      "note": "drum kit three-quarters from house left; locked off",
      "confidence": "high",
      "evidence": ["…/frames/A001_C012_00-01-30.jpg"]
    }
  }
}
```

- **`role`** is the only key `correlate_timeline` consumes, and it is what
  cross-project claims group on — so keep the vocabulary small and reuse it
  across projects. `<subject>-<character>` is the default shape.
- **`subject`** and **`character`** are the two axes kept apart, because
  "the drummer" and "a moving shot" are different facts about the same camera
  and the profile makes claims about each.
- **`confidence`** and **`evidence`** are why a claim resting on this angle is
  thin or not: a `low` here is a reason a corpus claim downgrades.
- **`confirmed_by_director`** — `false` until confirmed, then the date it was
  (`"2026-08-07"`). Labelling is auto-labelled by Claude and confirmed once;
  reruns never re-ask (#13). Until that flip, every claim resting on these
  labels is unverified in a second, sharper way than usual: not merely thin,
  but possibly inverted.

On a **multicam** project the confirmation is not optional politeness. Resolve
exposes no angle→source mapping — `GetClipProperty("Angle")` comes back empty
and a timeline item carries only the angle name — so which camera is behind
`Video 1` can be guessed (the home angle usually holds the most screen time)
but never read. Ask, before the roles are used for anything.

An entry with no `role` is dropped by `correlate_timeline` rather than refused,
so a half-labelled project still measures — its shots land under `unlabelled`.

Pass a sidecar by lifting its `angles` object straight through; the tool takes
labels, never a path:

```python
angles = json.loads(Path("styles/angles/<project>.json").read_text())["angles"]
correlate_timeline(beats=..., timeline=..., angles=angles)
```

## The analysis workflow, per corpus project

Per #13, and in this order:

1. **Pre-flight** (#21). Open the project. Spot-check `GetClipProperty("File
   Path")` on timeline clips — archived projects under `Archive/Client` are
   the likely relink candidates. Confirm **2+ source angles**: single-camera
   timelines leave the corpus at labelling time without a re-decision, because
   angle-switch behaviour is the core signal. Confirm the concert audio is
   reachable and that frame grabs render.
2. **Angle labelling.** `inspect_timeline` for the distinct source clips,
   `grab_frames` on each at three well-separated times — one grab in three is
   a lens cap or a black frame — then read the grabs and write the sidecar.
   Clip and file names are a hint layer when they cooperate, never the label.
   The director confirms or corrects once.
3. **Music analysis.** `analyze_music` on the concert audio for beats,
   downbeats and energy; `analyze_structure` for tunes and solo changes. A
   concert cut to a director-supplied master mix uses that file; otherwise the
   timeline's own mix has to be exported, and `separate_stems(scope="timeline")`
   acquires it on the way. **Rubato regions are excluded from cut-placement
   evidence** via beat-confidence gating — a grid fitted to free time measures
   nothing.
4. **`correlate_timeline`** with the sidecar's labels. This is the one tested
   measurement path: Claude interprets its records and never recomputes
   statistics ad hoc.
5. **Draft.** Update the profile sections from the records, tag every claim,
   record the row in `corpus.md`. The director reviews; then commit.

Read **across** the corpus before claiming, not one project at a time: the
number that matters is the distribution, and the tag a claim is entitled to is
set by how many cuts and how many projects stand behind it.

**`alignment.mode` decides whether a result is usable at all.** `audio_clip`
means the times were read through the audio the analysis ran on;
`timeline_start` means the timeline said nothing about where the mix sits and
the times count from its own first frame instead. A whole file measured against
the wrong zero looks exactly like a whole file measured against the right one,
so a corpus pass records the mode per timeline and excludes `timeline_start`
readings from every timing claim.
