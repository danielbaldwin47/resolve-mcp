"""The titles-file schema v1, served verbatim by ``get_titles_schema``.

The text below is the contract settled by the titling workflow spec (issue #14) and the
title-control research (issue #5). It is data, not documentation: Claude authors
``titles.json`` against exactly this, so the example stays annotated and is served
character-for-character as written here. Editing it changes the contract — do it in a
ticket, not in passing.

Two decisions in it are worth naming here, because they are what make titling
re-runnable rather than merely automatic:

* **Event times are offsets from the song's blue marker, never absolute frames.** The
  marker name is the song key, which is the explicit join between the file and the
  timeline (#14 §1). A rebuild makes a new timeline version; re-marking the songs on it
  is all that is needed for the same titles file to land correctly again. Absolute
  frames would silently point at the wrong music the moment a segment's length changed.
* **Titles are not in the cut file, and the cut file is not read here.** The two files
  together describe a deliverable, and each owns its own tracks — ``build_timeline``
  never touches the Titles track, and ``apply_titles`` never touches V1.
"""

from typing import Final

SCHEMA_VERSION: Final = 1
"""The only titles-file ``schema`` value this server accepts."""

TRACK_NAME: Final = "Titles"
"""The track ``apply_titles`` owns. Everything on it is the server's to clear and re-place."""

KINDS: Final = ("title", "personnel", "custom")
"""What an event is for. ``kind`` picks the template unless ``template`` overrides it."""

ROUTES: Final = ("textplus", "png")
"""How an event is rendered. Only ``textplus`` is placed today; ``png`` is the PNG route."""

SUPPORTED_ROUTES: Final = ("textplus",)
"""The routes this build can place. A declared-but-unsupported route is a T6 error."""

ANNOTATED_EXAMPLE: Final = """\
{
  "schema": 1,                       // supported-version check
  "timeline": "sunset-set v4",       // optional; default = the current timeline

  // GUI-authored Text+ templates, already in the media pool. Identity = clip name
  // + optional bin, resolved to exactly one media-pool clip at apply.
  "templates": {
    "title":     { "clip": "Song Title", "bin": "Titles/Templates" },
    "personnel": { "clip": "Personnel" }
  },

  "songs": [
    {
      "key": "sunset-boulevard",     // the name of a blue marker on the timeline (§2)
      "events": [
        {
          "id": "t01",               // required, unique across the whole file
          "kind": "title",           // title | personnel | custom
          "route": "textplus",       // textplus (png = the PNG route, not this one)
          "template": "title",       // optional; default = kind
          "text": "Sunset Boulevard",     // the final string, verbatim; \\n for a line break
          "in": 240, "out": 720,     // frames from the song's marker, half-open [in, out)
          "fade": { "in": 24, "out": 36 },  // optional; frames of opacity ramp (§3)
          "note": "let four bars play"      // free text; server ignores; feeds the report
        }
      ]
    }
  ]
}"""
"""The annotated schema example, verbatim. Comments are ``//`` — strip before parsing."""

_TRACK: Final = """\
## 1. One track, owned outright

`apply_titles` owns the topmost video track named `Titles`, and creates it if the
timeline has none. The name is not configurable: one track is owned outright, and
a tool that could be pointed at any track could clear one it does not own. Every
re-run clears that track completely and re-places from the file, so it always
holds exactly what the file says and nothing else. Put nothing there by hand that
you want to keep. No other track is read or written — the cut on V1 is never
touched, which is what makes "rebuild the cut, re-apply the titles" safe in
either order."""

_ANCHORS: Final = """\
## 2. Songs are anchored to blue markers, not to frames

A song's `key` is the *name* of a blue marker on the target timeline; every
event's `in`/`out` counts frames forward from that marker. So the join between
this file and the timeline is explicit and survives a rebuild: build the new
version, mark the songs on it, re-apply this file unchanged.

Exactly one blue marker must carry each key (T7). A blue marker with no matching
song is reported as a warning, never an error — a set list is often titled a song
at a time."""

_FADES: Final = """\
## 3. Fades are Fusion opacity keyframes

Resolve's clip-level fade handles are not reachable from the scripting API at
all, so a fade is written *inside* the placed instance's Fusion comp: a
BezierSpline on the Text+ node's `Opacity1`, keyframed 0 -> 1 over `fade.in` at
the head and 1 -> 0 over `fade.out` at the tail. Omit `fade` for a hard cut on
and off. `fade.in + fade.out` must fit inside the event's duration (T4).

Fade timing is a judgement call, not a default: fast tune roughly 0.5-1 s,
ballad roughly 2-4 s. The report says per event whether the written spline read
back, because a Resolve build that will not answer for an animated input is the
one thing here no test can check."""

_TIME: Final = """\
## Time

Frames authoritative, half-open `[in, out)` — duration = out - in. Offsets are
from the song's marker, so `in: 0` is the marker frame itself. Seconds are not
accepted here: the fps that would convert them belongs to the timeline, and a
titles file outlives any one version of it."""

_RERUN: Final = """\
## 4. Re-running

`apply_titles` is declarative and idempotent: same file, same timeline, same
result. Nothing is validated against the previous run, and no state is kept
between runs — the timeline *is* the state. Edit the file and re-apply to move,
retime or re-word titles: re-applying costs one clear and one append.

A typo in a title already placed does not need that. `edit_title` writes new
words, or new exposed template params, into that one instance in place and
leaves the rest of the track alone. It edits the timeline and not this file, so
the next apply puts the old wording back — change both when the fix is one to
keep."""

_RULES: Final = """\
## 5. Validation

9 hard errors (T1-T9) block an apply; 2 warnings (W1-W2) never do. A single
error aborts before the Titles track is touched, so a refused apply always
leaves the timeline exactly as it was. Every finding is
`{rule, id, message, fix_hint}` — see the `rules` field of this result."""

SCHEMA_DOC: Final = "\n\n".join(
    [
        "# Titles-file schema v1",
        _TRACK,
        _ANCHORS,
        "## Schema\n\n```jsonc\n" + ANNOTATED_EXAMPLE + "\n```",
        _TIME,
        _FADES,
        _RERUN,
        _RULES,
    ]
)
"""The full schema document: prose contract with the annotated example embedded."""
