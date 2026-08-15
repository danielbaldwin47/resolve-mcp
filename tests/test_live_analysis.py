"""The one analysis path no fake can stand in for: the real model, actually loaded.

ADR 0002 injects each model at a seam, which makes every decision around them testable and
leaves exactly one thing unverified per model — whether the default detector calls the
installed package correctly. Return arity, units and frame rates are the kind of thing that
is right or wrong on the first real run and nowhere before it, so they get live tests rather
than nothing, in the spirit of ADR 0001's interpreter guard.

Runs only under ``-m live``, and skips even there if the model is not installed — the point
is to be run on the machine that has it, and to say so on the ticket when it has not been.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from resolve_mcp.analysis import applause, bars, beats, device
from resolve_mcp.audio import separator, stems, wav
from resolve_mcp.config import Config
from resolve_mcp.jobs import cache

from .fakes import write_clicks, write_hits, write_sections

CLICK_SECONDS = 20.0
CLICK_BPM = 120.0

APPLAUSE_SECONDS = 8.0
ROOM_SECONDS = 6.0

BAR_METER = 4
BAR_SECONDS = 60.0 / CLICK_BPM * BAR_METER


@pytest.mark.live
def test_the_installed_torch_is_a_cuda_build_and_both_models_are_told(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#245's live half: the wheels the extra actually installed, and the device the models
    were actually handed.

    Red rather than skipped on a `+cpu` build, deliberately — same shape as the live
    separator test. The fake tier can prove which device string the site asks for; only a
    real install can say whether `uv sync --extra analysis` produced a torch that can
    honour it.
    """
    pytest.importorskip("torch", reason="the analysis extra is not installed")
    pytest.importorskip("beat_this.inference", reason="beat_this is not installed")
    pytest.importorskip(applause.MODULE, reason="panns_inference is not installed")

    note = device.torch_note()
    assert note is not None
    assert "+cu" in note["torch"], f"the analysis extra installed {note['torch']}, not a CUDA build"
    assert note["cuda_available"] is True
    assert note["device"] == "cuda"
    assert device.inference_device(note) == "cuda"

    path = write_clicks(tmp_path / "clicks.wav", beats_per_minute=CLICK_BPM, seconds=CLICK_SECONDS)
    with caplog.at_level("INFO"):
        beats.detect(path)
        applause.tag(path)
    announced = {
        one.message.split()[0] for one in caplog.records if "inference on CUDA" in one.message
    }
    # Both models, because they reach the device by different routes: beat_this had to be
    # told, and PANNs had to be stopped from choosing and falling back quietly.
    assert announced == {"beat_this", "PANNs"}


@pytest.mark.live
def test_the_installed_beat_model_hears_a_click_track(tmp_path: Path) -> None:
    pytest.importorskip("beat_this.inference", reason="beat_this is not installed")
    path = write_clicks(tmp_path / "clicks.wav", beats_per_minute=CLICK_BPM, seconds=CLICK_SECONDS)

    grid = beats.detect(path)
    found = beats.gist(grid, beats.rows(grid))

    # Times in seconds, not frames or samples: the unit is what a wrong call gets wrong.
    assert all(0.0 <= one <= CLICK_SECONDS for one in grid.beats)
    assert found["tempo_bpm"] == pytest.approx(CLICK_BPM, abs=10.0)
    # Downbeats are the second of the two lists the model returns, and a subset of the first.
    assert grid.downbeats
    assert set(grid.downbeats) <= set(grid.beats)


