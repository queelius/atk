"""Pytest configuration and fixtures for ATK tests."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


class MockDecodedSamples:
    """Mock for miniaudio decoded samples."""

    def __init__(
        self, duration: float = 60.0, sample_rate: int = 44100, channels: int = 2
    ):
        self.sample_rate = sample_rate
        self.nchannels = channels
        num_samples = int(duration * sample_rate * channels)
        self.samples = np.zeros(num_samples, dtype=np.float32)


class MockPlaybackDevice:
    """Mock for miniaudio.PlaybackDevice."""

    def __init__(self, **kwargs):
        self.output_format = kwargs.get("output_format")
        self.nchannels = kwargs.get("nchannels", 2)
        self.sample_rate = kwargs.get("sample_rate", 44100)
        self._generator = None

    def start(self, generator):
        self._generator = generator
        try:
            next(generator)
        except StopIteration:
            pass

    def close(self):
        self._generator = None


@pytest.fixture
def temp_runtime_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary runtime directory."""
    runtime = tmp_path / "atk-test"
    runtime.mkdir(parents=True)

    old_env = os.environ.get("ATK_RUNTIME_DIR")
    os.environ["ATK_RUNTIME_DIR"] = str(runtime)

    yield runtime

    if old_env:
        os.environ["ATK_RUNTIME_DIR"] = old_env
    else:
        os.environ.pop("ATK_RUNTIME_DIR", None)


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary data directory."""
    old_env = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = str(tmp_path)

    yield tmp_path / "atk"

    if old_env:
        os.environ["XDG_DATA_HOME"] = old_env
    else:
        os.environ.pop("XDG_DATA_HOME", None)


@pytest.fixture
def mock_miniaudio():
    """Mock miniaudio for tests."""
    mock_ma = MagicMock()
    mock_ma.SampleFormat = MagicMock()
    mock_ma.SampleFormat.FLOAT32 = "float32"
    mock_ma.decode_file = lambda path, **kw: MockDecodedSamples()
    mock_ma.PlaybackDevice = MockPlaybackDevice
    mock_info = MagicMock()
    mock_info.duration = 60.0
    mock_ma.mp3_get_file_info = MagicMock(return_value=mock_info)
    mock_ma.get_file_info = MagicMock(return_value=mock_info)

    with patch.dict("sys.modules", {"miniaudio": mock_ma}):
        with patch("atk.player.miniaudio", mock_ma):
            yield mock_ma


@pytest.fixture
def mock_player(mock_miniaudio):
    """Create a mocked Player instance."""
    from atk.player import Player

    return Player()


@pytest.fixture
def sample_audio_file(tmp_path: Path) -> Path:
    """Create a sample audio file for testing."""
    audio_file = tmp_path / "test.mp3"
    audio_file.write_bytes(b"\x00" * 1024)
    return audio_file


@pytest.fixture
def sample_queue(sample_audio_file: Path) -> list[str]:
    """Create a sample queue of audio files."""
    return [str(sample_audio_file)] * 3


@pytest.fixture
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
