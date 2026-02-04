"""Miniaudio wrapper for audio playback."""

from __future__ import annotations

import threading
from collections.abc import Generator
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import miniaudio
import numpy as np
from numpy.typing import NDArray

# Supported audio formats
SUPPORTED_EXTENSIONS = {".mp3", ".ogg", ".flac", ".wav", ".opus", ".m4a", ".aac"}

# Audio settings
SAMPLE_RATE = 44100
CHANNELS = 2
CHUNK_FRAMES = 2048  # Frames per chunk for processing


def list_audio_devices() -> list[dict]:
    """List available audio playback devices."""
    devices = []
    playback_devices = miniaudio.Devices()
    for dev in playback_devices.get_playbacks():
        # Convert cdata device ID to bytes for serialization
        device_id = dev["id"]
        if hasattr(miniaudio, "ffi"):
            # Convert cdata to bytes, strip null padding
            raw_bytes = bytes(miniaudio.ffi.buffer(device_id))
            # Find null terminator and truncate
            null_idx = raw_bytes.find(b"\x00")
            if null_idx > 0:
                raw_bytes = raw_bytes[:null_idx]
            device_id = raw_bytes
        devices.append(
            {
                "id": device_id,
                "name": dev["name"],
                "is_default": dev.get("isDefault", False),
            }
        )
    return devices


def _bytes_to_device_id(device_bytes: bytes):
    """Convert bytes back to miniaudio cdata device_id."""
    if not hasattr(miniaudio, "ffi"):
        return device_bytes
    # Create a new cdata device_id and copy bytes into it
    device_id = miniaudio.ffi.new("union ma_device_id *")
    buf = miniaudio.ffi.buffer(device_id)
    buf[0 : len(device_bytes)] = device_bytes
    return device_id