@pytest.mark.live
def test_a_bar_map_over_the_real_model_and_the_real_accents_finds_the_downbeat(
    tmp_path: Path,
) -> None:
    """The two seams the fake tier cannot join: the installed model, and RMS off real audio.

    Every decision in ``bars`` is checked against grids and readings written by hand. What
    that cannot show is whether the accent reading, run over a file the *model* also read,
    ranks the same beats the arithmetic was tested on — a window off by a beat, or a decoder
    handing back a different length than the grid was measured in, is right or wrong on the
    first real run and nowhere before it.

    A click track with a loud one, which is the meter no ear has to arbitrate.
    """
    pytest.importorskip("beat_this.inference", reason="beat_this is not installed")
    step = 60.0 / CLICK_BPM
    times = [round(index * step, 6) for index in range(int(CLICK_SECONDS / step))]
    path = write_hits(
        tmp_path / "accented.wav",
        times,
        seconds=CLICK_SECONDS,
        accents=[1.0 if index % BAR_METER == 0 else 0.3 for index in range(len(times))],
    )

    grid = beats.rows(beats.detect(path))
    salience = bars.accents(path, [float(row["t"]) for row in grid])
    found = bars.mapped(grid, salience)

    assert found.source in (bars.MODEL, bars.INFERRED), found.reasons
    assert found.meter == BAR_METER
    # The loud clicks are two seconds apart; the map has to put its bar lines on them.
    assert [one.t for one in found.bars][:3] == [
        pytest.approx(index * BAR_SECONDS, abs=0.1) for index in range(3)
    ]
    assert [one.in_group for one in found.bars][:5] == [1, 2, 3, 4, 1]


@pytest.mark.live
def test_the_installed_applause_tagger_returns_a_curve_over_the_whole_file(
    tmp_path: Path,
) -> None:
    """Not whether PANNs is right about a synthetic room — whether the call is right.

    What a wrong call gets wrong is the shape: a curve in frames rather than seconds, a
    curve covering only the first chunk, or probabilities that are logits. Those are all
    visible on any audio at all, and none of them are visible without the model installed.
    """
    pytest.importorskip(applause.MODULE, reason="panns_inference is not installed")
    path = write_sections(
        tmp_path / "room.wav",
        (("tone", APPLAUSE_SECONDS), ("noise", ROOM_SECONDS), ("tone", APPLAUSE_SECONDS)),
        sample_rate=44_100,
    )
    total = APPLAUSE_SECONDS * 2 + ROOM_SECONDS

    curve = applause.tag(path)

    assert curve.seconds and len(curve.seconds) == len(curve.probability)
    assert all(0.0 <= one <= 1.0 for one in curve.probability)
    assert curve.seconds[0] < 1.0
    assert curve.seconds[-1] == pytest.approx(total, abs=1.0)


BOARD_MIX = Path(
    r"P:\Client Work\Ryan Devlin\2026-06-17_Zinc Bar\Audio\Reaper\Zinc Set 2 Reaper v4.wav"
)
BOARD_TUNE_STARTS = (107.4405, 1373.8725, 1920.1265, 2725.014, 3568.4815)
"""The five human-established tune starts on that set, from the deliverable's own cuts."""

BOARD_TOLERANCE_SECONDS = 5.0


@pytest.mark.live
def test_the_five_tunes_of_a_board_mix_are_found_where_the_human_cut_them() -> None:
    """The #179 acceptance criterion, at the only seam that can hold it.

    Every rule this exercises is unit-tested on fixtures, and none of those fixtures can
    say the numbers are right: what the thresholds and margins are calibrated against is a
    real 74-minute desk feed with no crowd bleed in it, and the evidence that they still
    are is this file, tagged for real. Skips where the media is not mounted, which is most
    machines — record the run on the ticket when it is.
    """
    pytest.importorskip(applause.MODULE, reason="panns_inference is not installed")
    if not BOARD_MIX.exists():
        pytest.skip(f"the measured board mix is not mounted at {BOARD_MIX}")

    curve = applause.tag(BOARD_MIX)
    read = applause.reading(curve)
    bursts = applause.spans(curve, read.threshold, read.burst_seconds)
    duration = wav.describe(BOARD_MIX)["duration_seconds"]
    calls = applause.tunes(bursts, float(duration))
    loudness = _loudness_of(BOARD_MIX)
    found = applause.settled(calls, loudness)

    # The fallback has to fire here: this is the mix whose whole set sits under 0.3.
    assert read.own_scale is True
    starts = [one.start for one in found.kept]
    assert len(starts) == len(BOARD_TUNE_STARTS)
    for want in BOARD_TUNE_STARTS:
        assert min(abs(one - want) for one in starts) <= BOARD_TOLERANCE_SECONDS


ACQUIRED_SET = "Zinc-Set-2-Reaper-v4.wav-8833f33949fe.wav"
"""The board mix as the acquisition left it in the cache — what the stems on disk were cut from.

Named rather than re-acquired: the export needs Resolve and the project open, and what this
test is about is the directory the separation already filled, which is keyed on this file's
bytes. Reaching it any other way would separate a second copy under a second key.
"""


