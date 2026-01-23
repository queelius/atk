"""Configuration management for ATK."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore


@dataclass
class DaemonConfig:
    """Daemon configuration."""

    log_level: str = "info"
    position_update_interval: float = 1.0


@dataclass
class DefaultsConfig:
    """Default session settings."""

    volume: int = 80
    repeat: str = "none"
    shuffle: bool = False


@dataclass
class PathsConfig:
    """Path configuration."""

    runtime_dir: str = ""
    state_dir: str = ""


@dataclass
class Config:
    """Full ATK configuration."""

    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    defaults: DefaultsConfig = field(default_factory=DefaultsConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)


def get_config_dir() -> Path:
    """Get the ATK config directory."""
    if xdg_config := os.environ.get("XDG_CONFIG_HOME"):
        return Path(xdg_config) / "atk"
    return Path.home() / ".config" / "atk"


def get_state_dir() -> Path:
    """Get the ATK state directory (for logs and saved sessions)."""
    if xdg_state := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg_state) / "atk"
    return Path.home() / ".local" / "state" / "atk"


def get_data_dir() -> Path:
    """Get the ATK data directory."""
    if xdg_data := os.environ.get("XDG_DATA_HOME"):
        return Path(xdg_data) / "atk"
    return Path.home() / ".local" / "share" / "atk"


def get_runtime_dir() -> Path:
    """Get the ATK runtime directory for pipes."""
    if env_dir := os.environ.get("ATK_RUNTIME_DIR"):
        return Path(env_dir)

    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return Path(xdg_runtime) / "atk"

    return Path(f"/tmp/atk-{os.getlogin()}")


def load_config() -> Config:
    """Load configuration from file."""
    config_file = get_config_dir() / "config.toml"

    if not config_file.exists():
        return Config()

    with open(config_file, "rb") as f:
        data = tomllib.load(f)

    return Config(
        daemon=DaemonConfig(**data.get("daemon", {})),
        defaults=DefaultsConfig(**data.get("defaults", {})),
        paths=PathsConfig(**data.get("paths", {})),
    )


def get_effective_runtime_dir(config: Config) -> Path:
    """Get runtime directory, considering config overrides."""
    if config.paths.runtime_dir:
        return Path(config.paths.runtime_dir)
    return get_runtime_dir()


def get_effective_state_dir(config: Config) -> Path:
    """Get state directory, considering config overrides."""
    if config.paths.state_dir:
        return Path(config.paths.state_dir)
    return get_state_dir()
