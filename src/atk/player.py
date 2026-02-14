"""Miniaudio wrapper for audio playback with time-stretching."""

from __future__ import annotations

import threading
from collections.abc import Generator
from pathlib import Path
from typing import Callable

import miniaudio
import numpy as np
from numpy.typing import NDArray

SUPPORTED_EXTENSIONS = {".mp3", ".ogg", ".flac", ".wav", ".opus", ".m4a", ".aac"}
SAMPLE_RATE = 44100
CHANNELS = 2


def list_devices() -> list[dict]:
    """List available audio playback devices."""
    devices = []
    for dev in miniaudio.Devices().get_playbacks():
        dev_id = dev["id"]
        if hasattr(miniaudio, "ffi"):
            raw = bytes(miniaudio.ffi.buffer(dev_id))
            null = raw.find(b"\x00")
            dev_id = raw[:null] if null > 0 else raw
        devices.append(
            {
                "id": dev_id,
                "name": dev["name"],
                "is_default": dev.get("isDefault", False),
            }
        )
    return devices


def _bytes_to_device_id(device_bytes: bytes):
    """Convert bytes back to miniaudio cdata device_id."""
    if not hasattr(miniaudio, "ffi"):
        return device_bytes
    device_id = miniaudio.ffi.new("union ma_device_id *")
    buf = miniaudio.ffi.buffer(device_id)
    buf[0 : len(device_bytes)] = device_bytes
    return device_id


def is_supported(uri: str) -> bool:
    """Check if URI is a supported audio format."""
    return Path(uri).suffix.lower() in SUPPORTED_EXTENSIONS


