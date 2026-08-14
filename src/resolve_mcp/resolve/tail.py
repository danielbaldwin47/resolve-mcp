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
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Final

from ..cut.tail import Tail
from ..errors import (
    BuildFailedError,
    TimelineExportFailedError,
    TimelineImportFailedError,
    TimelineNotFoundError,
)
from ..logging_config import get_logger
from .connection import ResolveConnection
from .interchange import export_timeline, import_timeline
from .timeline import find_timeline, next_free_name

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


def staging_name(name: str, existing: Iterable[str] = ()) -> str:
    """The name the shots are appended to before the round trip renames them into ``name``.

    A suffix rather than a prefix, and outside the ``<base> v<N>`` pattern either way, so a
    staging timeline left behind by a failed build can never be read as a version by the
    scan that picks the next one.

    That last property has a cost this dodges: a staging timeline a failed build left behind
    is invisible to the version scan, so the retry picks the same version number and asks
    for a staging name the project already holds — and Resolve refuses to create it. The
    collision therefore walks the project's own ``<base> v<N>`` sequence, exactly as an
    import collision does, and the suffix rides along so the name still reads as staging.
    """
    return next_free_name(f"{name}{STAGING_SUFFIX}", set(existing))


# --- the document edit ----------------------------------------------------------------------


def inject(document: Document, tail: Tail) -> dict[str, Any]:
    """Edit ``tail`` into an exported OTIO document. Returns what was put where.

    The dissolve goes on every video track that reaches the end of the cut, which on an
    ordinary concert build is V1 alone: an overlay that stopped earlier has nothing to do
    with how the picture leaves, and fading it would be a second device nobody asked for.
    The audio fade goes on every audio track's last clip, because the master mix is the only
    thing this pillar puts there.

    Also returned is what did *not* get one where one was needed — ``unfaded_video`` and
    ``unfaded_audio``. A per-track refusal is invisible in the totals: a document where V1
    faded and an opaque V2 did not still reports a dissolve, and the picture then pops back
    out of black the frame the overlay ends. The caller refuses on either list rather than
    delivering half a device.
    """
    video: list[str] = []
    audio: list[str] = []
    unfaded_video: list[str] = []
    unfaded_audio: list[str] = []
    rate = _timeline_rate(document)
    if tail.dissolves:
        ending = _tracks(document, "Video", last_only=True, rate=rate)
        for track in ending:
            placed = video if _append_transition(track, rate, tail.frames) else unfaded_video
            placed.append(_name(track))
        unfaded_video.extend(_name(track) for track in _inside(document, tail, ending, rate))
    if tail.fades_audio:
        for track in _tracks(document, "Audio", last_only=False, rate=rate):
            placed = audio if _append_transition(track, rate, tail.audio_frames) else unfaded_audio
            placed.append(_name(track))
    return {
        "video_tracks": video,
        "audio_tracks": audio,
        "unfaded_video": unfaded_video,
        "unfaded_audio": unfaded_audio,
    }


def _name(track: Track) -> str:
    """What a track is called on *both* sides of the round trip — one vocabulary, not two.

    An OTIO track need not carry a name, and :func:`inject` and :func:`transitions` are
    compared to each other, by name, in ``_confirm``. A fallback invented separately on each
    side is therefore not a cosmetic difference: an unnamed track would have its dissolve
    recorded under one word and read back under another, so every such build would refuse a
    tail that landed perfectly — and take a correct import down with it on the way out.
    """
    return str(track.get("name") or "") or str(track.get("kind") or "").lower()


def _inside(document: Document, tail: Tail, ending: list[Track], rate: float) -> list[Track]:
    """Video tracks whose picture stops *inside* the dissolve, which nothing here fades.

    Such a track is opaque over part of the ramp and then ends, so the picture underneath
    comes back partway through a fade to black — a visible second ending. It is not a track
    to fade either: the dissolve reaches back into the shot that ends the cut, and one
    starting where an overlay happens to stop would be a device nobody asked for. So it is
    reported, and the build refuses rather than delivering a tail with a hole in it.
    """
    others = [
        track
        for track in _tracks(document, "Video", last_only=False, rate=rate)
        if not any(track is one for one in ending)
    ]
    furthest = max((_span(track, rate) for track in ending), default=0)
    return [track for track in others if furthest - tail.frames < _span(track, rate) < furthest]


