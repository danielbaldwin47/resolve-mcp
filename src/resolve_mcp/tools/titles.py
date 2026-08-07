"""The titling tools: the contract, the dry run, and the apply.

Titles are declarative in the same way the cut is — you author `titles.json`, the server
places it — but they never enter the cut file. The two files own different tracks, so a
cut can be rebuilt and the same titles re-applied to the new version unchanged.
"""

from __future__ import annotations

from typing import Any

from ..resolve import apply, title_edit, titles
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def get_titles_schema() -> dict[str, Any]:
    """Return the titles-file schema v1, its annotated example, and the validation rules.

    Read this before authoring or editing a titles file — the format is not guessable and
    the example is the contract. In particular, event times are offsets from the song's
    blue marker rather than timeline frames, which is what lets one titles file survive a
    rebuild. `rules` lists every error that blocks an apply and every warning that does
    not. Needs no project open.
    """
    return titles.get_titles_schema()


@tool
def validate_titles(titles_file: str) -> dict[str, Any]:
    """Dry-run a titles file and return every error and warning it has, with fix hints.

    Worth running after every edit: apply_titles runs the identical checks first, and
    unlike a build it works on a timeline you already have — so knowing the file is good
    before the Titles track is cleared is the cheap way round. Each finding names the
    rule, the event or song it is about, what is wrong and how to fix it — all of them at
    once, not the first.
    """
    return titles.validate_titles(get_connection(), titles_file)


@tool
def apply_titles(titles_file: str) -> dict[str, Any]:
    """Place every event in a titles file onto the timeline's own Titles track.

    Declarative and re-runnable: the topmost video track named `Titles` belongs to this
    tool, and every apply clears it whole and re-places from the file, so the same file
    always produces the same track. Nothing else on the timeline is touched — rebuild the
    cut, mark the songs on the new version, re-apply this file unchanged.

    Each song's events are positioned from the blue marker whose name is the song key, so
    the file needs no timeline frames in it. An event takes one of two routes and both are
    placed in the same pass: `textplus` puts an instance of a media-pool template down and
    writes the file's text and an opacity-keyframe fade into its Fusion comp, because
    Resolve exposes no clip-level fade to the API at all; `png` places a designed card
    already exported to frames with alpha, whose words and ramps are in the pixels. Cards
    are imported for you into `04_Assets/Text/<song>`, once, and reused on later applies.

    The validate_titles rules run first: a single error aborts before the track is
    cleared, so a refused apply leaves the previous titles in place. The target timeline
    is opened in Resolve as part of the apply, since an append lands on whatever timeline
    is current. The report says per title whether its fade read back — a fade that did
    not is the one thing here worth checking by eye in the GUI.
    """
    return apply.apply_titles(get_connection(), titles_file)


@tool
def list_titles(timeline: str | None = None) -> dict[str, Any]:
    """Read back every title standing on a timeline's Titles track, and what it exposes.

    The counterpart to apply_titles: that one writes the track from a file, this one reads
    the track as it now is. Each title reports its position along the track, where it sits,
    the words it says, and the Fusion inputs its template *sets* — read off the placed
    instance itself, since a media-pool template has no comp to ask.

    `params.values` is a summary, not the limit of what is editable: a stock Text+ carries
    194 settable inputs and nearly all sit at their stock value, so what is reported is the
    handful the template moved — its font, size and justification — which is what tells one
    template from another. `params.detail` gives both counts. edit_title takes any id the
    node has, and names them all if you get one wrong.

    Run it before edit_title to copy the exact wording and to see what the template sets. A
    build that will not enumerate its inputs says so in `params.detail` and reports none;
    the inputs can still be written by id. Anything on the track that is not a Text+ title
    is listed too, with `unreadable` saying why — a stray clip on the Titles track is
    worth knowing about, since the next apply_titles will delete it.
    """
    return title_edit.list_titles(get_connection(), timeline)


@tool
def edit_title(
    title: str | None = None,
    text: str | None = None,
    params: dict[str, Any] | None = None,
    at: Any = None,
    timeline: str | None = None,
) -> dict[str, Any]:
    """Fix one already-placed title in place — its words, its exposed params, or both.

    For the typo you spot in the review, this is one call and it costs nothing else: no
    rebuild, no re-apply, no clear. The title keeps its Fusion comp, its fade and its
    position. Every other readable title on the track has its text and the inputs this
    call writes read before and after, and `other_titles_unchanged` is how many were
    confirmed — if one moved, the instances share a Fusion comp, the edit is put back and
    the call fails rather than reporting a success that re-worded someone else's title.

    Name the title by `title`, the exact words it says now (list_titles reports them), or
    by `at`, its record frame — or both when two titles read the same. Nothing matching,
    or more than one, is refused with everything on the track listed rather than guessed
    at. `text` sets the words; `params` sets exposed inputs by Fusion id,
    e.g. {"Size": 0.08}. Any id the node has works, not just the ones list_titles shows —
    a write that does not read back fails and names every id it would have taken.

    This edits the timeline, not titles.json — so the next apply_titles puts the old
    wording back. Fix the file too when the change is one you want to keep.
    """
    return title_edit.edit_title(
        get_connection(),
        title,
        text=text,
        params=params,
        at=at,
        timeline=timeline,
    )


TOOLS: tuple[Any, ...] = (
    get_titles_schema,
    validate_titles,
    apply_titles,
    list_titles,
    edit_title,
)

__all__ = [
    "TOOLS",
    "apply_titles",
    "edit_title",
    "get_titles_schema",
    "list_titles",
    "validate_titles",
]
