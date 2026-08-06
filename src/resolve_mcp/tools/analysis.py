"""Analysis starters: heavy reading of the audio, each one a typed job.

The tools here return a job record, never the analysis — the file they write is bigger than
any tool result should be, and reading it is the agent's job.
"""

from __future__ import annotations

from typing import Any

from ..analysis import correlate, music, silence, transcribe, transcript, whisper
from ..resolve.connection import get_connection
from ..resolve.timeline import FIRST_TRACK
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


@tool
def correlate_timeline(
    beats: str,
    timeline: str | None = None,
    audio: str | None = None,
    tunes: str | None = None,
    solos: str | None = None,
    angles: dict[str, Any] | None = None,
    track: int = FIRST_TRACK,
    refresh: bool = False,
) -> dict[str, Any]:
    """Measure a cut against the music it was cut to. Returns a job to poll, not the measurement.

    This is how style is learned from your own past edits, and how a cut you just built gets
    reviewed before anyone watches it: for every shot, how far its start sits from the
    nearest beat and from the nearest transient (signed — negative is early, positive is
    late), where in the bar that lands, which tune it happens in, who was out front, how
    long the shot runs and which angle it came from.

    beats is the beats file analyze_music wrote. audio is the same master mix it analysed,
    and naming it is what makes the transient column real — onsets are not stored by any
    other job, so they are measured here. tunes and solos are the structure job's files.
    angles is the angle labels themselves, not a path: {"C0012.mp4": {"role": "drums"}}, or
    just {"C0012.mp4": "drums"} — you keep the sidecar, you read it, you pass what it says.
    Each of these is optional, and each one absent means that column reads null rather than
    a guess.

    timeline names the cut, defaulting to the open one; track is the video track it sits on.
    The result names a JSON file of one record per shot — grep it, or read a slice with sed
    — and returns the reading inline: offset statistics with early and late counted apart, a
    histogram of where in the bar the cuts land, shot-duration stats, and how much of the
    cut each angle and role holds.

    Nothing here judges the edit. Two frames late is reported as two frames late; what
    counts as musical belongs in your style profile, not in this server.
    """
    connection = get_connection()
    return {
        "job": correlate.correlate_timeline(
            connection,
            beats=beats,
            timeline=timeline,
            audio=audio,
            tunes=tunes,
            solos=solos,
            angles=angles,
            track=track,
            refresh=refresh,
        )
    }


TOOLS: tuple[Any, ...] = (transcribe_audio, analyze_music, correlate_timeline)

__all__ = ["TOOLS", "analyze_music", "correlate_timeline", "transcribe_audio"]
