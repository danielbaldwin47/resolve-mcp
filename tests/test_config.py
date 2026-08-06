"""Zero-config defaults, env overrides, no config file.

Expected values are compared as ``PureWindowsPath`` so the assertions read
"same Windows path" on any host — the fake tier runs on ubuntu in CI, where
a native ``Path`` treats backslashes as filename characters, not separators.
The conversion must go through ``str``: on Python 3.11, ``PureWindowsPath``
built from a ``PosixPath`` object re-joins its parts and silently drops the
root after the drive (``C:Program Files``).
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

from resolve_mcp.config import Config


def as_windows(p: Path) -> PureWindowsPath:
    return PureWindowsPath(str(p))


def test_defaults_come_from_the_standard_windows_install() -> None:
    config = Config.from_env(
        {"PROGRAMDATA": r"C:\ProgramData", "LOCALAPPDATA": r"C:\Users\d\AppData\Local"}
    )

    assert as_windows(config.script_api) == PureWindowsPath(
        r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\Developer\Scripting"
    )
    assert as_windows(config.script_lib) == PureWindowsPath(
        r"C:\Program Files\Blackmagic Design\DaVinci Resolve\fusionscript.dll"
    )
    assert as_windows(config.cache_dir) == PureWindowsPath(
        r"C:\Users\d\AppData\Local\resolve-mcp"
    )
    assert config.log_level == "INFO"


def test_every_path_has_an_env_override() -> None:
    config = Config.from_env(
        {
            "RESOLVE_SCRIPT_API": r"D:\resolve\Scripting",
            "RESOLVE_SCRIPT_LIB": r"D:\resolve\fusionscript.dll",
            "RESOLVE_MCP_CACHE": r"E:\cache",
            "RESOLVE_MCP_LOG_LEVEL": "DEBUG",
        }
    )

    assert config.script_api == Path(r"D:\resolve\Scripting")
    assert config.script_lib == Path(r"D:\resolve\fusionscript.dll")
    assert config.cache_dir == Path(r"E:\cache")
    assert config.log_level == "DEBUG"


def test_survives_an_environment_with_nothing_set() -> None:
    config = Config.from_env({})

    assert config.script_api.name == "Scripting"
    assert config.cache_dir.name == "resolve-mcp"


def test_the_scripting_modules_directory_hangs_off_the_api_root() -> None:
    config = Config.from_env({"RESOLVE_SCRIPT_API": r"D:\resolve\Scripting"})

    assert as_windows(config.script_modules) == PureWindowsPath(
        r"D:\resolve\Scripting\Modules"
    )


def test_artifacts_live_under_the_cache_root(tmp_path: Path) -> None:
    config = Config.from_env({"RESOLVE_MCP_CACHE": str(tmp_path)})

    assert config.snapshot_dir == tmp_path / "snapshots"
    assert config.job_dir == tmp_path / "jobs"
    assert config.result_dir == tmp_path / "results"
    assert config.audio_dir == tmp_path / "audio"
    assert config.stems_dir == tmp_path / "stems"


def test_the_separator_and_its_two_models_are_overridable() -> None:
    """The models are named, not discovered: a rename must not silently change a cache key."""
    assert Config.from_env({}).audio_separator == "audio-separator"
    assert Config.from_env({}).stem_model.startswith("htdemucs")
    assert "DrumSep" in Config.from_env({}).drum_model

    config = Config.from_env(
        {
            "RESOLVE_MCP_AUDIO_SEPARATOR": r"D:\tools\audio-separator.exe",
            "RESOLVE_MCP_STEM_MODEL": "htdemucs_6s.yaml",
            "RESOLVE_MCP_DRUM_MODEL": "drumsep.ckpt",
        }
    )

    assert config.audio_separator == r"D:\tools\audio-separator.exe"
    assert config.stem_model == "htdemucs_6s.yaml"
    assert config.drum_model == "drumsep.ckpt"


def test_ffmpeg_is_a_bare_name_on_path_until_told_otherwise() -> None:
    assert Config.from_env({}).ffmpeg == "ffmpeg"
    assert Config.from_env({"RESOLVE_MCP_FFMPEG": r"D:\tools\ffmpeg.exe"}).ffmpeg == (
        r"D:\tools\ffmpeg.exe"
    )
