"""The transcript a cut will read back as, derived before the cut is built.

The rough-cut pillar assembles A-roll by reading a word-level transcript and choosing
takes editorially. That leaves one question the agent cannot answer by re-reading its own
cut file: *what does the assembled cut actually say?* Segments are half-open source ranges;
the words they contain are somewhere else, in seconds, in another document. Answering it by
hand means holding two coordinate systems and a running total in your head for every
segment, which is exactly the arithmetic that goes wrong quietly on a forty-minute cut.

So this derives it. Given a cut file and the transcripts of the sources its segments come
from, it returns the words the built timeline would contain, in cut order, with the
timeline position each one lands at — plus the measurements a self-review needs: words the
cut opens or closes halfway through, a run of words that survives twice because two takes
of the same line were both kept, low-confidence words that made it into the delivery, and
seams between two shots of the same source that no overlay covers.

**Nothing here is a judgement.** Following ``transcript.py``: the document reports
confidence and gaps, and deciding that a word is a flub, that filler should go, or that a
repeat is a retake rather than a deliberate echo stays the agent's call. Every finding is a
warning — there is no cut this refuses. It is the P4 counterpart to ``correlate.py``, which
measures a concert cut against its music and likewise scores nothing.

It touches no Resolve handle: a cut file and some transcripts are both documents on disk,
so this is a plain reading, verified at the pure-function tier with no seam involved.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from ..config import Config, get_config
from ..cut.document import read_cut_file
from ..cut.layout import is_gap, overlay_positions, positions, total_frames
from ..document import LoadedDocument
from ..errors import InvalidRequestError
from ..findings import Finding, ordered
from ..spill import spill
from ..timing import IN_POINT, OUT_POINT, SECONDS_PRECISION, dual_time, frames_from_seconds
from . import records
from .transcript import DEFAULT_LOW_CONFIDENCE

INLINE_WORDS: Final = 200
"""Words returned inline before the whole reading goes to disk instead."""

REPEATED_RUN: Final = 2
"""How many words either side of a seam must match before a repeat is worth reporting.

One word repeating across a cut is ordinary English — "and", "the", a name. Two in the same
order is the shape of a line delivered twice with both takes kept.
"""

_FIX_HINTS: Final[dict[str, str]] = {
    "W3": "Move the boundary to the frame named above, or keep it if the clip is deliberate.",
    "W4": "Drop one of the two takes, or make the second an alternate of the first.",
    "W5": "Transcribe the source and pass it in, or accept that this segment reads as silence.",
    "W6": "Grab the frame and listen, then fix the wording or let the uncertainty stand.",
    "W7": "Cover the seam with an overlay anchored to the earlier segment, or accept the jump.",
}
"""The rules start at W3 because they share a document with ``cut/validate.py``.

