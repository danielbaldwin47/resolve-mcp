"""Round-2 digest: everything the P3 R2 plan needs, window-relative, in one printout.

Reads the round-1 recon files (events, human cuts, occlusion, camera motion) and an
RMS accent curve computed from the master mix, and prints them relative to the window
open (mix 3735.16 s / Zinc SYNC 175955). READ-ONLY apart from r2_accents.json.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
EV = json.loads((HERE / "taurus_mid_events.json").read_text(encoding="utf-8"))
HUM = json.loads((HERE / "mid_human_cuts.json").read_text(encoding="utf-8"))
RECON = json.loads((HERE / "mid_p3_recon.json").read_text(encoding="utf-8"))
ACC_OUT = HERE / "r2_accents.json"
MIX = r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"

FPS = 24000.0 / 1001.0
T0 = 3735.16
SPAN = 90.0


def rel(t: float) -> float:
    return round(t - T0, 2)


def accents() -> list[dict]:
    """RMS accents: 100 ms-hop short-term RMS, peaks that rise >=2 dB over 0.6 s."""
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{T0 - 2.0:.3f}", "-t", f"{SPAN + 4.0:.3f}",
        "-i", MIX, "-ac", "1", "-ar", "8000", "-f", "s16le", "-",
    ]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    import array
    import math

    pcm = array.array("h")
    pcm.frombytes(raw[: len(raw) // 2 * 2])
    hop, win = 800, 2400  # 100 ms hop, 300 ms window at 8 kHz
    db: list[float] = []
    times: list[float] = []
    for i in range(0, len(pcm) - win, hop):
        seg = pcm[i:i + win]
        s = sum(float(v) * float(v) for v in seg) / len(seg)
        db.append(10.0 * math.log10(s + 1e-9))
        times.append(-2.0 + i / 8000.0 + win / 16000.0)
    out: list[dict] = []
    for i in range(6, len(db) - 6):
        loc = db[i]
        if loc < max(db[i - 6:i + 7]) - 1e-9:
            continue
        floor = min(db[max(0, i - 12):i + 1])
        prom = loc - floor
        if prom < 2.0:
            continue
        t = times[i]
        if not (-0.5 <= t <= SPAN + 0.5):
            continue
        if out and t - out[-1]["t"] < 0.45:
            if prom <= out[-1]["prom"]:
                continue
            out.pop()
        out.append({"t": round(t, 2), "db": round(loc, 1), "prom": round(prom, 1)})
    return out


def main() -> None:
    print("=== HUMAN (this window) ===")
    print("counts", json.dumps(HUM["counts"]))
    print("shot_length", json.dumps(HUM["shot_length"]))
    print("histogram", json.dumps(HUM["shot_length_histogram"]))
    print("shots:", [s["seconds"] for s in HUM["shots"]])
    print("cuts rel:", [rel(c["t"]) for c in HUM["cuts"]])
    print("align", json.dumps({k: v for k, v in HUM["alignment_to_events"].items()
                               if k != "cuts"}))

    print("\n=== FRONT SEGMENTS ===")
    for f in EV["front_segments"]:
        print(f"  {f['stem']:>7} since {rel(f['since']['t']):7.2f} until "
              f"{rel(f['until']['t']) if f.get('until') else None}")
    print("solo_changes:", [(rel(s["t"]), s["from"], s["to"]) for s in EV["solo_changes"]])

    print("\n=== ENERGY ===")
    e = EV["energy"]
    print("loudest", json.dumps(e["loudest_in_window"]), "quietest",
          json.dumps(e["quietest_in_window"]), "range", e["range_db"])
    print("peaks:", [(rel(p["t"]), p.get("lufs"), p.get("prominence_db")) for p in e["peaks"]])
    print("legs:")
    for lg in EV["shape"]["legs"]:
        print("   ", json.dumps({k: (rel(v) if k in ("t_start", "t_end", "start", "end")
                                     and isinstance(v, (int, float)) else v)
                                 for k, v in lg.items()}))

    print("\n=== FILLS (rel t, dur, hits, conf) ===")
    for f in EV["drum_fills"]:
        print(f"   {rel(f['t']):7.2f} dur {f['duration']:5.2f} hits {f['hits']:4d} "
              f"conf {f['confidence']:.3f}")

    print("\n=== PHRASE BOUNDARIES (rel t, rest_s, conf) ===")
    for p in EV["phrase_boundaries"]:
        print(f"   {rel(p['t']):7.2f} rest {p.get('rest_seconds')} conf {p.get('confidence')}")

    print("\n=== RANKED TOP 25 ===")
    for r in EV["ranked"][:25]:
        print(f"   {rel(r['t']):7.2f} {r['kind']:<16} score {r['score']:.2f} "
              f"prom {r.get('prominence_db')} ons/s {r.get('onsets_per_second')}")

    print("\n=== FX6 MOVE RUNS ===")
    for r in RECON["FX6"]["move_runs"]:
        print(f"   {r['start']:7.2f} -> {r['end']:7.2f} ({r['seconds']:5.2f}s) "
              f"dx {r['net_dx']:+7.1f} dy {r['net_dy']:+6.1f}")
    print("A7IV move runs:")
    for r in RECON["A7IV"]["move_runs"]:
        print(f"   {r['start']:7.2f} -> {r['end']:7.2f} ({r['seconds']:5.2f}s) "
              f"dx {r['net_dx']:+7.1f} dy {r['net_dy']:+6.1f}")

    acc = accents()
    ACC_OUT.write_text(json.dumps({"t0": T0, "accents": acc}, indent=1), encoding="utf-8")
    print(f"\n=== RMS ACCENTS ({len(acc)}) ===")
    print(" ".join(f"{a['t']:.2f}/{a['prom']:.1f}" for a in acc))


if __name__ == "__main__":
    main()
