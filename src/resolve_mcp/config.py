"""Zero-config defaults with env overrides. No config file in v1.

The config surface is the env passthrough in the Claude Code MCP entry; the defaults
assume a stock Windows install of Resolve Studio.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

SCRIPT_API_UNDER_PROGRAMDATA = Path("Blackmagic Design/DaVinci Resolve/Support/Developer/Scripting")
DEFAULT_PROGRAMDATA = Path("C:/ProgramData")
DEFAULT_SCRIPT_LIB = Path("C:/Program Files/Blackmagic Design/DaVinci Resolve/fusionscript.dll")
DEFAULT_LOG_LEVEL = "INFO"
CACHE_DIR_NAME = "resolve-mcp"
DEFAULT_FFMPEG = "ffmpeg"


TRUTHY = {"1", "true", "yes", "on"}
BYPASS_ENV = "RESOLVE_MCP_ALLOW_ANY_PYTHON"


@dataclass(frozen=True)
class Config:
    script_api: Path
    script_lib: Path
    cache_dir: Path
    log_level: str
    allow_any_python: bool = False
    ffmpeg: str = DEFAULT_FFMPEG

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        program_data = Path(env.get("PROGRAMDATA") or DEFAULT_PROGRAMDATA)
        local_app_data = Path(env.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return cls(
            script_api=Path(
                env.get("RESOLVE_SCRIPT_API") or program_data / SCRIPT_API_UNDER_PROGRAMDATA
            ),
            script_lib=Path(env.get("RESOLVE_SCRIPT_LIB") or DEFAULT_SCRIPT_LIB),
            cache_dir=Path(env.get("RESOLVE_MCP_CACHE") or local_app_data / CACHE_DIR_NAME),
            log_level=env.get("RESOLVE_MCP_LOG_LEVEL") or DEFAULT_LOG_LEVEL,
            allow_any_python=(env.get(BYPASS_ENV) or "").lower() in TRUTHY,
            ffmpeg=env.get("RESOLVE_MCP_FFMPEG") or DEFAULT_FFMPEG,
        )

    @property
    def script_modules(self) -> Path:
        """Where ``DaVinciResolveScript.py`` lives."""
        return self.script_api / "Modules"

    @property
    def snapshot_dir(self) -> Path:
        """Where opaque ``.drp`` safety snapshots land."""
        return self.cache_dir / "snapshots"

    @property
    def listing_dir(self) -> Path:
        """Where listings too big to return inline spill to."""
        return self.cache_dir / "listings"

    @property
    def job_dir(self) -> Path:
        """One JSON record per job — the only thing that survives a server restart."""
        return self.cache_dir / "jobs"

    @property
    def result_dir(self) -> Path:
        """Cache entries, one per content+params key, pointing at the artifacts they own."""
        return self.cache_dir / "results"

    @property
    def audio_dir(self) -> Path:
        """Acquired WAVs. Analysis workers key off the content hash of what lands here."""
        return self.cache_dir / "audio"


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config


def set_config(config: Config) -> None:
    global _config
    _config = config


def reset_config() -> None:
    global _config
    _config = None
