"""The titling tools: the contract, the dry run, and the apply.

Titles are declarative in the same way the cut is — you author `titles.json`, the server
places it — but they never enter the cut file. The two files own different tracks, so a
cut can be rebuilt and the same titles re-applied to the new version unchanged.
"""

from __future__ import annotations

from typing import Any

from ..resolve import titles
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
    the file needs no timeline frames in it. Text comes from the file verbatim, and fades
    are written as Fusion opacity keyframes inside each placed instance, because Resolve
    exposes no clip-level fade to the API at all.

    The validate_titles rules run first: a single error aborts before the track is
    cleared, so a refused apply leaves the previous titles in place. The target timeline
    is opened in Resolve as part of the apply, since an append lands on whatever timeline
    is current. The report says per title whether its fade read back — a fade that did
    not is the one thing here worth checking by eye in the GUI.
    """
    return titles.apply_titles(get_connection(), titles_file)


TOOLS: tuple[Any, ...] = (
    get_titles_schema,
    validate_titles,
    apply_titles,
)

__all__ = ["TOOLS", "apply_titles", "get_titles_schema", "validate_titles"]
