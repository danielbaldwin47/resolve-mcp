"""Cache keys and cache entries — what makes a rerun instant, and what must not.

The cache is the reason an analysis is paid for once per media state, so the tests that
matter are the ones about *missing*: a changed parameter, changed media, or an artifact
someone deleted from the cache directory all have to miss.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

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


# --- identity: the bytes, not the name (#193) -------------------------------------------


def _wav(path: Path, body: bytes = b"RIFF....WAVE") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _refuse(path: Any) -> str:
    raise AssertionError("the hash was already remembered against this file state")


def test_a_copy_of_analysed_audio_shares_the_identity_of_its_original(tmp_path: Path) -> None:
    """The staged copy of a master is the same audio under another name, and pays nothing."""
    master = _wav(tmp_path / "director" / "master.wav")
    staged = get_config().audio_dir / "mix-abc123.wav"
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(master, staged)

    assert cache.audio_identity(master) == cache.audio_identity(staged)
    assert cache.audio_identity(master) == {"sha256": cache.content_hash(master)}


def test_different_audio_is_a_different_identity_however_it_is_named(tmp_path: Path) -> None:
    one = _wav(tmp_path / "a.wav")
    two = _wav(tmp_path / "b.wav", b"RIFF....WAVEx")

    assert cache.audio_identity(one) != cache.audio_identity(two)


def test_a_rewritten_file_is_hashed_again_rather_than_read_off_the_old_note(
    tmp_path: Path,
) -> None:
    media = _wav(tmp_path / "master.wav")
    before = cache.audio_identity(media)

    media.write_bytes(b"RIFF....WAVEx")

    assert cache.audio_identity(media) != before


def test_the_bytes_are_read_once_per_file_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The memo is what keeps content identity affordable in a starter that must return at once."""
    media = _wav(tmp_path / "master.wav")
    first = cache.audio_identity(media)

    monkeypatch.setattr(cache, "content_hash", _refuse)

    assert cache.audio_identity(media) == first


def test_a_note_that_disagrees_with_the_file_is_ignored(tmp_path: Path) -> None:
    """A note is a shortcut, never an authority: it is only used if it still describes the file."""
    media = _wav(tmp_path / "master.wav")
    cache.audio_identity(media)
    for note in get_config().identity_dir.iterdir():
        note.write_text(
            json.dumps({"path": str(media), "size": 999, "mtime_ns": 0, "sha256": "deadbeef"}),
            encoding="utf-8",
        )

    assert cache.audio_identity(media) == {"sha256": cache.content_hash(media)}


def test_an_unreadable_note_is_a_reread_not_a_crash(tmp_path: Path) -> None:
    media = _wav(tmp_path / "master.wav")
    cache.audio_identity(media)
    for note in get_config().identity_dir.iterdir():
        note.write_text("{ truncated", encoding="utf-8")

    assert cache.audio_identity(media) == {"sha256": cache.content_hash(media)}


def test_a_note_that_cannot_be_written_costs_a_reread_not_an_answer(tmp_path: Path) -> None:
    """The cache root is the user's own; a directory they replaced with a file must not raise."""
    get_config().identity_dir.parent.mkdir(parents=True, exist_ok=True)
    get_config().identity_dir.write_text("someone put a file here", encoding="utf-8")
    media = _wav(tmp_path / "master.wav")

    assert cache.audio_identity(media) == {"sha256": cache.content_hash(media)}


def test_audio_the_server_wrote_is_hashed_rather_than_read_off_a_note() -> None:
    """A note is guarded by a fingerprint, and this is the one substrate that cannot risk one.

    A same-size rewrite in place, on a filesystem whose mtime is granular to two seconds, is
    one file state by a fingerprint's reading and different audio in fact. Elsewhere that is
    the risk this cache already ran; under ``audio_dir`` it would be a new one, on the files
    every later job keys off — and these are files the server sized itself, so it can read them.
    """
    acquired = _wav(get_config().audio_dir / "mix.wav")

    assert cache.audio_identity(acquired) == {"sha256": cache.content_hash(acquired)}
    assert not get_config().identity_dir.exists()


# --- what the change to content identity does to entries already on disk ------------------


def test_an_entry_remembered_under_the_old_identity_is_not_hit(tmp_path: Path) -> None:
    """End to end: an entry a previous release wrote for this file cannot come back."""
    media = _wav(tmp_path / "master.wav")
    artifact = _wav(get_config().analysis_dir / "concert-beats.json", b"{}")
    was = cache.cache_key("analyze_music:beats", [cache.fingerprint(media)], {})
    cache.remember(was, "analyze_music:beats", {"path": str(artifact)}, [artifact])

    now = cache.cache_key("analyze_music:beats", [cache.audio_identity(media)], {})

    assert cache.lookup(was) is not None
    assert cache.lookup(now) is None


def test_an_entry_keyed_the_old_way_misses_rather_than_hitting_stale(tmp_path: Path) -> None:
    """Path-keyed identity and content-keyed identity are different shapes, so no key survives."""
    media = _wav(tmp_path / "master.wav")

    was = cache.cache_key("analyze_music:beats", [cache.fingerprint(media)], {})
    now = cache.cache_key("analyze_music:beats", [cache.audio_identity(media)], {})

    assert was != now


def test_an_entry_keyed_on_a_hashed_wav_carries_over_unchanged(tmp_path: Path) -> None:
    """Acquired audio was hashed before this change, so its entries migrate rather than re-run."""
    acquired = _wav(get_config().audio_dir / "mix.wav")

    was = cache.cache_key("analyze_music:beats", [{"sha256": cache.content_hash(acquired)}], {})
    now = cache.cache_key("analyze_music:beats", [cache.audio_identity(acquired)], {})

    assert was == now


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


def test_a_cache_directory_that_is_not_a_directory_is_a_miss_not_a_crash() -> None:
    """The cache root is the user's own; they can leave anything in it, including this."""
    get_config().result_dir.parent.mkdir(parents=True, exist_ok=True)
    get_config().result_dir.write_text("someone put a file here", encoding="utf-8")

    assert cache.lookup("key1") is None
