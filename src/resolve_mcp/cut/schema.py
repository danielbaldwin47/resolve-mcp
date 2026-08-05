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
    }
  ],

  "overlays": [
    {
      "id": "b03",                   // ids share one namespace with segments
      "source": "broll_pan",
      "in": 1200, "out": 1440,
      "over": { "segment": "s014", "offset": 24 },  // anchor + frames into it
      "note": "cover retake seam"
    }
  ]
}"""
"""The annotated schema example, verbatim. Comments are ``//`` — strip before parsing."""

_PLACEMENT: Final = """\
## 1. Placement model: sequential V1, positioned overlays

V1 segments are butt-joined in array order; record positions are computed at
build. Gaps and flash-frame positions are unrepresentable rather than checked,
and duration edits never require downstream position fixes. Only overlays carry
position — anchored, not absolute (§4)."""

_TAKES: Final = """\
## 3. Takes

- Selector = [main, alternates in order]; the selected take is main. The main
  slot *is* the current selection, always.
- Every alternate's duration must equal main's — in-place `swap_take` cannot
  ripple a sequential timeline. An unequal-length take choice is a main-segment
  edit plus a rebuild.
- Swap sync: after `swap_take`, you edit the cut file — alternate promoted to
  main, main demoted. The server never writes your cut file."""

_OVERLAYS: Final = """\
## 4. Overlays

Anchored to `{segment, offset}`, never absolute timeline frames — they ride with
the content they cover through tightening passes; build computes absolute
position = the anchor segment's computed start + offset. An overlay may run past
its anchor's end (seam coverage) but must land inside the total V1 span. Video
only: audio always omitted, no alternates, V2."""

_TITLES: Final = """\
## 5. Titles: not in the cut file

`apply_titles` and `titles.json` own the Titles track. `build_timeline` never
touches it; after a rebuild, re-run `apply_titles` on the new version. The cut
file plus titles.json together fully describe a deliverable."""

_VERSIONING: Final = """\
## 6. Versioning and naming

- Timelines materialize as `<name> v<N>`; build scans `^<name> v(\\d+)$` and
  takes max + 1. One version per review round.
- The cut file is one evolving `<name>.cut.json` beside songs.json, titles.json
  and sidecars — no per-version copies; history is versioned timelines + git.
- The build report echoes the cut file's BLAKE2b hash and the resulting version
  name, so every timeline is traceable to the exact cut state that built it and
  `swap_take` drift is detectable."""

_TIME: Final = """\
## Time

Frames are authoritative; ranges are half-open `[in, out)`, so duration =
out − in and adjacent takes share a boundary frame without overlap ambiguity.
Seconds are accepted at the tool boundary with explicit snapping — floor for
in-points, ceil for out-points."""

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
    ]
)
"""The full schema document: prose contract with the annotated example embedded."""
