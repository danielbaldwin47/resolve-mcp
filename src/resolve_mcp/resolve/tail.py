"""The tail's round trip: export the built cut, edit it, import it back, keep the fallback.

The scripting API cannot cut a transition. That is not a gap in this server's knowledge of
it — it was probed live on Resolve 21.0.3 and there is nothing: ``Timeline`` has no
transition call at all, ``TimelineItem`` exposes ``SetProperty`` for a *static* ``Opacity``
and nothing whatsoever for audio level. So the tail is built the only way it can be, on the
route :mod:`resolve_mcp.resolve.interchange` exists for — export the built cut as OTIO,
edit the transitions into the document, import it back. The edit itself is document surgery
with no Resolve in it and lives in :mod:`resolve_mcp.cut.otio`; this module owns everything
that touches the project.

Two things are decisions rather than mechanics:

* **The document is edited, and then the cut is read back to see whether the edit took.**
  An OTIO transition is not an item with a duration; it sits *between* two children of a
  track and reaches ``in_offset`` frames back into the one before. There is no getter for
  one anywhere in the API, so the only way to ask whether a dissolve landed is to export
  the imported timeline *again* and look — which this does, before the staging timeline is
  deleted. Resolve renames what it accepts (``Fade to Black`` comes back as
  ``Cross Dissolve`` on video and ``Cross Fade 0 dB`` on audio), so the check counts
  transitions and never trusts a name.
* **The staging timeline is deleted only once the import has landed *and* the caller is
  satisfied with it.** Until then it is the only copy of the cut, and a build that loses
  the round trip has to leave the shots somewhere a human can find them. That is why
  :func:`materialise` hands back a :class:`Landed` rather than a bare timeline: it is the
  imported cut *plus* the two things that can still be done about it — released, once the
  caller has checked it, or refused, which takes the import down and leaves staging
  standing. The caller cannot get the cut without also holding the choice.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ..cut.otio import Document, inject, transitions
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

Pool = Any
Project = Any
Timeline = Any

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


@dataclass(frozen=True)
class Staging:
    """Where the shots are until the round trip lands: one cut, and how to reach it again.

    Carried as one value rather than four parameters because they are one fact — the
    fallback — and every failure in this module says the same thing about it: the cut is
    still there, under this name, in this project.
    """

    project: Project
    pool: Pool
    timeline: Timeline
    name: str


@dataclass(frozen=True)
class Landed:
    """The imported cut, with the staging timeline still standing behind it.

    The invariant this type exists to carry is *check before delete*: the cut the round trip
    delivers is a different timeline from the one the build appended to and checked, so the
    one that ships is the one nobody has looked at yet. Until somebody does, staging is the
    only copy worth keeping.

    So this is not a timeline — it is a timeline plus the two things still to be done about
    it. :meth:`release` says the caller has read the import back and is keeping it, and
    drops staging. :meth:`refuse` says the caller found something wrong, and takes the
    import down instead. Doing neither leaves both timelines in the project, which is the
    safe end of the failure and not silently the wrong one.
    """

    timeline: Timeline
    applied: dict[str, Any]
    staging: Staging
    name: str

    def release(self) -> None:
        """Keep the import: drop the staging timeline. Best effort — see :func:`_delete_staging`.

        The line it logs is the one that says a tail *landed*, because this is the moment the
        cut stopped having a fallback. Everything before it is provisional.
        """
        log.info("Kept %s: its caller read the shots back on the cut the import made", self.name)
        _delete_staging(self.staging)

    def refuse(self, why: str) -> BuildFailedError:
        """Reject the import: take it down if Resolve will, and return the build's refusal.

        Returned rather than raised so the caller raises it from its own ``except`` and keeps
        the original failure as the cause.
        """
        return _failed_import(self.staging, self.name, self.name, why)


def materialise(
    connection: ResolveConnection,
    staging: Staging,
    name: str,
    tail: Tail,
) -> Landed:
    """Round-trip ``staging`` into ``name`` with the tail edited in.

    Returns the imported cut and what landed, as a :class:`Landed` the caller has to
    release or refuse — staging is untouched until it does. Every failure here is a
    :class:`BuildFailedError` naming the staging timeline, because the shots are on it and a
    caller told only "the export failed" has no way to know its cut still exists — and,
    once the import has landed, naming the imported one too unless Resolve let it be
    deleted.
    """
    # No path: the export lands, timestamped, in the interchange directory every other
    # export goes to. Two reasons it is not a temp file. Resolve holds what it exported open
    # for the life of the process (#26), so a name is spent once used — and a timestamped
    # one never collides. And this pair of documents, the export and the edit beside it, is
    # the evidence for what a tail did, so it belongs where a human already looks for
    # exports rather than in a temp directory nobody would think to open.
    try:
        exported = export_timeline(connection, name=staging.name, export_format="otio")
    except (TimelineExportFailedError, TimelineImportFailedError) as exc:
        raise _lost(staging, name, f"it could not be exported as OTIO: {exc.cause}") from exc

    document = _load(Path(exported["path"]), staging, name)
    placed = inject(document, tail)
    if tail.dissolves and not placed["video_tracks"]:
        raise _lost(
            staging,
            name,
            f"no video track in the exported document ends on a shot longer than the "
            f"{tail.frames}-frame dissolve",
        )
    if tail.fades_audio and not placed["audio_tracks"]:
        raise _lost(
            staging,
            name,
            f"no audio track in the exported document ends on a clip longer than the "
            f"{tail.audio_frames}-frame fade",
        )
    if placed["unfaded_video"]:
        raise _lost(
            staging,
            name,
            f"{_listed(placed['unfaded_video'])} would stay opaque over part of the "
            f"{tail.frames}-frame dissolve, so the picture would come back out of black "
            f"before the cut ends",
        )
    if placed["unfaded_audio"]:
        raise _lost(
            staging,
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
            staging, name, f"the edited document could not be written to {edited}: {exc}"
        ) from exc

    try:
        imported = import_timeline(connection, path=str(edited), name=name)
    except (TimelineExportFailedError, TimelineImportFailedError) as exc:
        raise _lost(staging, name, f"the edited document would not import: {exc.cause}") from exc

    landed = str(imported["timeline"]["name"])
    if landed != name:
        # Not necessarily Resolve renaming it: an import is always asked for a name no
        # timeline in the project answers to (``next_free_name``), so a project that already
        # holds ``name`` gets the dodge rather than a collision. Either way the cut under a
        # name nobody asked for is not the delivery, and it is not left standing.
        raise _failed_import(
            staging,
            name,
            landed,
            f"the import landed as {landed!r} rather than {name!r} — either the project "
            f"already held that name, so a free one was asked for, or Resolve renamed it",
        )

    try:
        built = find_timeline(staging.project, landed)
    except TimelineNotFoundError as exc:
        raise _lost(
            staging,
            name,
            f"Resolve reported importing {landed!r} and the project holds no timeline of "
            "that name",
        ) from exc
    staging.project.SetCurrentTimeline(built)
    # This module's own check, on its own device, before it hands anything back. The
    # caller's check of where the shots landed is the other half, and it runs on the value
    # returned here — with staging still standing, because nothing below deletes it.
    confirmed, refused = _confirm(connection, landed, placed, tail)
    if refused is not None:
        raise _failed_import(staging, name, landed, refused)
    # "Written", not "landed": the caller has still to read its shots back on this timeline,
    # and it may yet refuse it. A line claiming the tail here would leave a log where a
    # failed build reads as a successful one right up to the refusal two lines later — and a
    # live failure outside this session is diagnosed from the log or not at all. What the
    # build committed to is recorded by ``_delete_staging`` when the caller releases.
    log.info(
        "Tail written into %s, pending its caller's check: %s dissolve over %s, audio fade "
        "over %s",
        landed,
        tail.kind,
        ", ".join(placed["video_tracks"]) or "nothing",
        ", ".join(placed["audio_tracks"]) or "nothing",
    )
    return Landed(
        timeline=built,
        applied={
            **tail.as_dict(),
            "video_tracks": placed["video_tracks"],
            "audio_tracks": placed["audio_tracks"],
            "route": "otio_round_trip",
            "document": str(edited),
            "confirmed": confirmed,
        },
        staging=staging,
        name=name,
    )


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
    staging: Staging,
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
    kept = None if _discard_import(staging, landed) else landed
    return _lost(staging, name, why, orphan=kept)


def _discard_import(staging: Staging, landed: str) -> bool:
    """Best effort: the failure being reported must not be replaced by one from cleaning up.

    The current timeline is moved back to the staging cut first. Resolve will not delete the
    timeline it is sitting on and says so only with a ``False``, and the import being
    discarded is the one this build just switched to.
    """
    try:
        staging.project.SetCurrentTimeline(staging.timeline)
        deleted = bool(staging.pool.DeleteTimelines([find_timeline(staging.project, landed)]))
    except Exception:  # noqa: BLE001 - a refusal here only changes what the error says
        log.warning("Deleting the failed import %s raised", landed, exc_info=True)
        return False
    if deleted:
        log.info("Deleted the failed import %s", landed)
    else:
        log.warning("Resolve would not delete the failed import %s", landed)
    return deleted


def _load(path: Path, staging: Staging, name: str) -> Document:
    try:
        document: Document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise _lost(staging, name, f"its OTIO export could not be read back: {exc}") from exc
    if not isinstance(document, dict):
        raise _lost(staging, name, "its OTIO export is not a document")
    return document


def _delete_staging(staging: Staging) -> None:
    """Best effort, and after the import: a staging timeline left behind is a tidiness bug.

    A build that has its cut under the right name has succeeded; failing it over a timeline
    Resolve would not delete would throw away a good build to report housekeeping.
    """
    try:
        deleted = bool(staging.pool.DeleteTimelines([staging.timeline]))
    except Exception:  # noqa: BLE001 - a refusal here must not fail a landed build
        log.warning("Deleting the staging timeline %s raised", staging.name, exc_info=True)
        return
    if deleted:
        log.info("Deleted the staging timeline %s", staging.name)
    else:
        log.warning(
            "Resolve would not delete the staging timeline %s; it is still in %s",
            staging.name,
            staging.project.GetName(),
        )


def _lost(
    staging: Staging,
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
                f"timelines are left: {staging.name!r}, which holds the whole cut with "
                f"a hard cut where its tail should be, and {orphan!r}, the import that "
                f"could not be used and that Resolve would not delete. Nothing was "
                f"delivered as {name!r}."
            ),
            fix=(
                f"Delete {orphan!r} by hand, then either rename {staging.name!r} into "
                f"{name!r} if a hard out will do, or delete it and build again."
            ),
            detail={
                "timeline": name,
                "staging_timeline": staging.name,
                "imported_timeline": orphan,
            },
        )
    return BuildFailedError(
        cause=(
            f"The cut was built but its tail could not be placed, because {why}. Nothing "
            f"was delivered as {name!r}."
        ),
        fix=(
            f"The shots are on {staging.name!r}, which holds the whole cut with a hard "
            f"cut where its tail should be. Rename it by hand if that will do, or delete it "
            f"and build again."
        ),
        detail={"timeline": name, "staging_timeline": staging.name},
    )


__all__ = ["Landed", "Staging", "materialise", "staging_name"]
