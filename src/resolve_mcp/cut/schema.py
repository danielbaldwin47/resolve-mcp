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

_RULES: Final = """\
## 7. Validation

11 hard errors (E1-E11) block a build; 3 warnings (W1, W2, W8) never do. W3-W7 are
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
    ]
)
"""The full schema document: prose contract with the annotated example embedded."""
