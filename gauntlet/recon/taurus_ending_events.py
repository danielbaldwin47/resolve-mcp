"""Extract the concert-pillar event inputs for the Taurus People ENDING window.

Same four cached analyses taurus_events.py reads (solos / fills / phrases / energy), the same
frame mapping, but the window is the last 90 s of the set: mix 3976.15-4066.15 s, Zinc SYNC
181733-183891, deliverable 407.66-497.66 s.

Two things this adds over the opening extractor, because an ending is not an opening:

* the beat grid inside the window (the cached beats document) — where the pulse stops is the
  single most useful fact about a ritardando, and beat_trust.json already flagged a 37.6 s
  hole starting at 4047.34 s;
* a fine decay probe measured off the master mix itself. The cached energy curve is a 3.0 s
  window hopped 0.5 s — it cannot say where the last note lands, only roughly when it got
  quiet. ffmpeg pulls the tail out at 48 kHz mono and this measures a 10 ms RMS envelope, a
  spectral-flux onset list and a high-frequency share (applause is broadband and sustained;
  a held chord decays smoothly), then reports the decay slope in dB/s and the room-tone floor
  of the outro that follows the song.

READ-ONLY: five files and one ffmpeg read in, one file out. Never connects to Resolve.
"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

RECON = Path(__file__).parent
ANALYSIS = Path(r"C:\Users\Daniel\AppData\Local\resolve-mcp\analysis")
OUT = RECON / "taurus_ending_events.json"
RUN = RECON / "taurus_analysis.json"

MIX = Path(r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav")

SPAN = (3568.48, 4066.15)
"""The whole Taurus People piece, for density context."""
WINDOW = (3976.15, 4066.15)
"""The ending piece: the deliverable's last 90 s."""
DELIVERABLE_ZERO = 3568.4815
"""Mix second the Taurus deliverable starts at, so window times can be quoted in its clock."""

FPS = 23.976
FRAME_ZERO = 86401

ENERGY = ANALYSIS / "Zinc-Set-2-Reaper-v4-0b66b71707de-energy.json"
BEATS = ANALYSIS / "Zinc-Set-2-Reaper-v4-62537c590a14-beats.json"

PEAK_RADIUS = 3.0
PEAK_PROMINENCE_DB = 1.5
COINCIDENCE = 0.6
SPACING = 4.0

BASE = {"solo_change:lead": 0.80, "solo_change:timbre": 0.60}

# --- decay probe settings ---------------------------------------------------------------
PROBE = (3966.0, 4086.0)
"""What ffmpeg pulls: the window with 10 s of lead-in and 20 s of the outro behind it."""
HOP = 0.010
FRAME_LEN = 0.030
FLOOR = (4080.0, 4085.0)
"""Room tone after the applause has died — the floor every decay number is quoted against.

Not 4070-4080: the applause is still running there at -30 to -40 dB, and quoting the decay
against applause makes the ending look like it never got quiet.
"""


def frame(seconds: float) -> int:
    return FRAME_ZERO + round(seconds * FPS)


def timed(seconds: float | None) -> dict[str, Any] | None:
    if seconds is None:
        return None
    value = float(seconds)
    return {
        "t": round(value, 3),
        "frame": frame(value),
        "deliverable_t": round(value - DELIVERABLE_ZERO, 3),
        "window_t": round(value - WINDOW[0], 3),
    }


def inside(seconds: float | None, span: tuple[float, float] = WINDOW) -> bool:
    return seconds is not None and span[0] <= float(seconds) <= span[1]


