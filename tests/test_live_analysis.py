"""The one analysis path no fake can stand in for: the real model, actually loaded.

ADR 0002 injects the beat model at a seam, which makes every decision around it testable
and leaves exactly one thing unverified — whether ``beat_this_detector`` calls the installed
package correctly. Its return arity and units are the kind of thing that is right or wrong
on the first real run and nowhere before it, so it gets a live test rather than nothing,
in the spirit of ADR 0001's interpreter guard.

Runs only under ``-m live``, and skips even there if beat_this is not installed — the point
is to be run on the machine that has it, and to say so on the ticket when it has not been.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from resolve_mcp.analysis import beats

from .fakes import write_clicks

CLICK_SECONDS = 20.0
CLICK_BPM = 120.0


@pytest.mark.live
def test_the_installed_beat_model_hears_a_click_track(tmp_path: Path) -> None:
    pytest.importorskip("beat_this.inference", reason="beat_this is not installed")
    path = write_clicks(tmp_path / "clicks.wav", beats_per_minute=CLICK_BPM, seconds=CLICK_SECONDS)

    grid = beats.detect(path)
    found = beats.gist(grid, beats.numbered(grid))

    # Times in seconds, not frames or samples: the unit is what a wrong call gets wrong.
    assert all(0.0 <= one <= CLICK_SECONDS for one in grid.beats)
    assert found["tempo_bpm"] == pytest.approx(CLICK_BPM, abs=10.0)
    # Downbeats are the second of the two lists the model returns, and a subset of the first.
    assert grid.downbeats
    assert set(grid.downbeats) <= set(grid.beats)
