"""The typed starter for music analysis — beats, downbeats and energy on the master mix."""

from __future__ import annotations

from typing import Any

from ..analysis import music
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


TOOLS: tuple[Any, ...] = (analyze_music,)

__all__ = ["TOOLS", "analyze_music"]
