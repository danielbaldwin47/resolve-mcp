"""The settled cut-file schema v1, served verbatim by ``get_cut_schema``.

The text below is the contract from the schema resolution (issue #20). It is
data, not documentation: Claude authors cut files against exactly this, so the
example stays annotated and is served character-for-character as written here.
Editing it changes the contract — do it in a ticket, not in passing.
"""

from typing import Final

SCHEMA_VERSION: Final = 1
"""The only cut-file ``schema`` value this server accepts."""

ANNOTATED_EXAMPLE: Final = """\
{
  "schema": 1,                       // supported-version check
  "timeline": {
    "name": "sunset-set",            // version base name (§6)
    "fps": 59.94,                    // declared; sources validated against it
    "bin": "Cuts"                    // optional; default media pool root
  },

  // Source aliases: segments reference by alias. Identity = clip name
  // + optional bin, resolved to exactly one media-pool clip at validate.
  "sources": {
    "gtr_close":  { "clip": "C0012.mp4", "bin": "Angles", "sync_offset": 1432 },
    "master_mix": { "clip": "sunset-master.wav" }
  },
  // sync_offset: frames, from the sync-reference timeline. Informational —
  // Claude plans with it; server never computes or validates with it.

  // Optional continuous master-audio clip (concert substrate).
  // Rough cut omits this block; A-roll segment audio goes to A1.
  "audio": { "source": "master_mix", "in": 0, "out": 323000 },

  // Optional: how the picture leaves and how the mix goes with it (§8).
  // Omit and the cut ends the way v1 always did — a hard cut on the last
  // frame of picture, mix still hot.
  "tail": {
    "type": "dissolve_to_black",  // or "hard_to_black" (audio fade only)
    "duration_frames": 142,       // the dissolve, reaching back into the last shot
    "audio_fade_frames": 125      // optional; fades the mix's last N frames to silence
  },

  "segments": [
    {
      "id": "s014",                  // required, unique, author-chosen
      "source": "gtr_close",
      "in": 14032, "out": 14210,     // source-clip frames, half-open [in, out)
      "audio": false,                // optional; default false when master
                                     // audio block present, true when absent
      "alternates": [                // optional; same duration as main
        { "source": "keys_wide", "in": 8100, "out": 8278 }
      ],
      "note": "drum fill response"   // free text; server ignores; feeds cut report
    },

    // A gap: literal black on V1. Takes record time, places nothing, and so moves
    // everything after it. id + gap + optional note, and nothing else (§1).
    { "id": "g001", "gap": 58, "note": "false ending before the landing shot" }
  ],

  "overlays": [
    {
      "id": "b03",                   // ids share one namespace with segments and gaps
      "source": "broll_pan",
      "in": 1200, "out": 1440,
      "over": { "segment": "s014", "offset": 24 },  // anchor + frames into it
      "track": 2,                    // optional; V2 by default, 2-8 (§4)
      "note": "cover retake seam"
    }
  ]
}"""
"""The annotated schema example, verbatim. Comments are ``//`` — strip before parsing."""

_PLACEMENT: Final = """\
## 1. Placement model: sequential V1, positioned overlays

V1 entries run in array order; record positions computed at build, never stated.
Duration edits therefore never require downstream position fixes, and no entry can
name a frame that is wrong.

An entry is a **segment** (plays a clip) or a **gap** (literal black: `{"id", "gap":
<frames>}`, plus an optional `note`, and nothing else — no source, in, out, audio or
alternates). A gap places nothing and occupies record time, so it moves everything
after it. That is how a cut opens cold, stages a false ending, or ends on black.

Two things follow. An overlay may anchor **over a gap** — a V2 shot bridging black
into the first picture is one anchor, not a special case. And black at the *end* of
a cut is real only if an overlay covers it: nothing is appended for a gap, and a
timeline ends at its last item (W8 says so).

Only overlays carry position — anchored, not absolute (§4)."""

_TAKES: Final = """\
## 3. Takes

- Selector = [main, alternates in order]; selected take = main. The main slot
  *is* the current selection, always.
- `swap_take` counts that order: take 1 = the segment's own `source`, take 2
  onwards = `alternates` in document order. Indexes, not aliases — the same
  source can appear twice in one selector (two passes from one camera).
- Every alternate's duration must equal main's — in-place `swap_take` can't
  ripple a sequential timeline; an unequal-length take choice is a main-segment
  edit + rebuild. Also dodges untested Resolve behavior on mismatched selector
  lengths.
- Swap sync: after `swap_take`, Claude edits the file — alternate promoted to
  main, main demoted into its slot, so the selector order survives a rebuild.
  The swap report hands back those exact fields. The server never writes the
  cut file."""

