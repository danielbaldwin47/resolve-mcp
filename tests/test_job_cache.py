"""Cache keys and cache entries — what makes a rerun instant, and what must not.

The cache is the reason an analysis is paid for once per media state, so the tests that
matter are the ones about *missing*: a changed parameter, changed media, or an artifact
someone deleted from the cache directory all have to miss.
"""

from __future__ import annotations

import os
from pathlib import Path

from resolve_mcp.config import get_config
from resolve_mcp.jobs import cache


def test_the_key_ignores_the_order_the_parameters_were_written_in() -> None:
    one = cache.cache_key("analyze_music", [{"sha256": "abc"}], {"beats": True, "energy": False})
    two = cache.cache_key("analyze_music", [{"sha256": "abc"}], {"energy": False, "beats": True})

    assert one == two


def test_a_different_parameter_kind_or_input_is_a_different_key() -> None:
    base = cache.cache_key("analyze_music", [{"sha256": "abc"}], {"beats": True})

    assert cache.cache_key("analyze_music", [{"sha256": "abc"}], {"beats": False}) != base
    assert cache.cache_key("separate_stems", [{"sha256": "abc"}], {"beats": True}) != base
    assert cache.cache_key("analyze_music", [{"sha256": "def"}], {"beats": True}) != base


def test_a_source_file_fingerprints_by_size_and_mtime_not_by_reading_it(tmp_path: Path) -> None:
    """Hashing a concert master would take minutes; identity has to be cheap here."""
    media = tmp_path / "master.mov"
    media.write_bytes(b"pretend this is 40 GB")
    before = cache.fingerprint(media)

    os.utime(media, (0, 0))

    assert before["size"] == len(b"pretend this is 40 GB")
    assert cache.fingerprint(media) != before


def test_acquired_audio_is_identified_by_its_bytes(tmp_path: Path) -> None:
    """Downstream analysis keys off content, so two identical WAVs must share a hash."""
    one = tmp_path / "a.wav"
    two = tmp_path / "b.wav"
    one.write_bytes(b"RIFF....WAVE")
    two.write_bytes(b"RIFF....WAVE")

    assert cache.content_hash(one) == cache.content_hash(two)

    two.write_bytes(b"RIFF....WAVEx")
    assert cache.content_hash(one) != cache.content_hash(two)


def test_a_remembered_result_comes_straight_back(tmp_path: Path) -> None:
    artifact = get_config().audio_dir / "mix.wav"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"RIFF")
    cache.remember("key1", "extract_audio", {"path": str(artifact)}, [artifact])

    hit = cache.lookup("key1")

    assert hit == {"path": str(artifact)}


def test_a_key_that_was_never_run_misses() -> None:
    assert cache.lookup("nothing-here") is None


def test_a_deleted_artifact_misses_rather_than_returning_a_dead_path() -> None:
    artifact = get_config().audio_dir / "mix.wav"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"RIFF")
    cache.remember("key1", "extract_audio", {"path": str(artifact)}, [artifact])
    artifact.unlink()

    assert cache.lookup("key1") is None


def test_the_stale_entry_is_cleared_so_the_next_run_can_write_it_again() -> None:
    artifact = get_config().audio_dir / "mix.wav"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"RIFF")
    cache.remember("key1", "extract_audio", {"path": str(artifact)}, [artifact])
    artifact.unlink()
    cache.lookup("key1")

    assert not (get_config().result_dir / "key1.json").exists()


def test_an_unreadable_entry_misses_rather_than_raising() -> None:
    get_config().result_dir.mkdir(parents=True, exist_ok=True)
    (get_config().result_dir / "key1.json").write_text("{ truncated", encoding="utf-8")

    assert cache.lookup("key1") is None
