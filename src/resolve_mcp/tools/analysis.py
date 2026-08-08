"""Analysis starters: heavy reading of the audio, each one a typed job.

The tools here return a job record, never the analysis — the file they write is bigger than
any tool result should be, and reading it is the agent's job.
"""

from __future__ import annotations

from typing import Any

from ..analysis import (
    applause,
    correlate,
    fills,
    music,
    silence,
    solos,
    structure,
    transcribe,
    transcript,
    whisper,
)
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
def analyze_structure(
    audio: str,
    tunes: bool = True,
    solos: bool = False,
    stems: str | None = None,
    threshold: float = applause.DEFAULT_THRESHOLD,
    tune_seconds: float = applause.DEFAULT_TUNE_SECONDS,
    density_per_second: float = applause.DEFAULT_DENSITY_PER_SECOND,
    solo_seconds: float = solos.DEFAULT_MINIMUM_SECONDS,
    snap_seconds: float = solos.DEFAULT_SNAP_SECONDS,
    refresh: bool = False,
) -> dict[str, Any]:
    """Start tune-boundary and solo-change analysis of a concert. Returns a job to poll.

    A jazz set has no verses to segment, so the boundaries come from the room: applause is
    tagged on the master mix, and the music between two bursts is a tune. The tunes file
    holds one record per tune — its number, start, end, length, the seconds of applause on
    either side of it, and the beats per second measured under it — which is what a
    songs.json author reads before placing markers. Inline you get how many tunes, how much
    clapping, and where the longest one starts.

    Applause on its own over-calls: announcing the band at length, or talking between two
    rounds of clapping, looks exactly like a tune. So a call also has to have a musical
    pulse under it, measured against the beat grid, and this tool reads that grid the way
    the solo half does — analyze_music's if it exists, or it detects one and leaves it
    behind. Inline you get how many calls that dropped, and the two shoulders it decided
    on. density_per_second is the floor in beats per second; set it to 0 to keep every
    call the tagger made, which is also the way to run this tool with no beat model
    installed.

    solos=true adds the second half and needs stems: pass the directory a separate_stems
    job returned. It measures which stem is out front over its own quiet baseline, and
    where the residual stem's brightness steps — one horn out, piano in, both inside
    `other` — and writes one record per change point: where it is called, where it was
    measured, whether it landed on a downbeat, and what handed over to what. Change points
    are snapped to the nearest downbeat within a couple of seconds, so this half reads the
    beat grid — analyze_music's if it exists, or it detects one and leaves it for that tool
    to reuse.

    Nothing here names the soloist: no separator ships a horn stem or a piano stem, so what
    is measured is that the front changed and when. threshold moves how sure the tagger has
    to be that it is hearing a room; tune_seconds is how much music has to sit between two
    bursts before it is a tune rather than an announcement; solo_seconds is the same idea
    for a stretch out front; snap_seconds is how far a change may reach for a downbeat
    before it is called where it was measured instead. Reruns on unchanged audio come back
    from cache immediately.
    """
    return {
        "job": structure.analyze_structure(
            audio,
            tunes=tunes,
            solos=solos,
            stems=stems,
            threshold=threshold,
            tune_seconds=tune_seconds,
            density_per_second=density_per_second,
            solo_seconds=solo_seconds,
            snap_seconds=snap_seconds,
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
    audio_at: Any | None = None,
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

    The audio is normally located by finding it on the timeline. When it is not there at all
    — a multicam carries its own audio angle, and the mix itself was never laid down —
    audio_at names the timeline frame the analysed audio starts at, dual time as everywhere.
    A render of the whole timeline starts at its first frame, which is how that is knowable.
    Check alignment in the result: mode "given" is what you asked for, and mode "audio_clip"
    with matched false means the times were taken off a clip nobody vouched for.

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
            audio_at=audio_at,
            refresh=refresh,
        )
    }


@tool
def detect_drum_fills(
    stems: str,
    audio: str,
    minimum_confidence: float = fills.DEFAULT_MINIMUM_CONFIDENCE,
    refresh: bool = False,
) -> dict[str, Any]:
    """Start drum-fill detection over separated drum stems. Returns a job to poll.

    stems is the directory a separate_stems job reported — the kick, snare and toms files
    its second pass wrote. audio is the master mix those stems came from: fills are reported
    against its beat grid, and if music analysis already ran over it the beats come from
    cache rather than the model again.

    The result names one JSON file and summarises it. Every candidate carries its start and
    end (both on the grid), the bar and beat it starts on, the beat it resolves into, hits
    per stem, how much busier it is than the median beat of this performance, and a 0-1
    confidence with the four factors behind it — density, tom share, whether a hit lands on
    the resolution point, and where that point sits in the phrase. Inline you get the count,
    the mean confidence and the strongest candidate.

    These are candidates, not verdicts: a burst of toms into a downbeat is evidence, and
    whether it is a fill, a trade or a save is yours to read. Runs longer than two bars are
    counted and left out — that is a drum solo, a different question. minimum_confidence is
    the floor on what gets written; refresh redoes work the cache would answer for.
    """
    return {
        "job": fills.detect_drum_fills(
            stems,
            audio,
            minimum_confidence=minimum_confidence,
            refresh=refresh,
        )
    }


TOOLS: tuple[Any, ...] = (
    transcribe_audio,
    analyze_music,
    analyze_structure,
    detect_drum_fills,
    correlate_timeline,
)

__all__ = [
    "TOOLS",
    "analyze_music",
    "analyze_structure",
    "correlate_timeline",
    "detect_drum_fills",
    "transcribe_audio",
]
