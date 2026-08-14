"""Materialising a cut's tail: the dissolve to black and the fade under it.

The scripting API cannot cut a transition. That is not a gap in this server's knowledge of
it — it was probed live on Resolve 21.0.3 and there is nothing: ``Timeline`` has no
transition call at all, ``TimelineItem`` exposes ``SetProperty`` for a *static* ``Opacity``
and nothing whatsoever for audio level. So the tail is built the only way it can be, on the
route :mod:`resolve_mcp.resolve.interchange` exists for — export the built cut as OTIO,
edit the transitions into the document, import it back.

Three things are decisions rather than mechanics:

* **The document is edited, and then the cut is read back to see whether the edit took.**
  An OTIO transition is not an item with a duration; it sits *between* two children of a
  track and reaches ``in_offset`` frames back into the one before. There is no getter for
  one anywhere in the API, so the only way to ask whether a dissolve landed is to export
  the imported timeline *again* and look — which this does, before the staging timeline is
  deleted. Resolve renames what it accepts (``Fade to Black`` comes back as
  ``Cross Dissolve`` on video and ``Cross Fade 0 dB`` on audio), so the check counts
  transitions and never trusts a name.
* **The fade goes after the last shot, not after the last child.** Resolve pads every
  exported track with a trailing ``Gap`` out to the length of the *timeline*, so a concert
  cut — whose mix is authored to outlive the picture, as all five surveyed deliverables do
  — exports a V1 that ends in black (verified live, 21.0.3: a 480-frame picture under a
  520-frame mix exported V1 as three clips and a 40-frame gap). A transition appended to
  the end of that track would dissolve black into black. Nothing is appended *after* the
  picture either: black at the end of a cut is the cut file's own gap, subject to W8 like
  any other, and adding one here would be this module inventing record time.
* **The staging timeline is deleted only once the import has landed.** Until then it is the
  only copy of the cut, and a build that loses the round trip has to leave the shots
  somewhere a human can find them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from ..cut.tail import Tail
from ..errors import (
    BuildFailedError,
    TimelineExportFailedError,
    TimelineImportFailedError,
)
from ..logging_config import get_logger
from .connection import ResolveConnection
from .interchange import export_timeline, import_timeline
from .timeline import find_timeline

log = get_logger("build")

Document = dict[str, Any]
Track = dict[str, Any]
Item = dict[str, Any]
Pool = Any
Project = Any
Timeline = Any

DISSOLVE: Final = "SMPTE_Dissolve"
"""The only transition type in play: a fade to black is a dissolve into nothing."""

STAGING_SUFFIX: Final = " (tail staging)"
"""What the pre-transition timeline is called, so a lost round trip leaves a named cut."""


def staging_name(name: str) -> str:
    """The name the shots are appended to before the round trip renames them into ``name``.

    A suffix rather than a prefix, and outside the ``<base> v<N>`` pattern either way, so a
    staging timeline left behind by a failed build can never be read as a version by the
    scan that picks the next one.
    """
    return f"{name}{STAGING_SUFFIX}"


# --- the document edit ----------------------------------------------------------------------


def inject(document: Document, tail: Tail) -> dict[str, Any]:
    """Edit ``tail`` into an exported OTIO document. Returns what was put where.

    The dissolve goes on every video track that reaches the end of the cut, which on an
    ordinary concert build is V1 alone: an overlay that stopped earlier has nothing to do
    with how the picture leaves, and fading it would be a second device nobody asked for.
    The audio fade goes on every audio track's last clip, because the master mix is the only
    thing this pillar puts there.
    """
    video: list[str] = []
    audio: list[str] = []
    if tail.dissolves:
        for track in _tracks(document, "Video", last_only=True):
            if _append_transition(track, tail.frames):
                video.append(str(track.get("name") or "video"))
    if tail.fades_audio:
        for track in _tracks(document, "Audio", last_only=False):
            if _append_transition(track, tail.audio_frames):
                audio.append(str(track.get("name") or "audio"))
    return {"video_tracks": video, "audio_tracks": audio}


def _tracks(document: Document, kind: str, last_only: bool) -> list[Track]:
    """The tracks of one kind a transition belongs on, in document order.

    ``last_only`` keeps the video dissolve to the layer the cut actually ends on. Ties are
    kept, not broken: two video tracks both running to the last frame are both the end of
    the picture, and fading one of them would leave the other opaque over black.
    """
    found = [
        track
        for track in ((document.get("tracks") or {}).get("children") or [])
        if track.get("kind") == kind and _clips(track)
    ]
    if not last_only or not found:
        return found
    furthest = max(_span(track) for track in found)
    return [track for track in found if _span(track) == furthest]


def _clips(track: Track) -> list[Item]:
    return [item for item in (track.get("children") or []) if _is_clip(item)]


def _last_clip(track: Track) -> int:
    """Where the last clip on this track sits among its children, or -1 if there is none.

    Not simply the last child. Resolve pads every track it exports with a trailing ``Gap``
    out to the length of the *timeline*, so a concert cut — whose mix is authored to outlive
    the picture, as all five surveyed deliverables do — exports a V1 that ends in black
    rather than in a shot (verified live, 21.0.3). A fade appended after that gap would
    dissolve black into black.
    """
    children = list(track.get("children") or [])
    for index in range(len(children) - 1, -1, -1):
        if _is_clip(children[index]):
            return index
    return -1


def _span(track: Track) -> int:
    """Where the *picture* on this track stops — trailing black is not part of the answer.

    Measured to the end of the last clip rather than to the end of the track, for the same
    reason: Resolve pads every track out to the timeline's length, so track length is the
    one number that cannot tell the layer the cut ends on from a layer that stopped early.
    """
    children = list(track.get("children") or [])
    last = _last_clip(track)
    return sum(
        _frames(item) for item in children[: last + 1] if not _is_transition(item)
    )


def _append_transition(track: Track, frames: int) -> bool:
    """Put a fade at the end of the track's picture, or answer False and leave it alone.

    The transition goes immediately after the last clip, which is a clip→gap boundary
    whenever something outlives the picture — reaching ``frames`` back into the shot and
    nothing forward, so it lands on black on the shot's own last frame.

    Refused rather than trimmed when that clip is too short: the length was validated (E12)
    against the cut file, so a document that cannot carry it means the built timeline
    disagrees with the cut, and quietly shortening the device would hide that.
    """
    children = list(track.get("children") or [])
    index = _last_clip(track)
    if index < 0 or _frames(children[index]) <= frames:
        return False
    children.insert(index + 1, _transition(_rate(children[index]), frames))
    track["children"] = children
    return True


def _transition(rate: float, frames: int) -> Item:
    """A dissolve reaching ``frames`` back into what precedes it and nothing forward.

    ``out_offset`` of zero is what makes it a fade *out* rather than a cross: there is
    nothing after it to reach into, and black is the absence of the frames it gives up.
    """
    return {
        "OTIO_SCHEMA": "Transition.1",
        "name": "Fade to Black",
        "metadata": {},
        "transition_type": DISSOLVE,
        "in_offset": _rational(rate, frames),
        "out_offset": _rational(rate, 0),
    }


def transitions(document: Document) -> list[dict[str, Any]]:
    """Every transition in a document, as ``{track, kind, in_offset}`` — the read-back."""
    found = []
    for track in (document.get("tracks") or {}).get("children") or []:
        for item in track.get("children") or []:
            if _is_transition(item):
                found.append(
                    {
                        "track": str(track.get("name") or ""),
                        "kind": str(track.get("kind") or ""),
                        "name": str(item.get("name") or ""),
                        "in_offset": int(float((item.get("in_offset") or {}).get("value") or 0)),
                    }
                )
    return found


def _rational(rate: float, value: int) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": rate, "value": value}


def _is_clip(item: Item) -> bool:
    return str(item.get("OTIO_SCHEMA", "")).startswith("Clip.")


def _is_transition(item: Item) -> bool:
    return str(item.get("OTIO_SCHEMA", "")).startswith("Transition.")


def _duration(item: Item) -> dict[str, Any]:
    duration: dict[str, Any] = ((item.get("source_range") or {}).get("duration")) or {}
    return duration


def _frames(item: Item) -> int:
    try:
        return int(float(_duration(item).get("value") or 0))
    except (TypeError, ValueError):
        return 0


def _rate(item: Item) -> float:
    try:
        return float(_duration(item).get("rate") or 0.0)
    except (TypeError, ValueError):
        return 0.0


# --- the round trip -------------------------------------------------------------------------


def materialise(
    connection: ResolveConnection,
    project: Project,
    pool: Pool,
    staging: Timeline,
    staging_timeline: str,
    name: str,
    tail: Tail,
) -> tuple[Timeline, dict[str, Any]]:
    """Round-trip ``staging_timeline`` into ``name`` with the tail edited in.

    Returns the timeline the rest of the build works on — the imported one — and what
    landed. Every failure here is a :class:`BuildFailedError` naming the staging timeline,
    because the shots are on it and a caller told only "the export failed" has no way to
    know its cut still exists.
    """
    # No path: the document lands, timestamped, in the interchange directory every other
    # export goes to. Two reasons it is not a temp file. Resolve holds what it exported open
    # for the life of the process (#26), so a name is spent once used — and a timestamped
    # one never collides. And the edited document *is* the evidence for what a tail did, so
    # it belongs where a human already looks for exports rather than in a temp directory
    # nobody would think to open.
    try:
        exported = export_timeline(connection, name=staging_timeline, export_format="otio")
    except (TimelineExportFailedError, TimelineImportFailedError) as exc:
        raise _lost(
            staging_timeline, name, f"it could not be exported as OTIO: {exc.cause}"
        ) from exc

    document = _load(Path(exported["path"]), staging_timeline, name)
    placed = inject(document, tail)
    if tail.dissolves and not placed["video_tracks"]:
        raise _lost(
            staging_timeline,
            name,
            f"no video track in the exported document ends on a shot longer than the "
            f"{tail.frames}-frame dissolve",
        )
    if tail.fades_audio and not placed["audio_tracks"]:
        raise _lost(
            staging_timeline,
            name,
            f"no audio track in the exported document ends on a clip longer than the "
            f"{tail.audio_frames}-frame fade",
        )
    Path(exported["path"]).write_text(json.dumps(document, indent=1), encoding="utf-8")

    try:
        imported = import_timeline(connection, path=exported["path"], name=name)
    except (TimelineExportFailedError, TimelineImportFailedError) as exc:
        raise _lost(
            staging_timeline, name, f"the edited document would not import: {exc.cause}"
        ) from exc

    landed = str(imported["timeline"]["name"])
    if landed != name:
        raise _lost(
            staging_timeline,
            name,
            f"Resolve named the imported timeline {landed!r} instead",
        )

    built = find_timeline(project, landed)
    project.SetCurrentTimeline(built)
    # Before the staging timeline is deleted, because until the tail is confirmed the
    # staging one is still the only copy of the cut worth keeping.
    confirmed = _confirm(connection, landed, placed, staging_timeline, name)
    _delete_staging(pool, project, staging, staging_timeline)
    log.info(
        "Tail on %s: %s dissolve over %s, audio fade over %s",
        landed,
        tail.kind,
        ", ".join(placed["video_tracks"]) or "nothing",
        ", ".join(placed["audio_tracks"]) or "nothing",
    )
    return built, {
        **tail.as_dict(),
        **placed,
        "route": "otio_round_trip",
        "document": exported["path"],
        "confirmed": confirmed,
    }


def _confirm(
    connection: ResolveConnection,
    landed: str,
    placed: dict[str, Any],
    staging_timeline: str,
    name: str,
) -> list[dict[str, Any]]:
    """Read the imported cut back and check the tail is on it. Another export is the only way.

    There is no getter for a transition anywhere in the scripting API, so "did the dissolve
    land" can only be asked by exporting the timeline again and looking. That second export
    is the whole reason this check exists rather than trusting the import: a device that
    silently is not there is indistinguishable, everywhere downstream, from a cut that never
    asked for one — which is exactly how the ending piece lost a round 0-3.
    """
    try:
        again = export_timeline(connection, name=landed, export_format="otio")
        document = json.loads(Path(again["path"]).read_text(encoding="utf-8"))
    except (TimelineExportFailedError, TimelineImportFailedError, OSError, ValueError) as exc:
        raise _lost(
            staging_timeline, name, f"the imported cut could not be read back to check it: {exc}"
        ) from exc

    found = transitions(document)
    for kind, asked in (("Video", placed["video_tracks"]), ("Audio", placed["audio_tracks"])):
        got = [one for one in found if one["kind"] == kind]
        if len(got) < len(asked):
            raise _lost(
                staging_timeline,
                name,
                f"Resolve took the import and kept {len(got)} of the {len(asked)} "
                f"{kind.lower()} fade(s) the document carried",
            )
    return found


def _load(path: Path, staging_timeline: str, name: str) -> Document:
    try:
        document: Document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _lost(
            staging_timeline, name, f"its OTIO export could not be read back: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise _lost(staging_timeline, name, "its OTIO export is not a document")
    return document


def _delete_staging(pool: Pool, project: Project, staging: Timeline, staging_timeline: str) -> None:
    """Best effort, and after the import: a staging timeline left behind is a tidiness bug.

    A build that has its cut under the right name has succeeded; failing it over a timeline
    Resolve would not delete would throw away a good build to report housekeeping.
    """
    try:
        deleted = bool(pool.DeleteTimelines([staging]))
    except Exception:  # noqa: BLE001 - a refusal here must not fail a landed build
        log.warning("Deleting the staging timeline %s raised", staging_timeline, exc_info=True)
        return
    if deleted:
        log.info("Deleted the staging timeline %s", staging_timeline)
    else:
        log.warning(
            "Resolve would not delete the staging timeline %s; it is still in %s",
            staging_timeline,
            project.GetName(),
        )


def _lost(staging_timeline: str, name: str, why: str) -> BuildFailedError:
    return BuildFailedError(
        cause=(
            f"The cut was built but its tail could not be placed, because {why}. Nothing "
            f"was delivered as {name!r}."
        ),
        fix=(
            f"The shots are on {staging_timeline!r}, which holds the whole cut with a hard "
            f"cut where its tail should be. Rename it by hand if that will do, or delete it "
            f"and build again."
        ),
        detail={"timeline": name, "staging_timeline": staging_timeline},
    )


__all__ = ["inject", "materialise", "staging_name", "transitions"]