def _tracks(document: Document, kind: str, last_only: bool, rate: float) -> list[Track]:
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
    furthest = max(_span(track, rate) for track in found)
    return [track for track in found if _span(track, rate) == furthest]


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


def _span(track: Track, rate: float) -> int:
    """Where the *picture* on this track stops — trailing black is not part of the answer.

    Measured to the end of the last clip rather than to the end of the track, for the same
    reason: Resolve pads every track out to the timeline's length, so track length is the
    one number that cannot tell the layer the cut ends on from a layer that stopped early.

    In *timeline* frames, which is what makes the answer comparable across tracks at all: a
    mixed-rate multicam stack carries a different media rate per clip, and summing the raw
    numbers would measure two layers in two units and call the shorter one the end.

    Summed unrounded and rounded once, because this is a sum and the comparison it feeds is
    an equality. Rounding per clip spends up to half a frame each time, so a forty-shot V1
    can come out two frames short of an overlay that ends on the very same frame — which
    drops V1 out of the ending layers, straight into the window ``_inside`` refuses, and the
    build then fails a correct mixed-rate cut with the shots already on a staging timeline.
    """
    children = list(track.get("children") or [])
    last = _last_clip(track)
    return round(
        sum(
            _exact(_duration(item), rate)
            for item in children[: last + 1]
            if not _is_transition(item)
        )
    )


