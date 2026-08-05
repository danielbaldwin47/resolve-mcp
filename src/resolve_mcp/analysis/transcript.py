"""The transcript document: what gets written, and what comes back inline.

The vocabulary lives here too — ``Word``, ``Transcription``, ``Transcriber`` — so that the
backend module and the job module share one shape without importing each other.

Two decisions are worth stating, because both are about the agent reading this file rather
than the server producing it:

* **One record per line.** ``json.dumps(indent=2)`` spreads a word over five lines, so
  ``grep -n '"word": "bridge"'`` finds a fragment with no timestamp attached to it. Each
  word, silence span and unsure region is written as one compact object on one line, which
  greps as a whole record and still parses as ordinary JSON.

* **Nothing is called a flub.** The document reports confidence and gaps; deciding that
  0.31 on "bridge" plus two seconds of room afterwards is a retake is editorial judgment,
  and it belongs to whoever is reading, not to this module.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

SCHEMA = 1
PLACES = 3
SECONDS_PER_MINUTE = 60.0
PREVIEW_REGIONS = 20

DEFAULT_LOW_CONFIDENCE = 0.5
"""Below this a word counts as unsure. Lives here because the document is what applies it."""


class Word(NamedTuple):
    """One word as the model heard it. ``confidence`` is 0-1, higher is surer."""

    text: str
    start: float
    end: float
    confidence: float


class Transcription(NamedTuple):
    """What a backend returns: the words, and the language it decided it was hearing."""

    words: tuple[Word, ...]
    language: str | None = None


Transcriber = Callable[[Path, Mapping[str, Any]], Transcription]
"""The backend seam. The default shells out to faster-whisper; tests hand in their own."""


def document(
    audio: Mapping[str, Any],
    params: Mapping[str, Any],
    words: Sequence[Word],
    silence: Sequence[Mapping[str, float]],
    language: str | None = None,
) -> dict[str, Any]:
    """The whole transcript, ready to write: header, gist stats, then the records."""
    below = float(params.get("low_confidence", DEFAULT_LOW_CONFIDENCE))
    unsure = regions(words, below=below)
    return {
        "schema": SCHEMA,
        "kind": "transcript",
        "language": language,
        "audio": dict(audio),
        "params": dict(params),
        "stats": stats(audio, words, silence, unsure, below),
        "words": [_word(one) for one in words],
        "silence": [dict(one) for one in silence],
        "low_confidence": unsure,
    }


def stats(
    audio: Mapping[str, Any],
    words: Sequence[Word],
    silence: Sequence[Mapping[str, float]],
    unsure: Sequence[Mapping[str, Any]],
    below: float,
) -> dict[str, Any]:
    """The numbers worth having before deciding whether to read the file at all.

    A duration the WAV header did not give up stays ``None`` here and takes every stat
    derived from it with it. Calling it zero would report a transcript of a two-hour set as
    nought seconds of speech, which reads as a finding rather than as a missing reading.
    """
    reported = audio.get("duration_seconds")
    duration = float(reported) if reported is not None else None
    quiet = sum(float(one["duration"]) for one in silence)
    confidences = [one.confidence for one in words]
    return {
        "duration_seconds": round(duration, PLACES) if duration is not None else None,
        "word_count": len(words),
        "words_per_minute": (
            round(len(words) / (duration / SECONDS_PER_MINUTE), 1) if duration else None
        ),
        "mean_confidence": (
            round(sum(confidences) / len(confidences), PLACES) if confidences else None
        ),
        "low_confidence_threshold": below,
        "low_confidence_words": sum(1 for one in confidences if one < below),
        "low_confidence_regions": len(unsure),
        "silence_spans": len(silence),
        "silence_seconds": round(quiet, PLACES),
        "speech_seconds": (
            round(max(duration - quiet, 0.0), PLACES) if duration is not None else None
        ),
        "longest_silence_seconds": (
            round(max(float(one["duration"]) for one in silence), PLACES) if silence else 0.0
        ),
    }


def regions(words: Sequence[Word], below: float) -> list[dict[str, Any]]:
    """Neighbouring words the model was unsure of, merged into stretches worth listening to.

    Merged by adjacency in the transcript rather than by a time gap: two unsure words with
    a confident one between them are two separate doubts, however close together they sit.
    """
    found: list[dict[str, Any]] = []
    run: list[Word] = []
    stream: list[Word | None] = [*words, None]
    for word in stream:
        if word is not None and word.confidence < below:
            run.append(word)
            continue
        if run:
            found.append(_region(run))
            run = []
    return found


def write(path: Path, transcript: Mapping[str, Any]) -> Path:
    """Write the document one record per line, and return where it landed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ",\n".join(
        _rows(key, value) if isinstance(value, list) else _field(key, value)
        for key, value in transcript.items()
    )
    path.write_text(f"{{\n{body}\n}}\n", encoding="utf-8")
    return path


def gist(transcript: Mapping[str, Any], path: Path) -> dict[str, Any]:
    """What the job returns inline: where the file is, and enough to decide about reading it.

    The unsure regions come back capped rather than in full — they are the reason an agent
    opens a transcript, so a handful inline often answers the question outright, while a
    take that went badly enough to produce hundreds of them must not blow the output cap.
    """
    unsure = list(transcript.get("low_confidence") or [])
    audio = dict(transcript.get("audio") or {})
    return {
        "path": str(path),
        "language": transcript.get("language"),
        "stats": transcript.get("stats"),
        "low_confidence_regions": unsure[:PREVIEW_REGIONS],
        "low_confidence_truncated": len(unsure) > PREVIEW_REGIONS,
        "audio": audio,
    }


def _word(word: Word) -> dict[str, Any]:
    return {
        "start": round(word.start, PLACES),
        "end": round(word.end, PLACES),
        "word": word.text,
        "confidence": round(word.confidence, PLACES),
    }


def _region(run: Sequence[Word]) -> dict[str, Any]:
    start = round(run[0].start, PLACES)
    end = round(run[-1].end, PLACES)
    return {
        "start": start,
        "end": end,
        "duration": round(end - start, PLACES),
        "words": len(run),
        "text": " ".join(one.text.strip() for one in run),
    }


def _rows(key: str, rows: Sequence[Any]) -> str:
    """A list of records, one per line — the shape that makes the file greppable."""
    if not rows:
        return f'  "{key}": []'
    body = ",\n".join(f"    {json.dumps(row)}" for row in rows)
    return f'  "{key}": [\n{body}\n  ]'


def _field(key: str, value: Any) -> str:
    return f'  "{key}": {_indented(json.dumps(value, indent=2))}'


def _indented(rendered: str) -> str:
    return rendered.replace("\n", "\n  ")
