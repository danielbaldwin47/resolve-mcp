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
    phrases,
    silence,
    solos,
    structure,
    transcribe,
    transcript,
    whisper,
)
from ..analysis import bars as bars_module  # `bars` is a tool argument on correlate_timeline
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
def analyze_structure(
    audio: str,
    tunes: bool = True,
    solos: bool = False,
    stems: str | None = None,
    threshold: float = applause.DEFAULT_THRESHOLD,
    scale: float = applause.DEFAULT_SCALE,
    tune_seconds: float = applause.DEFAULT_TUNE_SECONDS,
    settle_seconds: float = applause.DEFAULT_SETTLE_SECONDS,
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

    A board mix needs two more things, and gets them by default. The tagger is far less
    sure of clapping it hears through a desk feed than of clapping in a room — a whole set
    can peak under the 0.3 an audible crowd clears easily — so the threshold you pass is a
    ceiling: if the file holds almost no clapping over it, the curve is read at `scale` of
    its own peak instead. read_at_own_scale says whether that happened, and threshold_used
    and burst_seconds_used what the file was actually read at, beside the threshold and
    burst_seconds you asked for; a mix the threshold does find clapping in is read exactly
    where it always was. scale=0 turns the fallback off. And the applause is
    not where the next tune starts: after it come the announcement, the re-tune and the
    count-in, up to a minute of them, all far below playing level. So each boundary walks
    forward to where the mix comes up and stays up for settle_seconds, and a call the band
    never comes in on is not a tune at all. Each tune record carries the talk_seconds that
    were skipped, and inline you get how many boundaries moved and by how much in total.
    This reads analyze_music's loudness curve, measuring one if there is none;
    settle_seconds=0 turns it off and puts the boundary back on the end of the applause.

    solos=true adds the second half and needs stems: pass the directory a separate_stems
    job returned. It measures which stem is out front over its own quiet baseline, and
    where one stem's brightness steps — a handover inside that stem, which energy cannot
    see — and writes one record per change point: where it is called, where it was
    measured, whether it landed on a downbeat, and what handed over to what. Change points
    are snapped to the nearest downbeat within a couple of seconds, so this half reads the
    beat grid — analyze_music's if it exists, or it detects one and leaves it for that tool
    to reuse.

    Which stems those are depends on what separate_stems wrote. With split_wind=true its
    third pass is on disk, and then the voices are `wind` and `comp` rather than the
    `other` they add up to, and the brightness is read off `wind`: one horn giving way to
    another. Without it the voices include `other` and the brightness comes off `other`,
    where a step is a horn giving way to a piano. The gist says which, in `voices` and
    `timbre_stem`, so a record can be read back without guessing which run it was.

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
            scale=scale,
            tune_seconds=tune_seconds,
            settle_seconds=settle_seconds,
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
    deltas: str | None = None,
    supers: str | None = None,
    bars: str | None = None,
    angles: dict[str, Any] | None = None,
    track: int | None = None,
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
    other job, so they are measured here. tunes and solos are the structure job's files. bars
    is the bar map detect_bars wrote, and it is what makes the form measurable: the in_bar
    column comes from the beat model's own downbeats, so on material where the model commits
    to no meter it says nothing, and the bar map is the second reading that recovers the bar
    line. Name it and every record also carries which bar of the form the cut is on, that
    bar's place in the four-bar group, and how far off the bar line it landed.
    angles is the angle labels themselves, not a path: {"C0012.mp4": {"role": "drums"}}, or
    just {"C0012.mp4": "drums"} — you keep the sidecar, you read it, you pass what it says.
    An entry's subject — what that camera is framed on — is read from "subject", or from a
    role written the way the corpus writes them, "drums-tight" or "ensemble-wide"; a one-word
    role names a character rather than a subject and labels nothing. Add "voice" where your
    sidecar names people and the solo map names stems: {"subject": "mike", "voice": "wind"}.
    Each of these is optional, and each one absent means that column reads null rather than
    a guess.

    deltas is a cut-delta catalog measured off a *rendered* picture — how far the picture
    steps at each cut (0 to 1) and whether the step is small enough to read as a jump cut,
    the 30-degree-rule check. Nothing on a timeline can answer that: it takes frames either
    side of every boundary, so the render comes first and this joins its numbers on by time.
    gauntlet/tools/ab_pack.py writes such a catalog as cuts.json. Render the whole timeline,
    not a span, or the times will not line up — the visual_delta block in the result says how
    many cuts joined and how many did not.

    supers is the other catalog off that render: when each burned-in graphic — lower third,
    title card, bug — is on screen, which again nothing on a timeline can answer. Every cut is
    measured against them and gets straddles_super: true where a graphic is up on both sides
    of it, plus super_kind. A super that arrives with the shot, or clears the frame before it,
    is not a straddle. Read the two kinds differently: a lower third held across cuts is how
    titling works and the human deliverables are full of them, while a cut inside a title card
    is a finding. ab_pack.py writes this one as supers.json beside its cuts.json; same clock
    rule as deltas.

    The audio is normally located by finding it on the timeline. When it is not there at all
    — a multicam carries its own audio angle, and the mix itself was never laid down —
    audio_at names the timeline frame the analysed audio starts at, dual time as everywhere.
    A render of the whole timeline starts at its first frame, which is how that is knowable.
    Check alignment in the result: mode "given" is what you asked for, and mode "audio_clip"
    with matched false means the times were taken off a clip nobody vouched for.

    timeline names the cut, defaulting to the open one. What gets measured is the *visible*
    edit: every frame taken from the topmost enabled video item, so a shot on V2 is a shot
    and the frames it covers are its own rather than the clip's underneath, and a stretch no
    track covers is a black shot with clip and role null. Each record says which track it
    came from, and visible in the result says which tracks were read and how many blacks
    there were. Pass track=1 to measure one video track alone instead, laid out as the
    editor left it — a different question, and the one to ask about a single-track cut.

    The result names a JSON file of one record per shot — grep it, or read a slice with sed
    — and returns the reading inline: offset statistics with early and late counted apart, a
    histogram of where in the bar the cuts land, shot-duration stats, and how much of the
    cut each angle and role holds (black counted on its own line, apart from the angles the
    sidecar has not named).

    With both a solo map and subject labels it also carries the on-soloist track: per shot,
    what it is framed on and whether that is the player out front, split in seconds where the
    front changes mid-shot; and inline, what share of the solo-window screen time went to the
    soloist, to the ensemble, to a player who was not soloing and to neither (an audience
    camera, a room shot). Screen time no label reaches, and black, are counted apart rather
    than folded into the shares. on_soloist_by says how a shot reached the soloist line —
    joined against the solo map, or asserted by a camera the sidecar says follows the front —
    and soloist_seconds_by_follow_camera is how much of the share is the second kind.

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
            deltas=deltas,
            supers=supers,
            bars=bars,
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


@tool
def detect_phrases(
    stems: str,
    audio: str,
    stem: str = phrases.DEFAULT_STEM,
    minimum_confidence: float = phrases.DEFAULT_MINIMUM_CONFIDENCE,
    refresh: bool = False,
) -> dict[str, Any]:
    """Start phrase-boundary detection over the soloist's stem. Returns a job to poll.

    The phrase is the cut-placement unit: a director says "cut after the sax's phrase" far
    more often than they name a beat. This finds those endings. stems is the directory a
    separate_stems job reported; stem names which one holds the line, and defaults to other —
    the stem the horns and the piano land in. audio is the master mix those stems came from:
    boundaries are placed against its beat grid, and if music analysis already ran over it the
    beats come from cache rather than the model again.

    The result names one JSON file and summarises it. Every boundary carries two times — t,
    the frame to cut on, which is the first beat inside the rest; and measured_t, where the
    line actually stopped — plus the bar and beat it lands on, the length of the rest, how
    long the ending note was held against the median note of this solo, the leap in semitones
    into the next phrase, and a 0-1 confidence with the four factors behind it. Inline you get
    the count, the median phrase length, the mean confidence and the strongest boundary.

    These are candidates, not verdicts, and the reading is monophonic: the residual stem holds
    piano under horn, so a chord reads as one note and a busy comp reads as a busy line.
    Endings closer together than half a bar are counted and left out as one ending heard
    twice. minimum_confidence is the floor on what gets written; refresh redoes work the cache
    would answer for.
    """
    return {
        "job": phrases.detect_phrases(
            stems,
            audio,
            stem=stem,
            minimum_confidence=minimum_confidence,
            refresh=refresh,
        )
    }


@tool
def detect_bars(
    audio: str,
    stems: str | None = None,
    stem: str = bars_module.DEFAULT_STEM,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    minimum_confidence: float = bars_module.DEFAULT_MINIMUM_CONFIDENCE,
    refresh: bool = False,
) -> dict[str, Any]:
    """Find where the bar starts, when the beat model would not say. Returns a job to poll.

    analyze_music reports the meter the beat model committed to, and on some material it does
    not commit: over a jazz set it tracks the swung eighth as the beat and calls every one of
    them a downbeat, so the meter reads 1 and nothing can cut on the "1". This is the second
    pass that recovers the bar. audio is the master mix; if music analysis already ran over it
    the beat grid comes from cache rather than the model again.

    **Ask about one tune, not one set.** The reading is a single fold, meter and phase, and a
    set has a different tempo and a different form every tune with applause between them — run
    over all of it at once the answer is wrong before the arithmetic starts, and the evidence
    averages to nothing. Pass start_seconds and end_seconds from the tune boundaries
    analyze_structure wrote.

    Two readings, in order. The pulse: a grid running faster than anything you would tap is a
    subdivision, so every k-th beat is tried and the one that lands in the tapping range and
    sits on the accents wins — a grid already in that range is kept exactly as it is. Then the
    bar line: each meter and each phase scored by how far its beats sit above the rest, the
    winner's lead over the runner-up, and — the check that matters most on this material —
    whether four-bar windows of the span reach the same answer on their own. A meter that
    holds for eight bars and not the next eight is not the meter, whatever its contrast, and
    that share comes back as agreement beside the confidence.

    The result names one JSON file and summarises it. Every bar carries its start in seconds
    (the downbeat time), its length, how many beats it holds, the grid beat it starts on, and
    its place in the four-bar group. Inline you get the meter, the tempo, how the meter was
    arrived at — model, inferred, or refused — the confidence, and the grid's own reading
    beside it, so 214 bpm in a meter of one against 107 in four is visible rather than
    inherited.

    A reading with no accents behind it is refused rather than guessed: source reads refused,
    meter is null, and no bars are written. That is the honest answer, and minimum_confidence
    is where the line sits — 0 writes whatever the arithmetic found.

    stems is optional: pass the directory a separate_stems job reported, and stem names which
    one the accents are read off. Worth doing exactly when the mix will not carry the pulse —
    brushes, a quiet room — because the bass walks quarters and is the strongest witness to
    the beat there is. refresh redoes work the cache would answer for.
    """
    return {
        "job": bars_module.detect_bars(
            audio,
            stems=stems,
            stem=stem,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            minimum_confidence=minimum_confidence,
            refresh=refresh,
        )
    }


TOOLS: tuple[Any, ...] = (
    transcribe_audio,
    analyze_music,
    analyze_structure,
    detect_bars,
    detect_drum_fills,
    detect_phrases,
    correlate_timeline,
)

__all__ = [
    "TOOLS",
    "analyze_music",
    "analyze_structure",
    "correlate_timeline",
    "detect_bars",
    "detect_drum_fills",
    "detect_phrases",
    "transcribe_audio",
]