def _append_transition(track: Track, rate: float, frames: int) -> bool:
    """Put a fade at the end of the track's picture, or answer False and leave it alone.

    The transition goes immediately after the last clip, which is a clip→gap boundary
    whenever something outlives the picture — reaching ``frames`` back into the shot and
    nothing forward, so it lands on black on the shot's own last frame.

    Refused rather than trimmed when that clip is too short: the length was validated (E12)
    against the cut file, so a document that cannot carry it means the built timeline
    disagrees with the cut, and quietly shortening the device would hide that.

    Both numbers are timeline frames. ``frames`` comes from the cut file, which counts in
    the timeline's rate; the clip's own duration is stamped in its *media* rate, so on a
    mixed-rate multicam the raw comparison asks whether a 23.976 count fits inside a 25
    one — and answers "the shot is too short to fade" about a shot that is not.
    """
    children = list(track.get("children") or [])
    index = _last_clip(track)
    if index < 0 or _frames(children[index], rate) <= frames:
        return False
    children.insert(index + 1, _transition(rate or _rate(children[index]), frames))
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
    """Every transition in a document, as ``{track, kind, in_offset}`` — the read-back.

    ``track`` is named by the same helper :func:`inject` records with, and ``in_offset`` is
    in timeline frames, because both are compared against what ``inject`` asked for.
    """
    rate = _timeline_rate(document)
    found = []
    for track in (document.get("tracks") or {}).get("children") or []:
        for item in track.get("children") or []:
            if _is_transition(item):
                found.append(
                    {
                        "track": _name(track),
                        "kind": str(track.get("kind") or ""),
                        "name": str(item.get("name") or ""),
                        "in_offset": _at_rate(item.get("in_offset"), rate),
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


def _frames(item: Item, rate: float) -> int:
    """One item's duration in whole timeline frames."""
    return _at_rate(_duration(item), rate)


def _rate(item: Item) -> float:
    try:
        return float(_duration(item).get("rate") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _timeline_rate(document: Document) -> float:
    """The rate the *timeline* counts in, or 0.0 when the document does not say.

    Every frame count a tail carries — ``duration_frames``, ``audio_fade_frames`` — is in
    the timeline's rate, because that is the rate the cut file counts in and the rate E12
    validated them against. OTIO stamps each clip's ``source_range`` in its own *media*
    rate instead, so on a mixed-rate multicam (an FX6 at 23.976 beside an A7IV at another
    rate, the ordinary concert kit) the two units are not the same number of frames.

    ``global_start_time`` is the OTIO timeline's own ``RationalTime``, and its rate is the
    timeline's — the one number in the document that is not somebody's media. **Not yet
    confirmed against a live mixed-rate export**; every rate in the documents seen so far is
    the same rate, which is exactly why this cannot be told apart by looking at them.

    Zero — a document that carries no start time — means "do not convert" rather than a
    guess: the arithmetic then runs on the items' own numbers, as it did before it could ask.
    """
    time = document.get("global_start_time")
    if not isinstance(time, dict):
        return 0.0
    try:
        rate = float(time.get("rate") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return rate if rate > 0 else 0.0


def _at_rate(time: Any, rate: float) -> int:
    """A ``RationalTime`` as a whole number of frames at ``rate``, its own rate if that is 0."""
    return round(_exact(time, rate))


def _exact(time: Any, rate: float) -> float:
    """The same reading, unrounded — what a sum has to be built out of. See :func:`_span`.

    A rate this cannot read is not a reason to measure the item as nothing. Only ``value``
    decides how long something is; the rate decides what unit that is in, and an unreadable
    one means the value is taken as it comes — which is what this module did before it
    converted between rates at all. Reading the two in one ``try`` would instead turn a
    stray rate into a zero-length clip, a track measuring zero, and every fade on it
    refused.
    """
    if not isinstance(time, dict):
        return 0.0
    try:
        value = float(time.get("value") or 0)
    except (TypeError, ValueError):
        return 0.0
    if rate <= 0:
        return value
    try:
        own = float(time.get("rate") or 0.0)
    except (TypeError, ValueError):
        return value
    return value * rate / own if own > 0 and own != rate else value


# --- the round trip -------------------------------------------------------------------------


def materialise(
    connection: ResolveConnection,
    project: Project,
    pool: Pool,
    staging: Timeline,
    staging_timeline: str,
    name: str,
    tail: Tail,
    verify: Callable[[Timeline], None] | None = None,
) -> tuple[Timeline, dict[str, Any]]:
    """Round-trip ``staging_timeline`` into ``name`` with the tail edited in.

    Returns the timeline the rest of the build works on — the imported one — and what
    landed. Every failure here is a :class:`BuildFailedError` naming the staging timeline,
    because the shots are on it and a caller told only "the export failed" has no way to
    know its cut still exists — and, once the import has landed, naming the imported one
    too unless Resolve let it be deleted.

    ``verify`` is the caller's own read-back of where the shots are, run here rather than
    by the caller so it happens on the *imported* timeline while the staging one is still
    the fallback: the cut the build checked and the cut it delivers are two different
    timelines, and the one nobody checked is the one that ships.
    """
    # No path: the export lands, timestamped, in the interchange directory every other
    # export goes to. Two reasons it is not a temp file. Resolve holds what it exported open
    # for the life of the process (#26), so a name is spent once used — and a timestamped
    # one never collides. And this pair of documents, the export and the edit beside it, is
    # the evidence for what a tail did, so it belongs where a human already looks for
    # exports rather than in a temp directory nobody would think to open.
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
    if placed["unfaded_video"]:
        raise _lost(
            staging_timeline,
            name,
            f"{_listed(placed['unfaded_video'])} would stay opaque over part of the "
            f"{tail.frames}-frame dissolve, so the picture would come back out of black "
            f"before the cut ends",
        )
    if placed["unfaded_audio"]:
        raise _lost(
            staging_timeline,
            name,
            f"{_listed(placed['unfaded_audio'])} took no fade while "
            f"{_listed(placed['audio_tracks'])} did, so the mix would end on a cut",
        )
    # A fresh path, never the one Resolve just exported to: Resolve holds a file it wrote
    # open for the life of the process (#26), so rewriting the export in place can fail on
    # Windows for a reason no retry here can clear. The untouched export stays beside the
    # edited document as the before to its after.
    edited = _beside(Path(exported["path"]))
    try:
        edited.write_text(json.dumps(document, indent=1), encoding="utf-8")
    except OSError as exc:
        raise _lost(
            staging_timeline, name, f"the edited document could not be written to {edited}: {exc}"
        ) from exc

    try:
        imported = import_timeline(connection, path=str(edited), name=name)
    except (TimelineExportFailedError, TimelineImportFailedError) as exc:
        raise _lost(
            staging_timeline, name, f"the edited document would not import: {exc.cause}"
        ) from exc

    landed = str(imported["timeline"]["name"])
    if landed != name:
        # Not necessarily Resolve renaming it: an import is always asked for a name no
        # timeline in the project answers to (``next_free_name``), so a project that already
        # holds ``name`` gets the dodge rather than a collision. Either way the cut under a
        # name nobody asked for is not the delivery, and it is not left standing.
        raise _failed_import(
            pool,
            project,
            staging,
            staging_timeline,
            name,
            landed,
            f"the import landed as {landed!r} rather than {name!r} — either the project "
            f"already held that name, so a free one was asked for, or Resolve renamed it",
        )

    try:
        built = find_timeline(project, landed)
    except TimelineNotFoundError as exc:
        raise _lost(
            staging_timeline,
            name,
            f"Resolve reported importing {landed!r} and the project holds no timeline of "
            "that name",
        ) from exc
    project.SetCurrentTimeline(built)
    # Both checks run before the staging timeline is deleted, because until the tail is
    # confirmed and the shots are re-read on the imported cut, the staging one is still the
    # only copy worth keeping.
    confirmed, refused = _confirm(connection, landed, placed, tail)
    if refused is not None:
        raise _failed_import(pool, project, staging, staging_timeline, name, landed, refused)
    if verify is not None:
        try:
            verify(built)
        except BuildFailedError as exc:
            raise _failed_import(
                pool,
                project,
                staging,
                staging_timeline,
                name,
                landed,
                f"the round trip moved the shots: {exc.cause}",
            ) from exc
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
        "video_tracks": placed["video_tracks"],
        "audio_tracks": placed["audio_tracks"],
        "route": "otio_round_trip",
        "document": str(edited),
        "confirmed": confirmed,
    }


def _listed(names: list[str]) -> str:
    return ", ".join(repr(one) for one in names) or "nothing"


def _beside(exported: Path) -> Path:
    """A path in the export's own directory that nothing has written to yet.

    The edited document lands next to the export it came from rather than in a temp
    directory: it *is* the evidence for what the tail did, so it belongs where a human
    already looks for exports.
    """
    candidate = exported.with_name(f"{exported.stem} (tail){exported.suffix}")
    number = 2
    while candidate.exists():
        candidate = exported.with_name(f"{exported.stem} (tail {number}){exported.suffix}")
        number += 1
    return candidate


def _confirm(
    connection: ResolveConnection,
    landed: str,
    placed: dict[str, Any],
    tail: Tail,
) -> tuple[list[dict[str, Any]], str | None]:
    """Read the imported cut back and check the tail is on it. Another export is the only way.

    Returns what is on the cut and why it is not the tail that was asked for, ``None`` when
    it is. The refusal is returned rather than raised because the caller has an imported
    timeline to deal with first — an error raised from here would leave it standing.

    There is no getter for a transition anywhere in the scripting API, so "did the dissolve
    land" can only be asked by exporting the timeline again and looking. That second export
    is the whole reason this check exists rather than trusting the import: a device that
    silently is not there is indistinguishable, everywhere downstream, from a cut that never
    asked for one — which is exactly how the ending piece lost a round 0-3.

    Counting is not enough, for the same reason. Resolve trims a dissolve to the handles the
    shot actually has, so a 40-frame fade can come back as a 12-frame one with the count
    still matching; and a transition that landed on another track is a device on the wrong
    layer. So every fade is checked against the track it was put on and the length the cut
    asks for, and anything else is refused.
    """
    try:
        again = export_timeline(connection, name=landed, export_format="otio")
        document = json.loads(Path(again["path"]).read_text(encoding="utf-8"))
    except (TimelineExportFailedError, TimelineImportFailedError, OSError, ValueError) as exc:
        log.warning("The imported cut %s could not be read back", landed, exc_info=True)
        return [], f"the imported cut could not be read back to check it: {exc}"

    found = transitions(document)
    for kind, asked, frames in (
        ("Video", placed["video_tracks"], tail.frames),
        ("Audio", placed["audio_tracks"], tail.audio_frames),
    ):
        got = [one for one in found if one["kind"] == kind]
        if len(got) < len(asked):
            return found, (
                f"Resolve took the import and kept {len(got)} of the {len(asked)} "
                f"{kind.lower()} fade(s) the document carried"
            )
        for track in asked:
            on_track = [one for one in got if one["track"] == track]
            if not on_track:
                return found, (
                    f"the {kind.lower()} fade the document put on {track!r} is not on that "
                    f"track in the imported cut"
                )
            if all(one["in_offset"] != frames for one in on_track):
                return found, (
                    f"the {kind.lower()} fade on {track!r} came back "
                    f"{on_track[0]['in_offset']} frames long rather than the {frames} the "
                    f"cut asks for — Resolve trims a fade to the handles the shot has"
                )
    return found, None


def _failed_import(
    pool: Pool,
    project: Project,
    staging: Timeline,
    staging_timeline: str,
    name: str,
    landed: str,
    why: str,
) -> BuildFailedError:
    """The import landed and cannot be kept: delete it if Resolve will, and say what is left.

    Deleting it matters more than tidiness. The staging timeline is what the caller is sent
    back to, and the advice for it is to rename it into ``name`` by hand — which collides
    with a failed import sitting there under that very name. When Resolve refuses the
    delete, the error names both timelines instead, because then renaming is not the advice.
    """
    kept = None if _discard_import(pool, project, staging, landed) else landed
    return _lost(staging_timeline, name, why, orphan=kept)


def _discard_import(pool: Pool, project: Project, staging: Timeline, landed: str) -> bool:
    """Best effort: the failure being reported must not be replaced by one from cleaning up.

    The current timeline is moved back to the staging cut first. Resolve will not delete the
    timeline it is sitting on and says so only with a ``False``, and the import being
    discarded is the one this build just switched to.
    """
    try:
        project.SetCurrentTimeline(staging)
        deleted = bool(pool.DeleteTimelines([find_timeline(project, landed)]))
    except Exception:  # noqa: BLE001 - a refusal here only changes what the error says
        log.warning("Deleting the failed import %s raised", landed, exc_info=True)
        return False
    if deleted:
        log.info("Deleted the failed import %s", landed)
    else:
        log.warning("Resolve would not delete the failed import %s", landed)
    return deleted


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


def _lost(
    staging_timeline: str,
    name: str,
    why: str,
    orphan: str | None = None,
) -> BuildFailedError:
    """The build's refusal, naming every timeline the failure left in the project.

    ``orphan`` is the import that landed and could not be deleted. It changes the advice
    rather than decorating it: with a timeline already carrying ``name``, "rename the
    staging one" is advice that collides, so both are named and the human picks.
    """
    if orphan is not None:
        return BuildFailedError(
            cause=(
                f"The cut was built but its tail could not be placed, because {why}. Two "
                f"timelines are left: {staging_timeline!r}, which holds the whole cut with "
                f"a hard cut where its tail should be, and {orphan!r}, the import that "
                f"could not be used and that Resolve would not delete. Nothing was "
                f"delivered as {name!r}."
            ),
            fix=(
                f"Delete {orphan!r} by hand, then either rename {staging_timeline!r} into "
                f"{name!r} if a hard out will do, or delete it and build again."
            ),
            detail={
                "timeline": name,
                "staging_timeline": staging_timeline,
                "imported_timeline": orphan,
            },
        )
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