@pytest.mark.live
def test_a_wind_split_on_the_zinc_stems_runs_that_pass_and_no_other(
    machine_cache: Config,
) -> None:
    """#192's live acceptance: the third pass over a two-pass directory is one pass.

    The fake tier proves the decision — which passes a directory owes — with the separator
    substituted. What it cannot say is that the real ``17_HP-Wind_Inst-UVR`` reads the
    ``other`` stem the first pass wrote and labels its halves the way ``WIND_STEMS`` spells
    them, which is the half of this only a real model on a real set can answer.

    A directory that owes nothing **skips** rather than passes. Measured as found, this would
    go green on zero passes the moment the split was complete — an acceptance criterion that
    has stopped being one. Clearing the wind output first would keep the assertion honest, but
    it makes every run destroy stems it then has to spend half an hour rebuilding, and a run
    killed in between leaves the director short of what it deleted (which is how this test lost
    the Zinc split's orphan half once). A skip costs nobody anything and lies about nothing.

    Skips where the Zinc set is not on this machine — record the run on the ticket when it is.
    """
    config = machine_cache
    acquired = config.audio_dir / ACQUIRED_SET
    if not acquired.exists():
        pytest.skip(f"the acquired Zinc set is not in this cache at {acquired}")
    audio = {"path": str(acquired), "content_sha256": cache.content_hash(acquired)}
    params = {**stems.separation_params(config), "split_wind": True}
    _owing_the_wind_pass(stems.stem_directory(audio, params, config))
    counting = _CountingSeparator()

    output = stems.multi_pass(
        audio, params, _no_progress, split_wind=True, runner=counting, config=config
    )

    assert len(counting.calls) == 1
    assert counting.calls[0][3] == config.wind_model
    assert counting.calls[0][1] == output.result["stems"][stems.OTHER_SOURCE]
    assert output.result["reused"] is False
    assert set(output.result[stems.OTHER_PASS]) == set(stems.WIND_KEYS.values())
    assert all(Path(one).exists() for one in output.result[stems.OTHER_PASS].values())
    assert len(output.result["stems"]) == len(stems.FOUR_STEMS)
    assert len(output.result["drums"]) >= len(stems.DRUM_STEMS)


def _owing_the_wind_pass(directory: Path) -> None:
    """Run only where this directory owes exactly the third pass and nothing else.

    Both halves are skips rather than assertions because both mean the same thing — the state
    this measures is not here — and neither is news about the code. With no first two passes
    on disk this would separate a 74-minute set from scratch and then assert it had not, which
    is half an hour spent learning nothing; with the split already done it would assert over a
    run that did nothing at all.
    """
    if separator.missing_from(directory / stems.MIX_PASS, stems.FOUR_STEMS):
        pytest.skip(f"the Zinc set has no first pass at {directory} to add a pass to")
    if separator.missing_from(directory / stems.DRUM_PASS, stems.DRUM_STEMS):
        pytest.skip(f"the Zinc set has no drum pass at {directory} to reuse")
    if not separator.missing_from(directory / stems.OTHER_PASS, stems.WIND_STEMS):
        pytest.skip(
            f"the wind split at {directory} is already complete, so this run would owe nothing "
            "— delete that pass's directory to measure the split again"
        )


class _CountingSeparator:
    """The real separator call with a note of every pass it was asked for.

    ``environment`` goes through the same seam, so its probe is kept off the count — what is
    being measured is separations, and a run that reused everything still asks what it runs on.
    """

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: Sequence[str], on_line: separator.Lines) -> int:
        if "--env_info" not in argv:
            self.calls.append(list(argv))
        return separator.run(argv, on_line)


def _no_progress(fraction: float, step: str) -> None:
    """A live pass reports for minutes and there is nobody here to read it."""


def _loudness_of(path: Path) -> applause.Loudness:
    """The loudness curve for a file, measured here rather than read from an analysis run."""
    from resolve_mcp.analysis import decode
    from resolve_mcp.analysis import energy as energy_module

    measured = energy_module.measure(decode.read(path), 3.0, 0.5)
    return applause.Loudness(
        seconds=tuple(point.seconds for point in measured.points),
        lufs=tuple(point.lufs for point in measured.points),
    )
