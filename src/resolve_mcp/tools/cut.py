"""The cut-file tools: the contract, the dry run, the build, and the one in-place edit.

A cut file is yours — you author it, the server never writes it. These tools are how you
find out what it must contain, whether the one you wrote will build, what happened when it
did, and how to flip a shot's angle on a built version without starting over.
"""

from __future__ import annotations

from typing import Any

from ..analysis import virtual
from ..analysis.transcript import DEFAULT_LOW_CONFIDENCE
from ..resolve import build, cut, takes
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def get_cut_schema() -> dict[str, Any]:
    """Return the cut-file schema v1, its annotated example, and the validation rules.

    Read this before authoring or editing a cut file — the format is not guessable and
    the example is the contract. `rules` lists every error that blocks a build and every
    warning that does not. Needs no project open.
    """
    return cut.get_cut_schema()


@tool
def validate_cut(
    cut_file: str,
    min_segment_frames: int = cut.MIN_SEGMENT_FRAMES,
) -> dict[str, Any]:
    """Dry-run a cut file and return every error and warning it has, with fix hints.

    Run this after every edit: build_timeline runs the identical checks pre-flight, so a
    file that is valid here will not abort a build. Each finding names the rule, the
    segment or overlay id, what is wrong and how to fix it — all of them at once, not the
    first. `min_segment_frames` tunes the W1 flash-frame warning only; it never blocks.
    """
    connection = get_connection()
    return cut.validate_cut(connection, cut_file, min_segment_frames)


@tool
def virtual_transcript(
    cut_file: str,
    transcripts: dict[str, str] | None = None,
    below: float = DEFAULT_LOW_CONFIDENCE,
) -> dict[str, Any]:
    """Read a cut file back as the words it will contain, before building it.

    This is the rough-cut self-review. You assembled A-roll by reading transcripts and
    choosing takes; this tells you what the assembly now says, so the quality bar is met
    against the delivered words rather than against your memory of the plan. Needs no
    project open and touches no timeline — a cut file and its transcripts are documents.

    `transcripts` maps a source alias in the cut file to the transcript document
    transcribe_audio wrote for that source. Aliases you leave out are reported (W3), not
    guessed at: a b-roll clip with no speech belongs out of the mapping. `below` is the
    confidence under which a surviving word is flagged as an uncertainty.

    The result reads the cut back three ways: `text` is the whole thing as prose,
    `segments` is the same split per shot, and `words` places each word at the frame it
    lands on (spilled to disk past the inline cap). `seams` reports every join, which ones
    put two shots of one source together, and the overlay covering each. There is no
    `errors` list because this cannot produce one — everything it finds is a warning: a
    word cut in half (W3), a run of words surviving on both sides of a seam because two
    takes were kept (W4), a source with no transcript (W5), a low-confidence word
    delivered (W6), and an uncovered same-source seam (W7). The numbering carries on from
    validate_cut's W1 and W2 rather than restarting, because both sets of warnings are
    about the same cut file and a second W1 meaning something else would be a trap.

    W4 compares each seam against the one before it, so two takes of a line separated by
    another shot read back clean — the doubling this catches is the adjacent kind.

    Nothing here decides anything. Whether a repeat is a retake to drop or an echo you
    meant, whether filler stays, whether a mid-word cut is sloppy or deliberate — that
    reading is yours, and no cut is refused on the strength of it.
    """
    return virtual.virtual_transcript(cut_file, transcripts, below=below)


@tool
def build_timeline(
    cut_file: str,
    min_segment_frames: int = cut.MIN_SEGMENT_FRAMES,
) -> dict[str, Any]:
    """Build a cut file into a new `<name> v<N>` timeline and report what landed.

    Every build makes a new version and never touches an earlier one, so rebuilding after
    an edit is always safe. The segments land butt-joined in document order over one
    continuous master-audio clip; positions are computed, so gaps cannot happen. Overlays
    land on V2 at their anchor segment's start plus the offset — never a frame you state —
    so tightening a segment and rebuilding leaves every overlay over the same content.

    The validate_cut rules run first: a single error aborts before any timeline is created,
    and comes back with the same per-segment findings. The report echoes the cut file's
    content hash, which is what ties the timeline back to the exact cut that made it —
    record it if you note the version anywhere. A failure names what did not land; a
    partially built version, if one was made, is scrap and can be deleted.
    """
    connection = get_connection()
    return build.build_timeline(connection, cut_file, min_segment_frames)


@tool
def swap_take(
    cut_file: str,
    segment: str,
    take: int,
    timeline: str | None = None,
) -> dict[str, Any]:
    """Switch which take a built segment shows, in place, without rebuilding the timeline.

    This is the one edit that does not go through a rebuild, and it only works because
    alternates are the same length as the take they replace. `take` is the 1-based slot in
    the selector the cut file describes: 1 is the segment's own `source`, 2 onwards are its
    `alternates` in order. The report lists the whole selector, so the numbers never have to
    be guessed, and names the version it touched — `timeline` defaults to the open one.

    Afterwards the timeline and the cut file disagree, and only you can fix that: `sync`
    holds the exact segment fields to write — the chosen alternate promoted to main, the
    old main demoted into its slot — so a later rebuild reproduces what is on screen now.

    A shot is found by the position and length this cut file computes for the segment, and
    its selector must be the size the file describes; anything else is refused as drift with
    a fix rather than swapped on a guess. That is a shape check, not proof of provenance —
    if it matters which version you are on, compare `content_hash` against the build report.
    """
    connection = get_connection()
    return takes.swap_take(connection, cut_file, segment, take, timeline)


TOOLS: tuple[Any, ...] = (
    get_cut_schema,
    validate_cut,
    virtual_transcript,
    build_timeline,
    swap_take,
)

__all__ = [
    "TOOLS",
    "build_timeline",
    "get_cut_schema",
    "swap_take",
    "validate_cut",
    "virtual_transcript",
]
