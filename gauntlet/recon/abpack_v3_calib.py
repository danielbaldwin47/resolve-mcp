"""Calibrate the pack v3 passes against the two ground-truth ending clips.

Extracts each span once into a cache dir (540p, exactly as ab_pack does), then
runs the audio class track and the slow-transition pass on it and prints the
numbers the thresholds are chosen from. Ground truth (G16):

    human tail  -> dissolve_to_black, ~5.9 s, black at ~89.8 s;
                   music -> applause at ~83.4 s -> applause to the end
    ours (p2r1) -> hard_to_black at ~83.46 s

Usage: uv run python gauntlet/recon/abpack_v3_calib.py [--audio-dump]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gauntlet" / "tools"))

import ab_pack  # noqa: E402

CACHE = ROOT / "gauntlet" / "recon" / "v3_calib_clips"
HUMAN = Path(
    r"S:/Deliverables/Ryan Devlin/6-17-26 Zinc Bar/Full Videos"
    r"/6-17 - Zinc Set 2 - Taurus People.mp4"
)
HUMAN_SPAN = (407.66, 497.66)
OURS = ROOT / "gauntlet" / "renders" / "taurus_ending_p2r1.mp4"
OPENING_SPAN = (0.0, 90.0)


def clip_for(name: str, src: Path, span: tuple[float, float] | None) -> tuple[Path, float]:
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"{name}.mp4"
    if dest.exists():
        return dest, ab_pack.probe_duration(dest)
    return dest, ab_pack.extract_span(src, span, dest)


def report(name: str, clip: Path, duration: float, dump: bool, luma_dump: bool) -> None:
    print(f"\n=== {name}  ({duration:.2f} s) ===")
    samples = ab_pack.decode_mono(clip)
    feats = ab_pack.audio_features(samples)
    track, thresholds = ab_pack.classify_audio(feats)
    spans = ab_pack.audio_class_spans(track, samples)
    print("audio thresholds:", thresholds)
    print("audio spans:", [(s["class"], s["start"], s["end"]) for s in spans])
    print("audio summary:", ab_pack.audio_class_summary(track, spans))
    if dump:
        for row in track:
            print(
                f"  {row['t']:7.2f} {row['rms_db']:8.2f} {row['flatness']:.5f} "
                f"{row['centroid_hz']:8.1f} {row['hf_share']:.4f}  {row['class']}"
            )

    cuts = ab_pack.detect_cuts(clip)
    shots = ab_pack.shots_from_cuts(cuts, duration)
    fps = ab_pack.probe_fps(clip)
    transitions = [ab_pack.classify_transition(clip, c, fps, duration) for c in cuts]
    slow = ab_pack.luma_track(clip)
    if luma_dump:
        t, luma, hf = slow["t"], slow["luma"], slow["hf"]
        for i in range(len(t)):
            if t[i] >= duration - 22.0:
                print(f"  luma {t[i]:7.2f} {luma[i]:7.2f} {hf[i]:7.2f}")
    events, ending = ab_pack.slow_transition_scan(slow, shots, transitions, duration)
    ghosts = ab_pack.ghost_scan(slow, shots, exclude=ab_pack.ending_ramp(ending))
    print(f"cuts: {len(cuts)}  last cut {cuts[-1] if cuts else None}")
    print("ending:", ending)
    print("slow events:", events)
    print("ghosting:", ghosts)


def synth_clip() -> tuple[Path, float]:
    """A clip whose transitions are known exactly: 3 s cross-dissolve, 5.9 s fade.

    testsrc2 for 8 s, xfade into smptebars at t=5 over 3 s, then fade to black
    from t=13 to t=18.9. The pack should see a ghosting event inside the
    cross-dissolve and end on dissolve_to_black of ~5.9 s.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / "synth.mp4"
    if not dest.exists():
        ab_pack.run(
            [
                "ffmpeg", "-hide_banner", "-y",
                "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=8",
                "-f", "lavfi", "-i", "smptebars=s=640x360:r=25:d=14",
                "-filter_complex",
                "[0][1]xfade=transition=fade:duration=3:offset=5,"
                "fade=t=out:st=13:d=5.9,scale=-2:360[v]",
                "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", str(dest),
            ]
        )
    return dest, ab_pack.probe_duration(dest)


def report_synth() -> None:
    clip, duration = synth_clip()
    print(f"\n=== synthetic (xfade 5-8 s, fade out 13-18.9 s)  ({duration:.2f} s) ===")
    cuts = ab_pack.detect_cuts(clip)
    shots = ab_pack.shots_from_cuts(cuts, duration)
    fps = ab_pack.probe_fps(clip)
    transitions = [ab_pack.classify_transition(clip, c, fps, duration) for c in cuts]
    slow = ab_pack.luma_track(clip)
    events, ending = ab_pack.slow_transition_scan(slow, shots, transitions, duration)
    print("cuts:", cuts)
    print("v2 transitions:", [t.get("type") for t in transitions])
    print("ending:", ending)
    print("slow events:", events)
    print("ghosting:", ab_pack.ghost_scan(slow, shots, exclude=ab_pack.ending_ramp(ending)))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio-dump", action="store_true")
    parser.add_argument("--luma-dump", action="store_true")
    args = parser.parse_args()

    report_synth()
    human, human_dur = clip_for("human_ending", HUMAN, HUMAN_SPAN)
    ours, ours_dur = clip_for("ours_ending", OURS, None)
    human_open, open_dur = clip_for("human_opening", HUMAN, OPENING_SPAN)
    report("human ending 407.66-497.66", human, human_dur, args.audio_dump, args.luma_dump)
    report("ours taurus_ending_p2r1", ours, ours_dur, args.audio_dump, args.luma_dump)
    report("human opening 0-90", human_open, open_dur, args.audio_dump, args.luma_dump)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
