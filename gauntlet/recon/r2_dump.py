"""Round-2 recon: the decisive rows for planning the ending recut."""

import json
from pathlib import Path

HERE = Path(__file__).parent
d = json.loads((HERE / "taurus_ending_events.json").read_text())

print("### reading_notes")
for n in d["reading_notes"]:
    print("-", n)

print()
print("### window / mapping")
print(json.dumps(d["window"], indent=1))
print(json.dumps(d["frame_mapping"], indent=1))

print()
print("### energy peaks (all 11)")
for p in d["energy"]["peaks"]:
    print(
        f"  win_t={p['window_t']:7.2f} f={p['frame']} lufs={p['lufs']} "
        f"rms={p['rms_dbfs']} ops={p['onsets_per_second']} prom={p['prominence_db']}"
    )

print()
print("### shortlist (8)")
for p in d["shortlist"]:
    print(
        f"  {p['kind']:18s} win_t={p['window_t']:7.2f} f={p['frame']} "
        f"score={p['score']} agrees={p.get('agrees_with')}"
    )

print()
print("### ranked top 40")
for p in d["ranked"][:40]:
    print(
        f"  {p['kind']:18s} win_t={p['window_t']:7.2f} f={p['frame']} "
        f"score={round(p['score'], 3)} conf={p.get('confidence')} agrees={p.get('agrees_with')}"
    )

print()
print("### drum fills (22)")
for p in d["drum_fills"]:
    print(
        f"  win_t={p['window_t']:7.2f} f={p['frame']} end_win_t={p['end']['window_t']:7.2f} "
        f"dur={p['duration']} hits={p['hits']} conf={p['confidence']} dens={p['density_ratio']}"
    )

print()
print("### solo changes")
for p in d["solo_changes"]:
    print(json.dumps(p))

print()
print("### phrase boundaries, win_t >= 55")
for p in d["phrase_boundaries"]:
    if p["window_t"] >= 55:
        print(
            f"  win_t={p['window_t']:7.2f} f={p['frame']} rest={p.get('rest_seconds')} "
            f"conf={p.get('confidence')} downbeat={p.get('downbeat')}"
        )

print()
print("### phrase boundaries, win_t < 55 (conf sorted top 20)")
lo = [p for p in d["phrase_boundaries"] if p["window_t"] < 55]
for p in sorted(lo, key=lambda x: -x.get("confidence", 0))[:20]:
    print(
        f"  win_t={p['window_t']:7.2f} f={p['frame']} rest={p.get('rest_seconds')} "
        f"conf={p.get('confidence')} downbeat={p.get('downbeat')}"
    )

print()
print("### beats block")
b = dict(d["beats"])
b.pop("beat_times", None)
print(json.dumps(b, indent=1)[:1500])

print()
print("### tail: last_note / final_figure / crowd / release")
t = d["tail"]
print("last_note:", json.dumps(t["last_note"]))
for f in t["final_figure"]:
    print("final_figure:", json.dumps(f))
for f in t["crowd_attacks_after_last_note"]:
    print("crowd:", json.dumps(f))
print("release:", json.dumps(t["release_before_last_note"]))
print("applause_takes_over:", json.dumps(t["applause_takes_over"]))
print("crossings:", json.dumps(t["crossings_below_last_note"]))
print("room_tone_floor_db:", t["room_tone_floor_db"])
print("method:", t["method"])

print()
print("### tail half_second_columns, win_t 60..92")
for c in t["half_second_columns"]:
    if 60 <= c["window_t"] <= 92:
        print(
            f"  win_t={c['window_t']:6.2f} f={c['frame']} rms={c['rms_db']:7.2f} "
            f"peak={c['peak_db']:7.2f} flat={c['flatness']} centroid={c['centroid_hz']} hf={c['hf_share']}"
        )

print()
print("### energy curve last 40 s (win_t >= 50)")
for c in d["energy"]["curve_last_40s"]:
    print(
        f"  win_t={c['window_t']:6.2f} f={c['frame']} lufs={c['lufs']:7.2f} "
        f"rms={c['rms_dbfs']:7.2f} ops={c['onsets_per_second']}"
    )

print()
print("### deliverable_tail")
print(json.dumps(d["deliverable_tail"], indent=1))