class Player:
    """Audio player using miniaudio."""

    def __init__(self, device_id: bytes | None = None):
        self._device: miniaudio.PlaybackDevice | None = None
        self._device_id: bytes | None = device_id
        self._samples: NDArray[np.float32] | None = (
            None  # Decoded PCM (interleaved stereo)
        )
        self._sample_rate: int = SAMPLE_RATE
        self._num_channels: int = CHANNELS
        self._total_frames: int = 0

        # Playback state
        self._position: int = 0  # Current frame position
        self._active: bool = False  # Generator running
        self._playing: bool = False  # Actively playing (not paused)
        self._current_uri: str | None = None
        self._end_callback: Callable[[], None] | None = None
        self._lock = threading.Lock()

        # Playback parameters
        self._volume: int = 100
        self._rate: float = 1.0

    def set_device(self, device_id: bytes | None) -> None:
        """Set the playback device. Requires restart to take effect."""
        self._device_id = device_id

    def set_end_callback(self, callback: Callable[[], None] | None) -> None:
        """Set callback for when track ends."""
        self._end_callback = callback

    def load(self, uri: str) -> None:
        """Load and decode entire audio file into memory."""
        self._current_uri = uri

        # Stop any current playback
        self._stop_device()

        parsed = urlparse(uri)
        if parsed.scheme in ("http", "https"):
            raise NotImplementedError("URL streaming not yet supported with miniaudio")

        path = Path(uri).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported format: {path.suffix}")

        # Decode entire file
        decoded = miniaudio.decode_file(
            str(path),
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=CHANNELS,
            sample_rate=SAMPLE_RATE,
        )

        # Convert to numpy array
        self._samples = np.array(decoded.samples, dtype=np.float32)
        self._sample_rate = decoded.sample_rate
        self._num_channels = decoded.nchannels
        self._total_frames = len(self._samples) // self._num_channels

        # Reset position
        self._position = 0

    def play(self, start_pos: float = 0.0) -> None:
        """Start playback, optionally from position in seconds."""
        if self._samples is None:
            return

        with self._lock:
            self._position = int(start_pos * self._sample_rate)
            self._position = max(0, min(self._position, self._total_frames - 1))
            self._playing = True
            self._active = True

        self._start_device()

    def pause(self) -> None:
        """Pause playback."""
        with self._lock:
            self._playing = False

    def unpause(self) -> None:
        """Resume playback."""
        if self._samples is None:
            return
        with self._lock:
            self._playing = True
            self._active = True
        if self._device is None:
            self._start_device()

    def stop(self) -> None:
        """Stop playback."""
        with self._lock:
            self._playing = False
            self._active = False
        self._stop_device()
        self._position = 0

    def is_playing(self) -> bool:
        """Check if currently playing (not paused)."""
        return self._playing

    def is_paused(self) -> bool:
        """Check if paused (active but not playing)."""
        return self._active and not self._playing

    def get_position(self) -> float:
        """Get current playback position in seconds."""
        return self._position / self._sample_rate if self._sample_rate > 0 else 0.0

    def get_duration(self) -> float:
        """Get total duration in seconds."""
        return self._total_frames / self._sample_rate if self._sample_rate > 0 else 0.0

    def seek(self, position: float) -> None:
        """Seek to position in seconds."""
        if self._samples is None:
            return

        frame = int(position * self._sample_rate)
        with self._lock:
            self._position = max(0, min(frame, self._total_frames - 1))

    def set_volume(self, level: int) -> None:
        """Set volume (0-100)."""
        self._volume = max(0, min(100, level))

    def get_volume(self) -> int:
        """Get current volume (0-100)."""
        return self._volume

    def set_rate(self, speed: float) -> None:
        """Set playback rate (0.25 to 4.0)."""
        self._rate = max(0.25, min(4.0, speed))

    def get_rate(self) -> float:
        """Get current playback rate."""
        return self._rate

    @property
    def current_uri(self) -> str | None:
        """Get the currently loaded URI."""
        return self._current_uri

    # Internal methods

    def _start_device(self) -> None:
        """Start the playback device."""
        if self._device is not None:
            return

        kwargs: dict = dict(
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=CHANNELS,
            sample_rate=SAMPLE_RATE,
        )
        if self._device_id:
            kwargs["device_id"] = _bytes_to_device_id(self._device_id)

        self._device = miniaudio.PlaybackDevice(**kwargs)
        self._device.start(self._audio_generator())

    def _stop_device(self) -> None:
        """Stop the playback device."""
        if self._device is not None:
            self._device.close()
            self._device = None

    def _audio_generator(self) -> Generator[bytes, int, None]:
        """Generate processed audio chunks for playback."""
        required_frames = yield b""  # Initial yield to get required frames

        while self._active:
            with self._lock:
                playing = self._playing

            if not playing:
                # Output silence while paused
                silence = np.zeros(
                    required_frames * self._num_channels, dtype=np.float32
                )
                required_frames = yield silence.tobytes()
                continue

            if self._samples is None:
                break

            # Get raw chunk - need to handle rate for position advancement
            frames_to_read = (
                int(required_frames * self._rate)
                if self._rate != 1.0
                else required_frames
            )
            chunk = self._get_next_chunk(frames_to_read)

            if len(chunk) == 0:
                # End of audio
                with self._lock:
                    self._active = False
                    self._playing = False
                if self._end_callback:
                    self._end_callback()
                break

            # Apply rate change if needed
            if self._rate != 1.0:
                chunk = self._apply_rate(chunk, required_frames)

            # Apply volume
            vol = self._volume / 100.0
            chunk = chunk * vol

            # Clip to prevent distortion
            chunk = np.clip(chunk, -1.0, 1.0)

            # Ensure we have the right number of samples
            expected_samples = required_frames * self._num_channels
            if len(chunk) < expected_samples:
                # Pad with zeros
                chunk = np.pad(chunk, (0, expected_samples - len(chunk)))
            elif len(chunk) > expected_samples:
                chunk = chunk[:expected_samples]

            required_frames = yield chunk.astype(np.float32).tobytes()

    def _get_next_chunk(self, frames: int) -> NDArray[np.float32]:
        """Get next chunk of raw samples."""
        if self._samples is None:
            return np.array([], dtype=np.float32)

        with self._lock:
            start_sample = self._position * self._num_channels
            end_frame = self._position + frames

            # Normal playback
            end_sample = min(end_frame * self._num_channels, len(self._samples))
            chunk = self._samples[start_sample:end_sample].copy()
            self._position = min(end_frame, self._total_frames)

        return chunk

    def _apply_rate(
        self, chunk: NDArray[np.float32], target_frames: int
    ) -> NDArray[np.float32]:
        """Apply rate change via linear interpolation (tape-style)."""
        if len(chunk) == 0:
            return chunk

        frames = len(chunk) // self._num_channels
        if frames == 0:
            return chunk

        # Reshape to (frames, channels) for processing
        chunk = chunk.reshape(frames, self._num_channels)

        # Resample to target_frames
        if target_frames <= 0:
            return np.array([], dtype=np.float32)

        # Linear interpolation for each channel
        old_indices = np.arange(frames)
        new_indices = np.linspace(0, frames - 1, target_frames)

        result = np.zeros((target_frames, self._num_channels), dtype=np.float32)
        for ch in range(self._num_channels):
            result[:, ch] = np.interp(new_indices, old_indices, chunk[:, ch])

        return result.flatten().astype(np.float32)

    @classmethod
    def shutdown(cls) -> None:
        """Shutdown - no global state to clean with miniaudio."""
        pass


def get_track_duration(uri: str) -> float | None:
    """Get duration of a track in seconds."""
    try:
        parsed = urlparse(uri)
        if parsed.scheme in ("http", "https"):
            return None  # Can't determine for URLs without loading

        path = Path(uri).expanduser().resolve()
        if not path.exists():
            return None

        # Use miniaudio to get file info
        info = (
            miniaudio.mp3_get_file_info(str(path))
            if path.suffix.lower() == ".mp3"
            else None
        )
        if info:
            return info.duration

        # Fallback: decode header only
        try:
            info = miniaudio.get_file_info(str(path))
            if info and info.duration:
                return info.duration
        except Exception:
            pass

        # Try mutagen if available
        try:
            from mutagen import File as MutagenFile

            audio = MutagenFile(uri)
            if audio and audio.info:
                return audio.info.length
        except ImportError:
            pass

    except Exception:
        pass

    return None


def is_supported_format(uri: str) -> bool:
    """Check if the URI is a supported format."""
    parsed = urlparse(uri)
    if parsed.scheme in ("http", "https"):
        # URLs not yet supported
        return False

    path = Path(uri)
    return path.suffix.lower() in SUPPORTED_EXTENSIONS