That module's rule set already owns W1 and W2 *for the same cut file*, so a second W1
meaning something else is a trap: an agent holding a validate result and a reading of the
same file in one session would have two W2s that disagree. One document, one numbering.
"""

_CLIPPED_HINT: Final = "Move the boundary to frame {frame}, or keep it if the clip is deliberate."
"""W3 names the frame that would have spared the word, so it is built rather than looked up."""


def virtual_transcript(
    cut_file: str,
    transcripts: Mapping[str, str] | None = None,
    below: float = DEFAULT_LOW_CONFIDENCE,
    config: Config | None = None,
) -> dict[str, Any]:
    """Read a cut file back as the words it will contain. Never touches Resolve."""
    loaded = read_cut_file(cut_file)
    if loaded.parse_error is not None:
        raise InvalidRequestError(
            cause=f"{loaded.path.name} is not readable as a cut file.",
            fix="Call validate_cut for the parse error, fix it, then read the cut back.",
            detail={"cut_file": str(loaded.path), "rule": loaded.parse_error.rule},
        )
    doc = loaded.doc
    fps = _fps(doc, loaded.path.name)
    entries = _entries(doc, loaded.path.name)
    segments = _segments(entries)
    sources = dict(transcripts or {})

    words_by_source = {alias: _words_of(path) for alias, path in sources.items()}
    spans = {str(segment.get("id")): _span(segment, loaded.path.name) for segment in segments}
    placed = _placed(doc, loaded.path.name)
    findings: list[Finding] = []
    rows: list[dict[str, Any]] = []
    read_back: list[dict[str, Any]] = []

    for segment in segments:
        id = str(segment.get("id"))
        alias = str(segment.get("source"))
        span = spans[id]
        at, duration = placed[id]
        if alias not in words_by_source:
            findings.append(
                _finding("W5", id, f"segment {id} plays {alias!r}, which has no transcript here")
            )
            read_back.append(_read(id, alias, at, duration, fps, []))
            continue

        kept, clipped = _within(words_by_source[alias], span, fps)
        for word, edge, frame in clipped:
            findings.append(
                _finding(
                    "W3",
                    id,
                    f"segment {id} {edge} halfway through {word['word']!r}",
                    _CLIPPED_HINT.format(frame=frame),
                )
            )
        for word, frame in kept:
            confidence = float(word.get("confidence", 1.0))
            rows.append(
                {
                    "segment": id,
                    "source": alias,
                    "word": str(word["word"]),
                    "confidence": round(confidence, SECONDS_PRECISION),
                    "at": dual_time(at + frame - span[0], fps),
                }
            )
            if confidence < below:
                findings.append(
                    _finding(
                        "W6",
                        id,
                        f"{str(word['word'])!r} is delivered at confidence "
                        f"{round(confidence, SECONDS_PRECISION)}",
                    )
                )
        read_back.append(_read(id, alias, at, duration, fps, [word for word, _ in kept]))

    findings.extend(_repeats(read_back))
    seams = _seams(entries, placed, doc, fps, loaded.path.name)
    findings.extend(_uncovered(seams))
    total = total_frames(doc)

    return _result(loaded, doc, fps, total, read_back, rows, seams, ordered(findings), config)


def _result(
    loaded: LoadedDocument,
    doc: Any,
    fps: float,
    total: int,
    read_back: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    seams: list[dict[str, Any]],
    findings: list[Finding],
    config: Config | None,
) -> dict[str, Any]:
    counts = {
        "words": len(rows),
        "unsure": sum(1 for finding in findings if finding.rule == "W6"),
        "clipped": sum(1 for finding in findings if finding.rule == "W3"),
        "jump_cuts": sum(1 for seam in seams if seam["jump_cut"]),
        "uncovered": sum(1 for seam in seams if seam["jump_cut"] and seam["covered_by"] is None),
    }
    result: dict[str, Any] = {
        "cut_file": str(loaded.path),
        "content_hash": loaded.content_hash,
        "timeline": {"name": _name(doc), "fps": fps},
        "total": dual_time(total, fps),
        "text": " ".join(read["text"] for read in read_back if read["text"]),
        "segments": read_back,
        "words": rows[:INLINE_WORDS],
        "truncated": len(rows) > INLINE_WORDS,
        "spilled_to": None,
        "seams": seams,
        "counts": counts,
        "warnings": [finding.as_dict() for finding in findings],
    }
    if result["truncated"]:
        full = {**result, "words": rows, "truncated": False, "spilled_to": None}
        result["spilled_to"] = spill(
            f"{_name(doc)} virtual transcript", full, config or get_config(), fallback="cut"
        )
    return result


def _read(
    id: str,
    alias: str,
    at: int,
    duration: int,
    fps: float,
    words: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "id": id,
        "source": alias,
        "at": dual_time(at, fps),
        "duration": dual_time(duration, fps),
        "words": len(words),
        "text": " ".join(str(word["word"]).strip() for word in words),
    }


def _within(
    words: Sequence[Mapping[str, Any]],
    span: tuple[int, int],
    fps: float,
) -> tuple[list[tuple[Mapping[str, Any], int]], list[tuple[Mapping[str, Any], str, int]]]:
    """Split a source's words against one segment: those wholly inside, and those it cuts.

    A word is kept when both its edges land inside the range, which is the same half-open
    reading the cut file itself uses. A word overlapping one edge is not silently dropped or
    silently kept — it is reported, with the frame that would have spared it.
    """
    kept: list[tuple[Mapping[str, Any], int]] = []
    clipped: list[tuple[Mapping[str, Any], str, int]] = []
    for word in words:
        start = frames_from_seconds(float(word["start"]), fps, IN_POINT)
        end = frames_from_seconds(float(word["end"]), fps, OUT_POINT)
        if end <= span[0] or start >= span[1]:
            continue
        if start < span[0]:
            clipped.append((word, "opens", start))
        elif end > span[1]:
            clipped.append((word, "closes", end))
        else:
            kept.append((word, start))
    return kept, clipped


def _repeats(read_back: Sequence[Mapping[str, Any]]) -> list[Finding]:
    """A run of words ending one segment and opening the next — two takes, both kept."""
    found: list[Finding] = []
    for earlier, later in zip(read_back, read_back[1:], strict=False):
        head = _spoken(str(earlier["text"]))
        tail = _spoken(str(later["text"]))
        run = next(
            (
                length
                for length in range(min(len(head), len(tail)), 0, -1)
                if head[-length:] == tail[:length]
            ),
            0,
        )
        if run >= REPEATED_RUN:
            said = " ".join(tail[:run])
            found.append(
                _finding(
                    "W4",
                    str(later["id"]),
                    f"{said!r} ends {earlier['id']} and opens {later['id']}",
                )
            )
    return found


def _seams(
    entries: Sequence[Mapping[str, Any]],
    placed: Mapping[str, tuple[int, int]],
    doc: Any,
    fps: float,
    name: str,
) -> list[dict[str, Any]]:
    """Every join between two adjacent segments, and whether an overlay rides across it.

    Only a join between two shots of the *same* source is a jump cut: cutting from one
    camera to another is an ordinary edit, and covering it would be covering nothing.

    Black between two shots is not a join at all, which is why this walks the entries as
    authored rather than the picture alone: cutting away to nothing and back is a device in
    its own right, and reporting it as an uncovered jump cut would send an agent to cover a
    seam that the gap has already broken.

    Both sides of the question come from the cut module's own placement — the seam from
    :func:`positions` and the overlay from :func:`overlay_positions`, the same two functions
    E9 and the build use. Deriving either here would be measuring a layout nobody builds.
    """
    spans = _overlay_spans(doc, name)
    seams: list[dict[str, Any]] = []
    for earlier, later in zip(entries, entries[1:], strict=False):
        if is_gap(earlier) or is_gap(later):
            continue
        at = placed[str(later.get("id"))][0]
        jump = str(earlier.get("source")) == str(later.get("source"))
        covering = [id for id, (start, length) in spans.items() if start < at < start + length]
        seams.append(
            {
                "between": [str(earlier.get("id")), str(later.get("id"))],
                "at": dual_time(at, fps),
                "jump_cut": jump,
                "covered_by": covering[0] if covering else None,
            }
        )
    return seams


def _overlay_spans(doc: Any, name: str) -> dict[str, tuple[int, int]]:
    """Where each overlay lands, from the one function that answers that for the build."""
    try:
        return overlay_positions(doc)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRequestError(
            cause=f"{name} has an overlay that does not resolve to a position.",
            fix="Call validate_cut — E9 names the overlay and what is wrong with its anchor.",
            detail={"cut_file": name},
        ) from exc


def _uncovered(seams: Sequence[Mapping[str, Any]]) -> list[Finding]:
    return [
        _finding(
            "W7",
            str(seam["between"][1]),
            f"{seam['between'][0]} and {seam['between'][1]} are both one source, uncovered",
        )
        for seam in seams
        if seam["jump_cut"] and seam["covered_by"] is None
    ]


def _spoken(text: str) -> list[str]:
    return [word.strip(".,!?").casefold() for word in text.split() if word.strip(".,!?")]


def _words_of(path: str) -> list[Mapping[str, Any]]:
    """A transcript document's words, in the order it wrote them."""
    document = records.read(_readable(path))
    words = document.get("words")
    if not isinstance(words, list):
        raise InvalidRequestError(
            cause=f"{path} has no words, so nothing can be read back from it.",
            fix="Pass the transcript document transcribe_audio wrote, not another job's result.",
            detail={"transcript": path},
        )
    return [word for word in words if isinstance(word, Mapping)]


