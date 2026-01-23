"""Miniaudio wrapper for audio playback with DSP processing."""

from __future__ import annotations

import threading
from collections.abc import Generator
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import miniaudio
import numpy as np
from numpy.typing import NDArray
from scipy import signal as scipy_signal

# Try to import librosa for independent rate/pitch control
try:
    import librosa
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False


# Supported audio formats
SUPPORTED_EXTENSIONS = {".mp3", ".ogg", ".flac", ".wav", ".opus", ".m4a", ".aac"}

# Audio settings
SAMPLE_RATE = 44100
CHANNELS = 2
CHUNK_FRAMES = 2048  # Frames per chunk for processing


class Player:
    """Audio player with DSP processing using miniaudio."""

    _lock = threading.Lock()

    def __init__(self):
        self._device: miniaudio.PlaybackDevice | None = None
        self._samples: NDArray[np.float32] | None = None  # Decoded PCM (interleaved stereo)
        self._sample_rate: int = SAMPLE_RATE
        self._num_channels: int = CHANNELS
        self._total_frames: int = 0

        # Playback state
        self._position: int = 0  # Current frame position
        self._playing: bool = False
        self._paused: bool = False
        self._current_uri: str | None = None
        self._end_callback: Callable[[], None] | None = None

        # DSP parameters
        self._volume: int = 100
        self._rate: float = 1.0
        self._pitch: float = 0.0  # semitones
        self._bass: float = 0.0   # dB
        self._treble: float = 0.0  # dB

        # A/B loop (in frames)
        self._loop_a: int | None = None
        self._loop_b: int | None = None
        self._loop_enabled: bool = False

        # Fade state
        self._fade_target: int | None = None
        self._fade_duration: float = 0.0
        self._fade_start_vol: int = 0
        self._fade_start_frame: int = 0
        self._fade_total_frames: int = 0

        # EQ filter states (for continuous filtering)
        self._bass_zi: NDArray | None = None
        self._treble_zi: NDArray | None = None

    def set_end_callback(self, callback: Callable[[], None] | None) -> None:
        """Set callback for when track ends."""
        self._end_callback = callback

    def check_end_event(self) -> bool:
        """Check if end event occurred. For compatibility with session's position update loop."""
        # With miniaudio we handle end via the generator returning, so this is a no-op
        return False

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

        # Reset position and filter states
        self._position = 0
        self._bass_zi = None
        self._treble_zi = None

    def play(self, start_pos: float = 0.0) -> None:
        """Start playback, optionally from position in seconds."""
        if self._samples is None:
            return

        self._position = int(start_pos * self._sample_rate)
        self._position = max(0, min(self._position, self._total_frames - 1))
        self._playing = True
        self._paused = False

        self._start_device()

    def pause(self) -> None:
        """Pause playback."""
        self._paused = True
        self._playing = False

    def unpause(self) -> None:
        """Resume playback."""
        if self._samples is None:
            return
        self._paused = False
        self._playing = True
        if self._device is None:
            self._start_device()

    def stop(self) -> None:
        """Stop playback."""
        self._playing = False
        self._paused = False
        self._stop_device()
        self._position = 0

    def is_playing(self) -> bool:
        """Check if currently playing (not paused)."""
        return self._playing and not self._paused

    def is_paused(self) -> bool:
        """Check if paused."""
        return self._paused

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
        self._position = max(0, min(frame, self._total_frames - 1))

        # Reset filter states on seek
        self._bass_zi = None
        self._treble_zi = None

    def set_volume(self, level: int) -> None:
        """Set volume (0-100)."""
        self._volume = max(0, min(100, level))

    def get_volume(self) -> int:
        """Get current volume (0-100)."""
        return self._volume

    # DSP parameter setters

    def set_rate(self, speed: float) -> None:
        """Set playback rate (0.25 to 4.0)."""
        self._rate = max(0.25, min(4.0, speed))

    def get_rate(self) -> float:
        """Get current playback rate."""
        return self._rate

    def set_pitch(self, semitones: float) -> None:
        """Set pitch shift in semitones (-12 to +12)."""
        self._pitch = max(-12.0, min(12.0, semitones))

    def get_pitch(self) -> float:
        """Get current pitch shift in semitones."""
        return self._pitch

    def set_bass(self, db: float) -> None:
        """Set bass adjustment in dB (-12 to +12)."""
        self._bass = max(-12.0, min(12.0, db))
        self._bass_zi = None  # Reset filter state

    def get_bass(self) -> float:
        """Get current bass adjustment in dB."""
        return self._bass

    def set_treble(self, db: float) -> None:
        """Set treble adjustment in dB (-12 to +12)."""
        self._treble = max(-12.0, min(12.0, db))
        self._treble_zi = None  # Reset filter state

    def get_treble(self) -> float:
        """Get current treble adjustment in dB."""
        return self._treble

    # A/B Loop

    def set_loop_points(self, a: float | None, b: float | None) -> None:
        """Set A/B loop points in seconds."""
        if a is not None:
            self._loop_a = int(a * self._sample_rate)
        if b is not None:
            self._loop_b = int(b * self._sample_rate)

    def get_loop_points(self) -> tuple[float | None, float | None]:
        """Get A/B loop points in seconds."""
        a = self._loop_a / self._sample_rate if self._loop_a is not None else None
        b = self._loop_b / self._sample_rate if self._loop_b is not None else None
        return (a, b)

    def set_loop_enabled(self, enabled: bool) -> None:
        """Enable or disable A/B loop."""
        self._loop_enabled = enabled

    def is_loop_enabled(self) -> bool:
        """Check if A/B loop is enabled."""
        return self._loop_enabled

    # Fade

    def start_fade(self, target_volume: int, duration: float) -> None:
        """Start volume fade over duration in seconds."""
        self._fade_target = max(0, min(100, target_volume))
        self._fade_duration = duration
        self._fade_start_vol = self._volume
        self._fade_start_frame = self._position
        self._fade_total_frames = int(duration * self._sample_rate)

    def cancel_fade(self) -> None:
        """Cancel any active fade."""
        self._fade_target = None

    @property
    def current_uri(self) -> str | None:
        """Get the currently loaded URI."""
        return self._current_uri

    # Internal methods

    def _start_device(self) -> None:
        """Start the playback device."""
        if self._device is not None:
            return

        def stream_generator():
            yield from self._audio_generator()

        self._device = miniaudio.PlaybackDevice(
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=CHANNELS,
            sample_rate=SAMPLE_RATE,
        )
        self._device.start(stream_generator())

    def _stop_device(self) -> None:
        """Stop the playback device."""
        if self._device is not None:
            self._device.close()
            self._device = None

    def _audio_generator(self) -> Generator[bytes, int, None]:
        """Generate processed audio chunks for playback."""
        required_frames = yield b""  # Initial yield to get required frames

        while self._playing:
            if self._paused:
                # Output silence while paused
                silence = np.zeros(required_frames * self._num_channels, dtype=np.float32)
                required_frames = yield silence.tobytes()
                continue

            if self._samples is None:
                break

            # Get raw chunk
            chunk = self._get_next_chunk(required_frames)
            if len(chunk) == 0:
                # End of audio
                self._playing = False
                if self._end_callback:
                    self._end_callback()
                break

            # Apply DSP processing
            chunk = self._process_chunk(chunk)

            # Ensure we have the right number of samples
            expected_samples = required_frames * self._num_channels
            if len(chunk) < expected_samples:
                # Pad with zeros
                chunk = np.pad(chunk, (0, expected_samples - len(chunk)))
            elif len(chunk) > expected_samples:
                chunk = chunk[:expected_samples]

            required_frames = yield chunk.astype(np.float32).tobytes()

    def _get_next_chunk(self, frames: int) -> NDArray[np.float32]:
        """Get next chunk of raw samples, handling A/B loop."""
        if self._samples is None:
            return np.array([], dtype=np.float32)

        start_sample = self._position * self._num_channels
        end_frame = self._position + frames

        # Handle A/B loop
        if self._loop_enabled and self._loop_b is not None and self._loop_a is not None:
            if end_frame >= self._loop_b:
                # Get samples up to loop point B
                samples_to_b = (self._loop_b - self._position) * self._num_channels
                chunk1 = self._samples[start_sample:start_sample + samples_to_b]

                # Wrap to loop point A
                self._position = self._loop_a
                remaining_frames = frames - (self._loop_b - self._position)

                if remaining_frames > 0:
                    start_sample = self._position * self._num_channels
                    chunk2 = self._samples[start_sample:start_sample + remaining_frames * self._num_channels]
                    self._position += remaining_frames
                    return np.concatenate([chunk1, chunk2])
                return chunk1

        # Normal playback
        end_sample = min(end_frame * self._num_channels, len(self._samples))
        chunk = self._samples[start_sample:end_sample]
        self._position = min(end_frame, self._total_frames)

        return chunk

    def _process_chunk(self, chunk: NDArray[np.float32]) -> NDArray[np.float32]:
        """Apply DSP chain: rate, pitch, EQ, fade, volume."""
        if len(chunk) == 0:
            return chunk

        # Reshape to (frames, channels) for processing
        frames = len(chunk) // self._num_channels
        if frames == 0:
            return chunk
        chunk = chunk.reshape(frames, self._num_channels)

        # Apply rate/pitch
        chunk = self._apply_rate_pitch(chunk)

        # Apply EQ
        chunk = self._apply_eq(chunk)

        # Apply fade
        chunk = self._apply_fade(chunk)

        # Apply volume
        vol = self._volume / 100.0
        chunk = chunk * vol

        # Clip to prevent distortion
        chunk = np.clip(chunk, -1.0, 1.0)

        # Flatten back to interleaved
        return chunk.flatten()

    def _apply_rate_pitch(self, chunk: NDArray[np.float32]) -> NDArray[np.float32]:
        """Apply rate and pitch changes."""
        if self._rate == 1.0 and self._pitch == 0.0:
            return chunk

        # Convert to mono for processing if needed, then back to stereo
        # Work on each channel separately
        left = chunk[:, 0]
        right = chunk[:, 1] if self._num_channels > 1 else left

        if HAS_LIBROSA and (self._rate != 1.0 or self._pitch != 0.0):
            # Independent rate and pitch using librosa
            if self._rate != 1.0:
                left = librosa.effects.time_stretch(left, rate=self._rate)
                if self._num_channels > 1:
                    right = librosa.effects.time_stretch(right, rate=self._rate)

            if self._pitch != 0.0:
                left = librosa.effects.pitch_shift(left, sr=self._sample_rate, n_steps=self._pitch)
                if self._num_channels > 1:
                    right = librosa.effects.pitch_shift(right, sr=self._sample_rate, n_steps=self._pitch)
        elif self._rate != 1.0:
            # Simple resampling (rate affects pitch - tape-style)
            new_len = int(len(left) / self._rate)
            if new_len > 0:
                left = scipy_signal.resample(left, new_len)
                if self._num_channels > 1:
                    right = scipy_signal.resample(right, new_len)

        # Reconstruct stereo
        if self._num_channels > 1:
            chunk = np.column_stack([left, right])
        else:
            chunk = left.reshape(-1, 1)

        return chunk.astype(np.float32)

    def _apply_eq(self, chunk: NDArray[np.float32]) -> NDArray[np.float32]:
        """Apply bass and treble EQ using shelving filters."""
        if self._bass == 0.0 and self._treble == 0.0:
            return chunk

        # Process each channel
        for ch in range(self._num_channels):
            channel_data = chunk[:, ch]

            if self._bass != 0.0:
                # Low shelf filter at 250Hz
                gain_linear = 10 ** (self._bass / 20)
                sos = self._design_shelf_filter(250, gain_linear, 'low')
                if self._bass_zi is None:
                    self._bass_zi = scipy_signal.sosfilt_zi(sos)
                    self._bass_zi = np.tile(self._bass_zi, (self._num_channels, 1, 1))
                channel_data, self._bass_zi[ch] = scipy_signal.sosfilt(
                    sos, channel_data, zi=self._bass_zi[ch]
                )

            if self._treble != 0.0:
                # High shelf filter at 4000Hz
                gain_linear = 10 ** (self._treble / 20)
                sos = self._design_shelf_filter(4000, gain_linear, 'high')
                if self._treble_zi is None:
                    self._treble_zi = scipy_signal.sosfilt_zi(sos)
                    self._treble_zi = np.tile(self._treble_zi, (self._num_channels, 1, 1))
                channel_data, self._treble_zi[ch] = scipy_signal.sosfilt(
                    sos, channel_data, zi=self._treble_zi[ch]
                )

            chunk[:, ch] = channel_data

        return chunk

    def _design_shelf_filter(self, freq: float, gain: float, shelf_type: str) -> NDArray:
        """Design a shelving filter using biquad coefficients."""
        # Simplified shelf filter using butterworth as approximation
        w0 = 2 * np.pi * freq / self._sample_rate
        if shelf_type == 'low':
            b, a = scipy_signal.butter(2, freq, btype='low', fs=self._sample_rate)
            # Apply gain to low frequencies
            b = b * gain
        else:  # high
            b, a = scipy_signal.butter(2, freq, btype='high', fs=self._sample_rate)
            # Apply gain to high frequencies
            b = b * gain

        # Convert to second-order sections
        sos = scipy_signal.tf2sos(b, a)
        return sos

    def _apply_fade(self, chunk: NDArray[np.float32]) -> NDArray[np.float32]:
        """Apply volume fade if active."""
        if self._fade_target is None:
            return chunk

        frames = len(chunk)
        elapsed_frames = self._position - self._fade_start_frame

        if elapsed_frames >= self._fade_total_frames:
            # Fade complete
            self._volume = self._fade_target
            self._fade_target = None
            return chunk

        # Calculate fade envelope
        start_progress = elapsed_frames / self._fade_total_frames
        end_progress = (elapsed_frames + frames) / self._fade_total_frames
        end_progress = min(end_progress, 1.0)

        vol_start = self._fade_start_vol + (self._fade_target - self._fade_start_vol) * start_progress
        vol_end = self._fade_start_vol + (self._fade_target - self._fade_start_vol) * end_progress

        # Create linear ramp
        fade_envelope = np.linspace(vol_start / 100.0, vol_end / 100.0, frames)
        fade_envelope = fade_envelope.reshape(-1, 1)

        # Update current volume for next chunk
        self._volume = int(vol_end)

        return chunk * fade_envelope

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
        info = miniaudio.mp3_get_file_info(str(path)) if path.suffix.lower() == '.mp3' else None
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
