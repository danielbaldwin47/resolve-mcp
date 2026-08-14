"""Third pass: fold the measurements into the per-song summary the style doc cites.

Writes the final `gauntlet/recon/openings_survey.json`: a `summary` block that
holds card in/out, first-note time and tail treatment per song, over the raw
curves the numbers came from.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

RESULT = Path(__file__).resolve().parent / "openings_survey.json"
BLACK = 66.0

# Onset times read off a 20 ms-window RMS sweep of each head (see
# gauntlet/recon/taurus_note.scratch.log for the Taurus sweep); the four
# picture-first tunes are already sounding inside the fade-up.
FIRST_NOTE_S = {
    "hardest_part": 1.15,
    "maitland_boulevard": 0.65,
    "sambra": 0.25,
    "soultrane": 0.45,
    "taurus_people": 2.38,
}

# Soultrane's personnel super sits far outside the 60 s scan window; measured
# separately (gauntlet/recon/soultrane_text.scratch.log).
EXTRA_SUPERS = {
    "soultrane": [
        {
            "fade_in_start": 69.945,
            "full_in": 69.945,
            "full_out": 77.869,
            "fade_out_end": 77.869,
        }
    ],
}


def fade_up(yavg: list[dict[str, float]]) -> dict[str, object]:
    """Black-to-picture ramp at the head."""
    head = [p for p in yavg if p["t"] <= 4.0]
    peak = max(p["yavg"] for p in head)
    full = next((p["t"] for p in head if p["yavg"] >= 0.98 * peak), None)
    # a full-frame card is a run, starting at frame 1, held within 2 codes and
    # far below the picture level
    v1 = yavg[1]["yavg"]
    end = yavg[1]["t"]
    for p in yavg[1:]:
        if abs(p["yavg"] - v1) <= 2.0:
            end = p["t"]
        else:
            break
    plateau_len = end - yavg[1]["t"]
    return {
        "first_frame_yavg": yavg[0]["yavg"],
        "picture_yavg": round(peak, 1),
        "reaches_picture_at_s": round(full, 3) if full is not None else None,
        "card_plateau_yavg": round(v1, 2),
        "card_plateau_end_s": round(end, 3),
        "card_plateau_len_s": round(plateau_len, 3),
        "is_full_frame_card": plateau_len >= 0.5 and v1 < 0.7 * peak,
    }


def tail_shape(yavg: list[dict[str, float]], dur: float) -> dict[str, object]:
    body = statistics.median([p["yavg"] for p in yavg if p["t"] <= dur - 10.0])
    # start of the departure: last frame still at full picture level
    depart = None
    for p in yavg:
        if p["yavg"] >= 0.97 * body:
            depart = p["t"]
    black_start = None
    for p in reversed(yavg):
        if p["yavg"] <= BLACK:
            black_start = p["t"]
        else:
            break
    # hard cut vs dissolve: a cut drops to black inside one frame
    kind = "unknown"
    if black_start is not None:
        idx = next(i for i, p in enumerate(yavg) if p["t"] >= black_start)
        prev = yavg[idx - 1]["yavg"] if idx else None
        kind = "hard_cut_to_black" if prev and prev > 0.8 * body else "dissolve_to_black"
    # dissolve length: last frame still at 90 % of the picture level a dozen
    # seconds earlier, through to the first black frame. Meaningless for a hard
    # cut, so it is only reported for a dissolve.
    fade_start = None
    if black_start is not None and kind == "dissolve_to_black":
        ref_pts = [p["yavg"] for p in yavg if black_start - 13 <= p["t"] <= black_start - 11]
        if ref_pts:
            ref = statistics.median(ref_pts)
            over = [p["t"] for p in yavg if p["t"] <= black_start and p["yavg"] >= 0.90 * ref]
            fade_start = max(over) if over else None
    return {
        "body_yavg": round(body, 1),
        "fade_to_black_start_s": round(fade_start, 3) if fade_start is not None else None,
        "fade_to_black_len_s": (
            round(black_start - fade_start, 3)
            if fade_start is not None and black_start is not None
            else None
        ),
        "leaves_full_picture_at_s": round(depart, 3) if depart else None,
        "leaves_full_picture_before_end_s": round(dur - depart, 2) if depart else None,
        "black_from_s": round(black_start, 3) if black_start is not None else None,
        "black_tail_len_s": round(dur - black_start, 3) if black_start is not None else 0.0,
        "kind": kind,
    }


def audio_tail(rms: list[dict[str, float]], dur: float) -> dict[str, object]:
    silent = None
    for p in reversed(rms):
        if p["rms_db"] <= -95.0:
            silent = p["t"]
        else:
            break
    return {
        "digital_silence_from_s": round(silent, 3) if silent is not None else None,
        "silence_before_end_s": round(dur - silent, 2) if silent is not None else None,
        "rms_db_at_end_minus_8s": next(
            (round(p["rms_db"], 1) for p in rms if p["t"] >= dur - 8.0), None
        ),
    }


def main() -> None:
    doc = json.loads(RESULT.read_text(encoding="utf-8"))
    if doc.get("curves_thinned"):
        # This pass decimates the curves it reads, so a second run would compute
        # the summary from thinned data and silently move the numbers. Re-measure
        # instead: openings_survey.py sheets, luma, tailluma, text.
        raise SystemExit(
            "openings_survey.json already holds thinned curves - re-run the "
            "openings_survey.py measure passes before summarising again."
        )
    summary: dict[str, object] = {}
    for key, v in doc["songs"].items():
        dur = float(v["probe"]["duration_s"])
        up = fade_up(v["head_yavg"])
        supers = list(v.get("supers", [])) + EXTRA_SUPERS.get(key, [])
        supers.sort(key=lambda s: s["full_in"])
        card = supers[0] if supers else None
        is_card = bool(up["is_full_frame_card"])
        entry = {
            "file": v["file"],
            "duration_s": dur,
            "fps": v["probe"]["fps"],
            "head": {
                "first_frame_is_black": up["first_frame_yavg"] <= BLACK,
                "device": "full_frame_title_card" if is_card else "dissolve_up_from_black",
                "black_to_picture_s": (
                    round(float(up["reaches_picture_at_s"]), 3)
                    if up["reaches_picture_at_s"] is not None
                    else None
                ),
                "first_note_s": FIRST_NOTE_S[key],
                "luma": up,
            },
            "title": None,
            "personnel_super": None,
            "tail": tail_shape(v["tail_yavg"], dur) | audio_tail(v["tail_rms_0p1s"], dur),
        }
        if is_card:
            # the card is the picture itself: in at frame 1, out when the cut lands
            out = float(up["reaches_picture_at_s"] or 0.0)
            entry["title"] = {
                "form": "full_frame_card_over_black",
                "in_s": round(v["head_yavg"][1]["t"], 3),
                "out_s": round(out, 3),
                "hold_s": round(out - v["head_yavg"][1]["t"], 3),
                "out_minus_first_note_s": round(out - FIRST_NOTE_S[key], 3),
                "clears_by": "hard cut to picture",
            }
            if len(supers) >= 1:
                entry["personnel_super"] = supers[0]
        else:
            if card:
                entry["title"] = {
                    "form": "lower_third_super_over_picture",
                    "in_s": round(card["fade_in_start"], 3),
                    "out_s": round(card["fade_out_end"], 3),
                    "hold_s": round(card["fade_out_end"] - card["fade_in_start"], 3),
                    "in_minus_first_note_s": round(card["fade_in_start"] - FIRST_NOTE_S[key], 3),
                    "clears_by": "fade out (~0.3 s)",
                }
            if len(supers) >= 2:
                entry["personnel_super"] = supers[1]
        if entry["personnel_super"]:
            ps = entry["personnel_super"]
            ps["hold_s"] = round(ps["fade_out_end"] - ps["fade_in_start"], 3)
        summary[key] = entry
        print(json.dumps({key: entry}, indent=1))
    # thin the raw curves before committing: per-frame luma decimates to ~0.1 s,
    # the 60 s band curve to ~0.2 s over its first 30 s. The summary above is
    # computed from the full-rate data, and rerunning the earlier passes
    # regenerates it.
    for v in doc["songs"].values():
        for field, step in (
            ("head_yavg", 6),
            ("tail_yavg", 6),
            ("head_rms_0p1s", 2),
            ("tail_rms_0p1s", 2),
        ):
            if field in v:
                v[field] = v[field][::step]
        if "band_white_pct_60s" in v:
            v["band_white_pct_60s"] = [
                p for i, p in enumerate(v["band_white_pct_60s"]) if i % 12 == 0 and p["t"] <= 30.0
            ]
        v.pop("band_ymax_40s", None)
        v.pop("head_first_rise", None)
    doc["curves_thinned"] = True
    doc["summary"] = summary
    doc["measured"] = "2026-08-13"
    doc["source_dir"] = "S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos"
    doc["method"] = (
        "ffmpeg. Frame grabs at 0.5 s over the first and last 8 s (320 px) plus a "
        "0.5 s/40 s strip of the lower-third band, all read by eye. Numbers from "
        "per-frame signalstats YAVG (10-bit limited black = 64) for black/fade/card "
        "structure, from a near-white pixel share inside the lower-third band "
        "(crop 0.55w x 0.14h at 0.03w,0.76h -> gray -> threshold 200) for super "
        "in/out, and from astats RMS at 0.1 s (0.02 s at the Taurus entrance) for "
        "the music. Super spans are full-strength; the fades either side run ~0.3 s."
    )
    RESULT.write_text(json.dumps(doc, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