def _readable(path: str) -> Path:
    found = Path(path)
    if not found.is_file():
        raise InvalidRequestError(
            cause=f"No transcript at {path}.",
            fix="Call transcribe_audio for the source, then pass the path it reports.",
            detail={"transcript": path},
        )
    return found


def _fps(doc: Any, name: str) -> float:
    timeline = doc.get("timeline") if isinstance(doc, Mapping) else None
    fps = timeline.get("fps") if isinstance(timeline, Mapping) else None
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or fps <= 0:
        raise InvalidRequestError(
            cause=f"{name} has no timeline.fps, so words have no frame to land on.",
            fix="Call validate_cut — timeline.fps is required by the schema.",
            detail={"cut_file": name},
        )
    return float(fps)


def _entries(doc: Any, name: str) -> list[Mapping[str, Any]]:
    """``segments`` as authored — picture and black, in order, because adjacency matters."""
    segments = doc.get("segments") if isinstance(doc, Mapping) else None
    if not isinstance(segments, list) or not segments:
        raise InvalidRequestError(
            cause=f"{name} has no segments to read back.",
            fix="Call validate_cut — a cut file needs at least one segment.",
            detail={"cut_file": name},
        )
    return [segment for segment in segments if isinstance(segment, Mapping)]


def _segments(entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Only the entries that play a clip: black has no source, so it contains no words."""
    return [entry for entry in entries if not is_gap(entry)]


def _placed(doc: Any, name: str) -> dict[str, tuple[int, int]]:
    """Every segment's ``(start, duration)`` — from the cut module, never derived here.

    ``positions`` is what E9 validates against and what the build places against, so a
    reading that summed its own durations could put a word at a frame no clip occupies.
    """
    try:
        return positions(doc)
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRequestError(
            cause=f"{name} has a segment that does not resolve to a position.",
            fix="Call validate_cut — it names the segment and what is wrong with its range.",
            detail={"cut_file": name},
        ) from exc


def _span(segment: Mapping[str, Any], name: str) -> tuple[int, int]:
    id = str(segment.get("id"))
    try:
        span = (int(segment["in"]), int(segment["out"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidRequestError(
            cause=f"segment {id} in {name} has no readable in/out.",
            fix="Call validate_cut — in and out are integer frames, half-open [in, out).",
            detail={"cut_file": name, "segment": id},
        ) from exc
    if span[1] <= span[0]:
        raise InvalidRequestError(
            cause=f"segment {id} in {name} ends before it starts.",
            fix="Call validate_cut — ranges are half-open [in, out) with out above in.",
            detail={"cut_file": name, "segment": id},
        )
    return span


def _name(doc: Any) -> str:
    timeline = doc.get("timeline") if isinstance(doc, Mapping) else None
    name = timeline.get("name") if isinstance(timeline, Mapping) else None
    return str(name) if name else "cut"


def _finding(rule: str, id: str | None, message: str, fix_hint: str | None = None) -> Finding:
    return Finding(rule=rule, id=id, message=message, fix_hint=fix_hint or _FIX_HINTS[rule])


__all__ = ["INLINE_WORDS", "virtual_transcript"]
