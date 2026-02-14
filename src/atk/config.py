"""XDG path configuration for ATK."""

from __future__ import annotations

import os
from pathlib import Path


def get_runtime_dir() -> Path:
    """Get ATK runtime directory (pipes, PID file)."""
    if env := os.environ.get("ATK_RUNTIME_DIR"):
        return Path(env)
    if xdg := os.environ.get("XDG_RUNTIME_DIR"):
        return Path(xdg) / "atk"
    return Path(f"/tmp/atk-{os.getlogin()}")


def get_state_dir() -> Path:
    """Get ATK state directory (logs)."""
    if xdg := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg) / "atk"
    return Path.home() / ".local" / "state" / "atk"


def get_data_dir() -> Path:
    """Get ATK data directory (playlists)."""
    if xdg := os.environ.get("XDG_DATA_HOME"):
        return Path(xdg) / "atk"
    return Path.home() / ".local" / "share" / "atk"