def document(path: Path, field: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = list(raw.get(field) or [])
    header = {k: v for k, v in raw.items() if k != field}
    return header, rows


def paths_from_run() -> dict[str, Path]:
    run = json.loads(RUN.read_text(encoding="utf-8"))
    jobs = run["jobs"]
    structure = jobs["analyze_structure"]["result"]
    return {
        "solos": Path(structure["solos"]["path"]),
        "fills": Path(jobs["detect_drum_fills"]["result"]["path"]),
        "phrases": Path(jobs["detect_phrases"]["result"]["path"]),
    }


def energy_peaks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Local loudness maxima inside the window, with the prominence that made each one."""
    reach = 2 * PEAK_RADIUS
    near = [r for r in rows if WINDOW[0] - reach <= r["t"] <= WINDOW[1] + reach]
    peaks: list[dict[str, Any]] = []
    for row in near:
        if not inside(row["t"]):
            continue
        neighbours = [
            other
            for other in near
            if abs(other["t"] - row["t"]) <= PEAK_RADIUS and other is not row
        ]
        wider = [other for other in near if abs(other["t"] - row["t"]) <= reach]
        if not neighbours or not wider:
            continue
        if row["lufs"] < max(one["lufs"] for one in neighbours):
            continue
        prominence = row["lufs"] - min(one["lufs"] for one in wider)
        if prominence < PEAK_PROMINENCE_DB:
            continue
        if peaks and row["t"] - peaks[-1]["t"] <= PEAK_RADIUS:
            if row["lufs"] <= peaks[-1]["lufs"]:
                continue
            peaks.pop()
        peaks.append(
            {
                **timed(row["t"]),  # type: ignore[dict-item]
                "lufs": row["lufs"],
                "rms_dbfs": row["rms_dbfs"],
                "onsets_per_second": row["onsets_per_second"],
                "prominence_db": round(prominence, 2),
            }
        )
    return peaks


def rank(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for one in events:
        base = BASE.get(f"{one['kind']}:{one.get('signal')}", float(one.get("confidence") or 0.0))
        agree = [
            other
            for other in events
            if other is not one and abs(other["t"] - one["t"]) <= COINCIDENCE
        ]
        bonus = min(0.2 * len(agree), 0.4) + (0.15 if one.get("downbeat") else 0.0)
        scored.append(
            {
                **one,
                "base": round(base, 3),
                "agrees_with": sorted({other["kind"] for other in agree}),
                "score": round(base + bonus, 3),
            }
        )
    return sorted(scored, key=lambda one: (-one["score"], one["t"]))


def spaced(ranked: list[dict[str, Any]], keep: int = 8) -> list[dict[str, Any]]:
    picked: list[dict[str, Any]] = []
    for one in ranked:
        if any(abs(one["t"] - other["t"]) < SPACING for other in picked):
            continue
        picked.append(one)
        if len(picked) == keep:
            break
    return picked


# --- the tail ----------------------------------------------------------------------------


def pull_tail() -> tuple[np.ndarray, int]:
    """The probe span of the master, mono 48 kHz float, through ffmpeg."""
    from resolve_mcp.analysis import decode

    with tempfile.TemporaryDirectory() as tmp:
        cut = Path(tmp) / "tail.wav"
        subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                "ffmpeg", "-v", "error", "-y",
                "-ss", f"{PROBE[0]:.3f}", "-t", f"{PROBE[1] - PROBE[0]:.3f}",
                "-i", str(MIX),
                "-ac", "1", "-ar", "48000", "-c:a", "pcm_f32le",
                str(cut),
            ],
            check=True,
            capture_output=True,
        )
        audio = decode.read(cut)
        return np.asarray(audio.mono(), dtype=np.float32), int(audio.sample_rate)


def db(value: float) -> float:
    return round(20.0 * math.log10(value), 2) if value > 1e-9 else -180.0


def tail_shape() -> dict[str, Any]:
    """Where the last note lands, how it decays, and what it decays into.

    Three measurements, because an ending needs all three to be readable:

    * an attack list built on the ENVELOPE, not on spectral flux — the last note of this song
      arrives after the quietest moment of the piece, and a flux threshold set against the
      loud body of the tune cannot see it;
    * spectral flatness and centroid per half second — the only honest way to say where the
      music stops and the applause starts, since both are simply "sound above the floor";
    * the level quoted against the room-tone floor measured after the applause has died, not
      against the applause itself.
    """
    audio, rate = pull_tail()
    hop = int(round(HOP * rate))
    length = int(round(FRAME_LEN * rate))
    starts = np.arange(0, max(0, len(audio) - length), hop)
    times = PROBE[0] + starts / rate

    frames = np.lib.stride_tricks.sliding_window_view(audio, length)[::hop][: len(starts)]
    frames = frames.astype(np.float64)
    rms_db = 10.0 * np.log10(np.maximum((frames**2).mean(axis=1), 1e-20))
    sample_peak = np.abs(frames).max(axis=1)

    window = np.hanning(length)
    power = np.abs(np.fft.rfft(frames * window, axis=1)) ** 2 + 1e-20
    freqs = np.fft.rfftfreq(length, 1.0 / rate)
    total = power.sum(axis=1)
    flatness = np.exp(np.log(power).mean(axis=1)) / (total / power.shape[1])
    centroid = (freqs * power).sum(axis=1) / total
    hf_share = power[:, freqs >= 4000.0].sum(axis=1) / total

    floor_mask = (times >= FLOOR[0]) & (times <= FLOOR[1])
    floor_db = float(np.median(rms_db[floor_mask]))

    def at(index: int) -> dict[str, Any]:
        return {
            **timed(float(times[index])),  # type: ignore[misc]
            "rms_db": round(float(rms_db[index]), 2),
            "sample_peak": round(float(sample_peak[index]), 3),
            "flatness": round(float(flatness[index]), 4),
            "centroid_hz": round(float(centroid[index])),
            "hf_share": round(float(hf_share[index]), 3),
        }

    # --- attacks: a step up in level over the eighth of a second before it ------------
    attacks: list[dict[str, Any]] = []
    for index in range(30, len(times) - 6):
        before = float(np.median(rms_db[index - 25 : index - 7]))
        after = float(rms_db[index : index + 5].max())
        lift = after - before
        if lift < 8.0 or after < floor_db + 18.0:
            continue
        if attacks and times[index] - attacks[-1]["t"] < 0.15:
            if after <= attacks[-1]["rms_db_after"]:
                continue
            attacks.pop()
        row = at(index)
        # A note or a clap. A hand clap and a whistle are broadband and bright; a note off
        # this stage is tonal and low. Without this split the "last note" of the song is the
        # loudest clap in the applause, which is the wrong answer with the right arithmetic.
        row["voice"] = (
            "musical" if row["flatness"] <= 0.002 and row["centroid_hz"] <= 800 else "crowd"
        )
        attacks.append({**row, "rms_db_after": round(after, 2), "lift_db": round(lift, 2)})

    in_song = [one for one in attacks if WINDOW[0] <= one["t"] <= WINDOW[1]]

    # --- half-second columns over the ending and the outro behind it -----------------
    columns: list[dict[str, Any]] = []
    for edge in np.arange(WINDOW[1] - 30.0, PROBE[1] - 0.5, 0.5):
        mask = (times >= edge) & (times < edge + 0.5)
        if not mask.any():
            continue
        columns.append(
            {
                "t": round(float(edge), 2),
                "window_t": round(float(edge) - WINDOW[0], 2),
                "deliverable_t": round(float(edge) - DELIVERABLE_ZERO, 2),
                "frame": frame(float(edge)),
                "rms_db": round(float(np.median(rms_db[mask])), 2),
                "peak_db": round(float(rms_db[mask].max()), 2),
                "above_floor_db": round(float(np.median(rms_db[mask])) - floor_db, 2),
                "flatness": round(float(np.median(flatness[mask])), 4),
                "centroid_hz": round(float(np.median(centroid[mask]))),
                "hf_share": round(float(np.median(hf_share[mask])), 3),
            }
        )

    # Applause is broadband: flatness an order of magnitude above the tonal body of the tune
    # and a centroid up near a kilohertz. The first sustained half second of both, after the
    # ending's hardest hit, is where the room takes over from the band. It is found before the
    # last note rather than after it, because "the last note" is only definable as the last
    # musical attack the band plays *before* the room takes over — search it the other way and
    # the loudest clap in the applause wins.
    loudest = max(in_song, key=lambda one: one["rms_db_after"]) if in_song else None
    applause = None
    for index, one in enumerate(columns):
        if loudest and one["t"] <= loudest["t"]:
            continue
        window_of_three = columns[index : index + 3]
        if len(window_of_three) == 3 and all(
            two["flatness"] >= 0.004 and two["centroid_hz"] >= 900 for two in window_of_three
        ):
            applause = one
            break

    edge = applause["t"] + 0.5 if applause else WINDOW[1]
    musical = [one for one in in_song if one["voice"] == "musical" and one["t"] <= edge]
    last_note = musical[-1] if musical else None
    final_figure = [one for one in musical if last_note and one["t"] >= last_note["t"] - 8.0]
    crowd_after = [
        one
        for one in in_song
        if last_note and one["voice"] == "crowd" and one["t"] > last_note["t"]
    ]

    # --- the decay from the last note ------------------------------------------------
    anchor = last_note["t"] if last_note else WINDOW[1] - 10.0
    anchor_index = int(np.argmin(np.abs(times - anchor)))
    peak_db = float(rms_db[anchor_index : anchor_index + 12].max())
    crossings: dict[str, Any] = {}
    for drop in (6.0, 12.0, 20.0, 30.0, 40.0):
        for index in range(anchor_index, len(times)):
            if rms_db[index] > peak_db - drop:
                continue
            held = rms_db[index : index + int(0.5 / HOP)]
            if len(held) and float(held.max()) <= peak_db - drop + 1.0:
                crossings[f"minus_{int(drop)}_db"] = timed(float(times[index]))
                break

    # Sustained, not a gap between two claps: at 10 ms the applause bed touches the floor
    # between hands all through the ovation, and the first touch is not the end of anything.
    reaches_floor = None
    for index in range(anchor_index, len(times) - int(1.0 / HOP)):
        if float(rms_db[index : index + int(1.0 / HOP)].max()) <= floor_db + 3.0:
            reaches_floor = timed(float(times[index]))
            break

    # The release before the last note: how the band's held sound decays out of the tune, and
    # the quietest point it reaches before the final hit lands.
    release = (times >= anchor - 6.0) & (times < anchor - 0.3)
    release_slope = float(np.polyfit(times[release] - (anchor - 6.0), rms_db[release], 1)[0])
    dip_index = int(np.argmin(np.where(release, rms_db, 300.0)))

    return {
        "measured_from": str(MIX),
        "method": (
            "ffmpeg -> 48 kHz mono float; 30 ms RMS at 10 ms hop. An attack is a >=8 dB step "
            "over the median of the 80-250 ms before it, landing at least 18 dB over the "
            "floor. Spectral flatness (geometric/arithmetic mean power) and centroid separate "
            f"tonal music from broadband applause. Floor is the median RMS of {FLOOR[0]}-"
            f"{FLOOR[1]} s, after the applause has died — the outro's own room tone."
        ),
        "probe_span": list(PROBE),
        "room_tone_floor_db": round(floor_db, 2),
        "last_note": last_note,
        "final_figure": final_figure,
        "crowd_attacks_after_last_note": crowd_after[:8],
        "loudest_attack": loudest,
        "release_before_last_note": {
            "slope_db_per_s_over_6s": round(release_slope, 2),
            "quietest_point": at(dip_index),
        },
        "peak_at_last_note_db": round(peak_db, 2),
        "crossings_below_last_note": crossings,
        "reaches_room_tone_plus_3db": reaches_floor,
        "applause_takes_over": applause,
        "half_second_columns": columns,
    }


def deliverable_tail() -> dict[str, Any]:
    """What the released Taurus cut does at its own end, in this window's clock.

    Straight out of openings_survey.json (measured off the delivered mp4), re-quoted in mix
    seconds and SYNC frames so a builder does not have to convert it by hand. The tail of the
    deliverable IS the tail of this window — the file ends at mix 4066.15.
    """
    survey = json.loads((RECON / "openings_survey.json").read_text(encoding="utf-8"))
    tail = survey["summary"]["taurus_people"]["tail"]
    duration = float(survey["summary"]["taurus_people"]["duration_s"])
    marks = {
        "leaves_full_picture": tail["leaves_full_picture_at_s"],
        "dissolve_start": tail["fade_to_black_start_s"],
        "black_from": tail["black_from_s"],
        "file_end": duration,
    }
    return {
        "source": "gauntlet/recon/openings_survey.json (summary.taurus_people.tail)",
        "kind": tail["kind"],
        "dissolve_len_s": tail["fade_to_black_len_s"],
        "black_tail_len_s": tail["black_tail_len_s"],
        "black_tail_frames": round(float(tail["black_tail_len_s"]) * FPS, 1),
        "body_yavg": tail["body_yavg"],
        "digital_silence_from_s": tail["digital_silence_from_s"],
        "rms_db_at_end_minus_8s": tail["rms_db_at_end_minus_8s"],
        "marks": {
            name: (
                {
                    "deliverable_t": round(float(value), 3),
                    "t": round(float(value) + DELIVERABLE_ZERO, 3),
                    "frame": frame(float(value) + DELIVERABLE_ZERO),
                    "window_t": round(float(value) + DELIVERABLE_ZERO - WINDOW[0], 3),
                }
                if value is not None
                else None
            )
            for name, value in marks.items()
        },
    }


def beat_shape() -> dict[str, Any]:
    """The pulse inside the window: where it stops, and whether it slows before it does."""
    raw = json.loads(BEATS.read_text(encoding="utf-8"))
    rows = [one for one in raw["beats"] if inside(one["t"])]
    times = [float(one["t"]) for one in rows]
    gaps = [round(second - first, 3) for first, second in zip(times, times[1:], strict=False)]

    last = times[-1] if times else None
    after = [float(one["t"]) for one in raw["beats"] if float(one["t"]) > WINDOW[1]]
    tail_gap = round(after[0] - last, 2) if last is not None and after else None

    # Ritardando reads as the last dozen gaps growing. Report the trend rather than a verdict:
    # this grid is a 214 bpm sub-beat reading (beat_trust.json: meter 1, 72 % steady), so a
    # slowing here is evidence, not proof.
    tail_gaps = gaps[-12:]
    trend = None
    if len(tail_gaps) >= 6:
        half = len(tail_gaps) // 2
        first_half = sum(tail_gaps[:half]) / half
        second_half = sum(tail_gaps[half:]) / (len(tail_gaps) - half)
        trend = {
            "first_half_mean_gap_s": round(first_half, 3),
            "second_half_mean_gap_s": round(second_half, 3),
            "ratio": round(second_half / first_half, 2) if first_half else None,
        }
    return {
        "beats_in_window": len(rows),
        "downbeats_in_window": sum(1 for one in rows if one.get("downbeat")),
        "first_beat": timed(times[0]) if times else None,
        "last_beat": timed(last),
        "seconds_of_window_after_last_beat": (
            round(WINDOW[1] - last, 2) if last is not None else None
        ),
        "gap_to_next_beat_after_window_s": tail_gap,
        "median_gap_s": round(float(np.median(gaps)), 3) if gaps else None,
        "last_12_gaps_s": tail_gaps,
        "tail_gap_trend": trend,
        "note": (
            "beat_trust.json records a 37.64 s hole in the grid opening at 4047.34 s — the "
            "largest anywhere after the two set breaks. That is the detector losing the pulse, "
            "which for an ending is the reading that matters."
        ),
    }


def main() -> None:
    found = paths_from_run()
    solos_header, solos_rows = document(found["solos"], "solos")
    fills_header, fills_rows = document(found["fills"], "fills")
    phrases_header, phrases_rows = document(found["phrases"], "phrases")
    energy_raw = json.loads(ENERGY.read_text(encoding="utf-8"))

    changes = []
    for row in solos_rows:
        if not (inside(row.get("t")) or inside(row.get("measured_t"))):
            continue
        changes.append(
            {
                "kind": "solo_change",
                "change": row.get("change"),
                **timed(row["t"]),  # type: ignore[dict-item]
                "measured": timed(row.get("measured_t")),
                "downbeat": row.get("downbeat"),
                "signal": row.get("signal"),
                "from": row.get("from"),
                "to": row.get("to"),
                "detail": row.get("detail"),
            }
        )
    before = [row for row in solos_rows if float(row["t"]) < WINDOW[0]]
    front_at_open = (
        {
            "since": timed(before[-1]["t"]),
            "stem": before[-1].get("to"),
            "signal": before[-1].get("signal"),
        }
        if before
        else None
    )

    fills = []
    for row in fills_rows:
        if not inside(row.get("start")):
            continue
        fills.append(
            {
                "kind": "drum_fill",
                **timed(row["start"]),  # type: ignore[dict-item]
                "end": timed(row.get("end")),
                "resolves_into_bar": row.get("resolves_into_bar"),
                "duration": row.get("duration"),
                "bar": row.get("bar"),
                "beat": row.get("beat"),
                "in_bar": row.get("in_bar"),
                "hits": row.get("hits"),
                "density_ratio": row.get("density_ratio"),
                "confidence": row.get("confidence"),
                "counts": {
                    name: row.get(name)
                    for name in ("kick", "snare", "toms", "ride", "crash")
                    if name in row
                },
                "factors": row.get("factors"),
            }
        )

    boundaries = []
    for row in phrases_rows:
        if not (inside(row.get("t")) or inside(row.get("measured_t"))):
            continue
        boundaries.append(
            {
                "kind": "phrase_boundary",
                **timed(row["t"]),  # type: ignore[dict-item]
                "measured": timed(row.get("measured_t")),
                "resumes": timed(row.get("resumes_t")),
                "snapped": row.get("snapped"),
                "downbeat": row.get("downbeat"),
                "bar": row.get("bar"),
                "in_bar": row.get("in_bar"),
                "rest_seconds": row.get("rest_seconds"),
                "held_ratio": row.get("held_ratio"),
                "interval_semitones": row.get("interval_semitones"),
                "confidence": row.get("confidence"),
                "factors": row.get("factors"),
            }
        )

    rows = list(energy_raw["energy"])
    peaks = energy_peaks(rows)
    in_window = [r for r in rows if inside(r["t"])]
    loudest = max(in_window, key=lambda one: one["lufs"]) if in_window else None
    quietest = min(in_window, key=lambda one: one["lufs"]) if in_window else None

    ranked_input = [
        *changes,
        *fills,
        *boundaries,
        *[
            {
                "kind": "energy_peak",
                **{k: v for k, v in one.items() if k != "prominence_db"},
                "prominence_db": one["prominence_db"],
                "confidence": min(one["prominence_db"] / 6.0, 1.0),
            }
            for one in peaks
        ],
    ]
    ranked = rank(ranked_input)
    shortlist = spaced(ranked)

    rest = {
        "solo_changes": sum(1 for row in solos_rows if inside(row.get("t"), SPAN)),
        "drum_fills": sum(1 for row in fills_rows if inside(row.get("start"), SPAN)),
        "phrase_boundaries": sum(1 for row in phrases_rows if inside(row.get("t"), SPAN)),
    }

    report = {
        "kind": "taurus_ending_events",
        "song": "Taurus People",
        "window": {
            "label": "ending piece — last 90 s of the set's last song",
            "mix_seconds": list(WINDOW),
            "frames": [frame(WINDOW[0]), frame(WINDOW[1])],
            "deliverable_seconds": [
                round(WINDOW[0] - DELIVERABLE_ZERO, 3),
                round(WINDOW[1] - DELIVERABLE_ZERO, 3),
            ],
            "seconds": round(WINDOW[1] - WINDOW[0], 3),
            "after_the_window": (
                "mix 4066.15 s is the end of the last song of the set; everything after it is "
                "outro room tone and applause, not music."
            ),
        },
        "song_span": {"mix_seconds": list(SPAN), "frames": [frame(SPAN[0]), frame(SPAN[1])]},
        "frame_mapping": {
            "formula": "frame = 86401 + round(seconds * 23.976)",
            "fps": FPS,
            "frame_at_mix_zero": FRAME_ZERO,
            "deliverable_zero_mix_s": DELIVERABLE_ZERO,
        },
        "sources": {name: str(path) for name, path in found.items()}
        | {"energy": str(ENERGY), "beats": str(BEATS), "mix": str(MIX)},
        "reading": {
            "voices": solos_header.get("voices"),
            "timbre_stem": solos_header.get("timbre_stem"),
            "phrase_stem": phrases_header.get("stem"),
            "fill_stems": fills_header.get("stems"),
        },
        "counts": {
            "solo_changes": len(changes),
            "drum_fills": len(fills),
            "phrase_boundaries": len(boundaries),
            "energy_peaks": len(peaks),
            "total": len(ranked_input),
        },
        "counts_whole_song": rest,
        "reading_notes": [
            "The last drum_fill row (t 4047.34) carries duration 37.64 s and 1370 hits. That is"
            " not a fill: it is the same hole beat_trust.json records in the grid, read by the"
            " fill detector as one enormous run. Treat it as 'the pulse stops here', not as a"
            " cue to cut on.",
            "The solo_change at t 4065.0 reads 'other -> vocals, lead, +8.33 dB'. There is no"
            " singer in this band. At 4065 the room is applauding and the vocals stem is the"
            " audience, so the change is real and its label is not.",
            "The only other solo_change in the window (4043.12) is a timbre step inside the"
            " same stem, which BASE weights at 0.60 for exactly this reason.",
            "phrase_boundary rows stop at 4055.79 and resume at 4084.98 — nothing between the"
            " end of the last phrase and the outro, which is the free coda and the applause.",
        ],
        "front_at_window_open": front_at_open,
        "solo_changes": changes,
        "drum_fills": fills,
        "phrase_boundaries": boundaries,
        "energy": {
            "window_seconds": energy_raw.get("window_seconds"),
            "hop_seconds": energy_raw.get("hop_seconds"),
            "concert_integrated_lufs": energy_raw.get("integrated_lufs"),
            "loudest_in_window": (
                {**timed(loudest["t"]), "lufs": loudest["lufs"]} if loudest else None
            ),
            "quietest_in_window": (
                {**timed(quietest["t"]), "lufs": quietest["lufs"]} if quietest else None
            ),
            "curve_last_40s": [
                {
                    "t": one["t"],
                    "window_t": round(one["t"] - WINDOW[0], 2),
                    "frame": frame(one["t"]),
                    "lufs": one["lufs"],
                    "rms_dbfs": one["rms_dbfs"],
                    "onsets_per_second": one["onsets_per_second"],
                }
                for one in rows
                if WINDOW[1] - 40.0 <= one["t"] <= WINDOW[1] + 20.0
            ],
            "peaks": peaks,
        },
        "beats": beat_shape(),
        "tail": tail_shape(),
        "deliverable_tail": deliverable_tail(),
        "shortlist": shortlist,
        "ranked": ranked,
        "ranking_rule": (
            "score = base + 0.20 per other event within 0.6 s (max 0.4) + 0.15 when the "
            "detector snapped it to a downbeat; base is the detector's confidence, "
            "prominence_db / 6 for an energy peak, and BASE[kind:signal] for a solo change. "
            "Identical to taurus_events.py so the two windows rank on one scale."
        ),
    }
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["counts"], indent=2), flush=True)
    tail = report["tail"]
    print("floor_db", tail["room_tone_floor_db"], flush=True)
    print("last_note", json.dumps(tail["last_note"]), flush=True)
    print("final_figure", json.dumps(tail["final_figure"]), flush=True)
    print("release", json.dumps(tail["release_before_last_note"]), flush=True)
    print("crossings", json.dumps(tail["crossings_below_last_note"]), flush=True)
    print("applause", json.dumps(tail["applause_takes_over"]), flush=True)
    print("floor+3", json.dumps(tail["reaches_room_tone_plus_3db"]), flush=True)
    for one in tail["half_second_columns"]:
        if not (4053.0 <= one["t"] <= 4070.0):
            continue
        print(
            f"  t={one['t']:7.2f} win={one['window_t']:6.2f} rms={one['rms_db']:7.2f} "
            f"flat={one['flatness']:.4f} cen={one['centroid_hz']:5d} "
            f"above_floor={one['above_floor_db']:6.2f}",
            flush=True,
        )
    for one in shortlist[:8]:
        print(
            one["kind"], one["t"], one["frame"], one["score"],
            one.get("signal") or one.get("confidence"), one.get("agrees_with"), flush=True,
        )


if __name__ == "__main__":
    main()
