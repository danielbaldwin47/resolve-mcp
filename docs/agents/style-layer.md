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

- **`role`** is what cross-project claims group on — so keep the vocabulary
  small and reuse it across projects. `<subject>-<character>` is the default
  shape.
- **`subject`** and **`character`** are the two axes kept apart, because
  "the drummer" and "a moving shot" are different facts about the same camera
  and the profile makes claims about each. `subject` is also the second key
  `correlate_timeline` consumes (#181): with a solo map it answers whether a
  shot is on the player out front, on the ensemble, on a player who was not
  soloing, or on neither (an audience camera, a room shot). It is read from
  `subject` where an entry names one and otherwise from the subject half of a
  `<subject>-<character>` role — a one-word role is a *character* and labels no
  subject. `ensemble` is the whole band; `soloist` is a camera pointed at
  whoever is out front, whose shots are on the soloist by construction rather
  than by measurement — the reading says which of the two it was, and how many
  of a cut's soloist seconds came from a camera taken at its word.
- **`voice`** — optional, and only where this sidecar's subjects and the solo
  map's stems are different words for the same player: `{"subject": "mike",
  "voice": "wind"}`. The join uses it; a subject the solo map never names reads
  as neither a player nor the band, which is right for an audience camera and
  wrong for a horn player nobody aliased.
- **`confidence`** and **`evidence`** are why a claim resting on this angle is
  thin or not: a `low` here is a reason a corpus claim downgrades.
- **`confirmed_by_director`** — `false` until confirmed, then the date it was
  (`"2026-08-07"`). Labelling is auto-labelled by Claude and confirmed once;
  reruns never re-ask (#13). Until that flip, every claim resting on these
  labels is unverified in a second, sharper way than usual: not merely thin,
  but possibly inverted.
- **`stated_by_director`**, **`inferred`** and **`inference_basis`** — for a
  label that came from a sentence rather than from a frame. Quote what was
  actually said, list which of `subject`/`character` was *not* in it, and say
  what the rest was filled in from. A description is strong evidence for what a
  camera **is** ("the fixed wide side-view") and no evidence at all for what it
  **points at** moment to moment — so an angle carrying `"inferred":
  ["subject"]` must not be counted toward a claim that turns on subject. This
  is finer-grained than `confidence`, which grades the whole entry: an angle can
  be certain about its framing and a guess about its subject at the same time.
  Entries 3 and 5 in the corpus are labelled this way.

### Labelling a multicam: grab the render, not the sources

Resolve exposes no angle→source mapping. `GetClipProperty("Angle")` comes back
empty and a timeline item carries only the angle name, so labelling from the
multicam's *source clips* can establish what each camera is and still not say
which camera is `Video 1`. That gap is what made the anchor's sidecar need a
director's eye, and the screen-time guess behind it (the home angle usually
holds the most) was right once — which is one data point, not a method.

**Where the timeline has a finished render, there is no gap.** Grab a frame of
the render at a moment a given angle is on screen: the frame *is* that angle.
Nothing is inferred, and `confirmed_by_director` is not needed to trust it.

1. Read the shots (`correlate_timeline`'s reader, or the same walk) and group
   them by angle name.
2. For each angle take its longest shot or two, and the frame at the midpoint —
   a midpoint is safely inside the shot even if a transition softens the edges.
3. Convert to a render time: `(record_in - start_frame) / fps`. **Check the
   render actually spans the timeline first** — compare its duration against
   `(end_frame - start_frame) / fps`. On the three entry-2 tunes these agreed
   to within 0.02 s, under a frame; a render of a section rather than the whole
   timeline would not, and would put every label on the wrong angle.
4. Read the frames and write the labels.

Where there is no render — the anchor, `Mike Tucker Scullers`, `Monkfish Main`
— the gap is real and the director's eye is the only way to close it. Ask
before the roles are used for anything, and note that timing claims need no
labels at all: a timeline can be measured while its angles are still
unlabelled, which is what makes asking cheap. Measure first, ask once, and
**re-run with the sidecar attached rather than writing the role shares in by
hand** — `angles` is part of the cache key, so a labelled run is a new result
file that can be diffed against the unlabelled one. On entries 3 and 5 every
number was identical, which is the only way to know the labels added names and
nothing else.

An answer in words closes less than a frame does. It fixes what each camera is
and which angle number it is; it does not say what a moving camera was
following at any given moment. Record the difference in the entry
(`stated_by_director` / `inferred`) instead of flattening it, or the corpus
will use a sentence as though it were a look.

Angle *numbers* are per-multicam and do not carry across timelines even inside
one project: on the Judson's show `Angle 10` is the drummer on one tune and the
roaming camera on another. Label every timeline separately.

An entry with no `role` is dropped by `correlate_timeline` rather than refused,
so a half-labelled project still measures — its shots land under `unlabelled`,
in the role shares and in the on-soloist track alike, and the track counts that
screen time apart from its shares rather than in them.

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

**`alignment.mode` decides whether a result is usable at all.** A whole file
measured against the wrong zero looks exactly like a whole file measured
against the right one, so a corpus pass records the mode per timeline:

- **`audio_clip` with `matched: true`** — the clip carrying the analysed audio
  was found on the timeline. Trust it.
- **`given`** — the caller named the frame. Trust it exactly as far as the
  caller's reason: on entry 2 that reason is a render whose duration matches
  its timeline to within a frame, which is checkable; a remembered number is
  not the same thing.
- **`audio_clip` with `matched: false`** — the times were taken off whichever
  audio clip happened to be first. Excluded from timing claims.
- **`timeline_start`** — the timeline said nothing at all. Excluded.

Hand-edited concerts routinely need `given`, and it is worth knowing why before
you reach for it: where the music arrives through a multicam's own audio angle,
*no* clip on the timeline is the mix, and the in point that can be read belongs
to the multicam's timebase. Measured against the entry-2 timelines' own A1 the
error would have been 15 s on one tune and 40 s on another — invisible in the
output, and enough to move every cut to a different beat.
