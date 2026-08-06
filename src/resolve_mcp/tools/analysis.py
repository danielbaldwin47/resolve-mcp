"""Analysis starters: heavy reading of the audio, each one a typed job.

The tools here return a job record, never the analysis — the file they write is bigger than
any tool result should be, and reading it is the agent's job.
"""

from __future__ import annotations

from typing import Any

from ..analysis import music, silence, transcribe, transcript, whisper
from ..resolve.connection import get_connection
from .envelope import tool


@tool
def analyze_music(
    audio: str,
    beats: bool = True,
    energy: bool = True,
    window_seconds: float = music.DEFAULT_WINDOW_SECONDS,
    hop_seconds: float = music.DEFAULT_HOP_SECONDS,
    refresh: bool = False,
) -> dict[str, Any]:
    """Start beat, downbeat and energy analysis of a WAV. Returns a job to poll, not the analysis.

    audio is the master mix: the file the director handed over, or the path an audio
    acquisition job returned. Nothing here touches Resolve, so it runs while a render does.

    The result names two files and summarises them. beats holds one record per beat — time,
    beat number, bar, position in the bar, whether it is a downbeat — and energy holds one
    record per window of loudness (LUFS), level (RMS dBFS) and onset density. Both are JSON
    with one record per line: read a slice with sed, or grep for a time, rather than asking
    for the whole concert. Inline you get tempo, meter, counts, the integrated loudness and
    where the loudest and quietest windows are.

    window_seconds and hop_seconds shape the energy curve — 3 seconds every half second by
    default, which is the EBU short-term window. beats=false or energy=false runs one half.
    Reruns on unchanged audio come back from cache immediately; refresh=true redoes the work.
    """
    return {
        "job": music.analyze_music(
            audio,
            beats=beats,
            energy=energy,
            window_seconds=window_seconds,
            hop_seconds=hop_seconds,
            refresh=refresh,
        )
    }


@tool
def transcribe_audio(
    clip: str | None = None,
    bin: str | None = None,  # noqa: A002 - "bin" is the Resolve term the agent uses
    timeline: str | None = None,
    model: str = whisper.DEFAULT_MODEL,
    language: str | None = None,
    low_confidence: float = transcript.DEFAULT_LOW_CONFIDENCE,
    silence_threshold_db: float = silence.DEFAULT_THRESHOLD_DB,
    min_silence_seconds: float = silence.DEFAULT_MIN_SECONDS,
    refresh: bool = False,
) -> dict[str, Any]:
    """Start a word-level transcript of one source clip, or of the timeline mix.

    Name clip (with bin if the name is ambiguous) for a source file; name timeline, or
    neither, for the open timeline's mix — the audio is acquired for you either way. Poll
    the returned job with get_job.

    The result is a path to timestamped JSON plus gist stats: duration, word count, mean
    confidence, how much of it is silence, and the unsure regions (a capped preview of them
    comes back inline). The file holds one record per line, so grep it and read slices —
    every word carries start, end and a 0-1 confidence, and the silence spans are measured
    off the waveform rather than off the gaps between words, so a held chord or a room of
    applause is not mistaken for room to cut in.

    Nothing here labels a flub, a retake or filler. Low confidence next to a long silence is
    evidence; what it means is yours to decide. language forces one instead of detecting it;
    low_confidence moves the threshold a word counts as unsure below; refresh re-transcribes
    audio the cache would otherwise answer for.
    """
    connection = get_connection()
    return {
        "job": transcribe.transcribe_audio(
            connection,
            clip=clip,
            bin=bin,
            timeline=timeline,
            model=model,
            language=language,
            low_confidence=low_confidence,
            silence_threshold_db=silence_threshold_db,
            min_silence_seconds=min_silence_seconds,
            refresh=refresh,
        )
    }


TOOLS: tuple[Any, ...] = (transcribe_audio, analyze_music)

__all__ = ["TOOLS", "analyze_music", "transcribe_audio"]