class Player:
    """Audio player with rate control (time-stretch or tape-style)."""

    def __init__(self, device_id: bytes | None = None):
        self._device: miniaudio.PlaybackDevice | None = None
        self._device_id = device_id
        self._samples: NDArray[np.float32] | None = None
        self._total_frames = 0
        self._position = 0
        self._active = False
        self._playing = False
        self._current_uri: str | None = None
        self._end_callback: Callable[[], None] | None = None
        self._lock = threading.Lock()
        self._volume = 100
        self._rate = 1.0
        self._rate_mode = "stretch"  # "stretch" (WSOLA) or "tape" (resample)

    def set_device(self, device_id: bytes | None) -> None:
        self._device_id = device_id

    def set_end_callback(self, cb: Callable[[], None] | None) -> None:
        self._end_callback = cb

    def load(self, uri: str) -> None:
        """Load and decode audio file into memory."""
        self._current_uri = uri
        self._stop_device()

        path = Path(uri).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported format: {path.suffix}")

        decoded = miniaudio.decode_file(
            str(path),
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=CHANNELS,
            sample_rate=SAMPLE_RATE,
        )
        self._samples = np.array(decoded.samples, dtype=np.float32)
        self._total_frames = len(self._samples) // CHANNELS
        self._position = 0

    def play(self, start_pos: float = 0.0) -> None:
        if self._samples is None:
            return
        with self._lock:
            self._position = max(
                0, min(int(start_pos * SAMPLE_RATE), self._total_frames - 1)
            )
            self._playing = True
            self._active = True
        self._start_device()

    def pause(self) -> None:
        with self._lock:
            self._playing = False

    def unpause(self) -> None:
        if self._samples is None:
            return
        with self._lock:
            self._playing = True
            self._active = True
        if self._device is None:
            self._start_device()

    def stop(self) -> None:
        with self._lock:
            self._playing = False
            self._active = False
        self._stop_device()
        self._position = 0

    def is_playing(self) -> bool:
        return self._playing

    def is_paused(self) -> bool:
        return self._active and not self._playing

    def get_position(self) -> float:
        return self._position / SAMPLE_RATE

    def get_duration(self) -> float:
        return self._total_frames / SAMPLE_RATE

    def seek(self, position: float) -> None:
        if self._samples is None:
            return
        with self._lock:
            self._position = max(
                0, min(int(position * SAMPLE_RATE), self._total_frames - 1)
            )

    def set_volume(self, level: int) -> None:
        self._volume = max(0, min(100, level))

    def get_volume(self) -> int:
        return self._volume

    def set_rate(self, speed: float, mode: str | None = None) -> None:
        """Set playback rate. mode: 'stretch' (default) or 'tape'."""
        self._rate = max(0.25, min(4.0, speed))
        if mode:
            self._rate_mode = mode

    def get_rate(self) -> float:
        return self._rate

    @property
    def current_uri(self) -> str | None:
        return self._current_uri

    # --- Internal ---

    def _start_device(self) -> None:
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
        if self._device is not None:
            self._device.close()
            self._device = None

    def _audio_generator(self) -> Generator[bytes, int, None]:
        """Generate processed audio chunks for playback."""
        required_frames = yield b""

        while self._active:
            with self._lock:
                playing = self._playing

            if not playing:
                silence = np.zeros(required_frames * CHANNELS, dtype=np.float32)
                required_frames = yield silence.tobytes()
                continue

            if self._samples is None:
                break

            # Read source frames (more when speeding up, fewer when slowing)
            source_frames = (
                int(required_frames * self._rate)
                if self._rate != 1.0
                else required_frames
            )
            chunk = self._read_chunk(source_frames)

            if len(chunk) == 0:
                with self._lock:
                    self._active = False
                    self._playing = False
                if self._end_callback:
                    self._end_callback()
                break

            # Apply rate change
            if self._rate != 1.0:
                if self._rate_mode == "tape":
                    chunk = self._tape_resample(chunk, required_frames)
                else:
                    chunk = self._time_stretch(chunk, required_frames)

            # Apply volume and clip
            chunk = np.clip(chunk * (self._volume / 100.0), -1.0, 1.0)

            # Pad/trim to exact output size
            expected = required_frames * CHANNELS
            if len(chunk) < expected:
                chunk = np.pad(chunk, (0, expected - len(chunk)))
            elif len(chunk) > expected:
                chunk = chunk[:expected]

            required_frames = yield chunk.astype(np.float32).tobytes()

    def _read_chunk(self, frames: int) -> NDArray[np.float32]:
        """Read next chunk of raw interleaved samples."""
        if self._samples is None:
            return np.array([], dtype=np.float32)
        with self._lock:
            start = self._position * CHANNELS
            end = min((self._position + frames) * CHANNELS, len(self._samples))
            chunk = self._samples[start:end].copy()
            self._position = min(self._position + frames, self._total_frames)
        return chunk

    def _tape_resample(
        self, chunk: NDArray[np.float32], target_frames: int
    ) -> NDArray[np.float32]:
        """Tape-style rate: resample (changes pitch with speed)."""
        frames = len(chunk) // CHANNELS
        if frames == 0 or target_frames <= 0:
            return np.array([], dtype=np.float32)
        audio = chunk.reshape(frames, CHANNELS)
        old_idx = np.arange(frames)
        new_idx = np.linspace(0, frames - 1, target_frames)
        result = np.zeros((target_frames, CHANNELS), dtype=np.float32)
        for ch in range(CHANNELS):
            result[:, ch] = np.interp(new_idx, old_idx, audio[:, ch])
        return result.flatten()

    def _time_stretch(
        self, chunk: NDArray[np.float32], target_frames: int
    ) -> NDArray[np.float32]:
        """WSOLA time-stretch: change speed while preserving pitch."""
        frames = len(chunk) // CHANNELS
        if frames == 0 or target_frames <= 0:
            return np.array([], dtype=np.float32)

        audio = chunk.reshape(frames, CHANNELS)
        win_len = min(1024, frames)
        hop_in = win_len // 2
        if hop_in == 0:
            return chunk

        # Output hop adjusted for rate
        hop_out = max(
            1, int(hop_in * frames / (target_frames if target_frames > 0 else frames))
        )
        window = np.hanning(win_len).astype(np.float32)

        # Estimate output length and allocate
        n_windows = max(1, (frames - win_len) // hop_in + 1)
        out_len = (n_windows - 1) * hop_out + win_len
        output = np.zeros((out_len, CHANNELS), dtype=np.float32)
        norm = np.zeros(out_len, dtype=np.float32)

        for i in range(n_windows):
            in_start = i * hop_in
            out_start = i * hop_out
            if in_start + win_len > frames or out_start + win_len > out_len:
                break
            for ch in range(CHANNELS):
                output[out_start : out_start + win_len, ch] += (
                    audio[in_start : in_start + win_len, ch] * window
                )
            norm[out_start : out_start + win_len] += window

        # Normalize to avoid amplitude changes
        mask = norm > 1e-8
        output[mask] /= norm[mask, np.newaxis]

        # Trim/pad to target
        if len(output) >= target_frames:
            output = output[:target_frames]
        else:
            output = np.pad(output, ((0, target_frames - len(output)), (0, 0)))

        return output.flatten()