_OVERLAYS: Final = """\
## 4. Overlays

Anchored to `{segment, offset}`, never absolute timeline frames — they ride with
the content they cover through tightening passes; build computes absolute
position = segment's computed start + offset. The anchor may be a gap as readily
as a segment. May run past the anchor's end (seam coverage), must land inside the
total V1 span. Video only, audio always omitted, no alternates.

`track` is optional and defaults to 2. It is the video track the overlay rides on,
between V2 and V8; V1 belongs to the segments. Two overlays may cover the same
frames only on different tracks (E10) — that is what a second layer is for."""

_TITLES: Final = """\
## 5. Titles: not in the cut file

`apply_titles` + `titles.json` own the Titles track. `build_timeline` never
touches it; after a rebuild, re-run `apply_titles` on the new version. Cut JSON
+ titles.json together fully describe a deliverable."""

_VERSIONING: Final = """\
## 6. Versioning/naming

- Timelines materialize as `<name> v<N>`; build scans `^<name> v(\\d+)$`, takes
  max+1. One version per review round.
- Cut file: one evolving `<name>.cut.json` beside `songs.json`/`titles.json`/
  sidecars — no per-version copies; history = versioned timelines + git.
- Build report echoes the cut-file BLAKE2b hash + resulting version name: every
  timeline traceable to the exact cut state that built it; `swap_take` drift
  detectable."""

_TIME: Final = """\
## Time

Frames authoritative, half-open `[in, out)` — duration = out − in; adjacent
takes share a boundary frame without overlap ambiguity. Snapping (floor in,
ceil out) at the tool boundary."""

_TAIL: Final = """\
## 8. The tail: dissolve to black, and the fade under it

A gap is the only black v1 could author, and a gap is a *hard* edge: the picture stops on
one frame. The finished work in this corpus does not do that. The picture leaves while the
band is still playing, over seconds, and the mix fades under it and outlives it — so a cut
that can only hard-cut cannot end the way the deliverables end.

`tail` is that device, and it is one object because it is one gesture:

- `type` is `dissolve_to_black` (the picture ramps to black) or `hard_to_black` (the
  picture stops, the mix still fades — two of the five surveyed deliverables do exactly
  this). A cut with no `tail` at all is the third shape: hard out, mix hot to the last
  frame.
- `duration_frames` is the dissolve. It reaches **back** into the last shot on V1 and
  lands on black at the cut's last picture frame, which is how the corpus places it — the
  last shot must therefore be longer than the dissolve. Required for `dissolve_to_black`,
  forbidden for `hard_to_black`.
- `audio_fade_frames` is optional and independent: the master mix's last N frames fade to
  silence, ending where the mix ends. It needs an `audio` block to fade. Because the mix
  is usually authored to run past the last picture, the fade normally starts under the
  dissolve and finishes after it — which is the measured shape, not a coincidence.

Measured convention (the concert style profile, §5b, five deliverables from one night): the
picture is gone before the file is, the dissolve runs roughly 6-10 s, and the audio
reaches digital silence in the last 0.2-0.5 s. Taurus, the closest read: a 5.923 s
dissolve (142 frames at 23.976) over a ~5.2 s audio fade.

**How it is built, and why that matters.** The Resolve scripting API cannot cut a
transition — there is no call for it on any object this server touches (probed live on
21.0.3: `TimelineItem` exposes `SetProperty` for a *static* `Opacity` and nothing for
audio level at all). So a cut whose `tail` has a transition to cut in is built twice: the
shots are appended to a staging timeline named `<name> v<N> (tail staging)`, that timeline
is exported as OTIO,
the transitions are edited into the document, and the document is imported back as
`<name> v<N>`. The staging timeline is deleted once the transitions and the shot
placements have both been read back off the import. A `hard_to_black` tail with no
`audio_fade_frames` has nothing to inject, so it builds directly under its own name. Two
consequences worth stating: a build with a dissolve or a fade costs an export and an
import, and a build that gets as far as the staging timeline and then cannot round-trip
**fails** rather than quietly delivering a hard cut — the staging timeline is left in the
project, named, and the error says so."""

_RULES: Final = """\
## 7. Validation

12 hard errors (E1-E12) block a build; 4 warnings (W1, W2, W8, W9) never do. W9 is the
one that says a rule *could not run*: Resolve reported no media bounds for that clip, so
the range was never checked against them. W3-W7 are
`virtual_transcript`'s and describe the same cut file — one document, one numbering.
The list is identical in the `validate_cut` dry run and `build_timeline`'s pre-flight,
and a failing file aborts before Resolve is touched. Every finding is
`{rule, id, message, fix_hint}` — see the `rules` field of this result."""

SCHEMA_DOC: Final = "\n\n".join(
    [
        "# Cut-file schema v1",
        _PLACEMENT,
        "## 2. Schema\n\n```jsonc\n" + ANNOTATED_EXAMPLE + "\n```",
        _TIME,
        _TAKES,
        _OVERLAYS,
        _TITLES,
        _VERSIONING,
        _RULES,
        _TAIL,
    ]
)
"""The full schema document: prose contract with the annotated example embedded."""
