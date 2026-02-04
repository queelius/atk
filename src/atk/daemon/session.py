"""Playback session management."""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from ..protocol.messages import (
    ErrorCode,
    Event,
    EventType,
    RepeatMode,
    StatusInfo,
    TrackInfo,
)
from .player import Player, get_track_duration, is_supported_format

_logger = logging.getLogger("atk.session")


class PlaybackState(str, Enum):
    """Playback state."""

    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass
class Session:
    """Audio playback session with queue and state management."""

    name: str
    player: Player = field(default_factory=Player)
    state: PlaybackState = PlaybackState.STOPPED
    queue: list[str] = field(default_factory=list)
    queue_position: int = 0
    shuffle: bool = False
    shuffle_order: list[int] = field(default_factory=list)
    repeat: RepeatMode = RepeatMode.NONE
    volume: int = 80
    position: float = 0.0
    rate: float = 1.0
    _event_callback: Callable[[Event], None] | None = None
    _position_task: asyncio.Task | None = None

    def __post_init__(self):
        self.player.set_end_callback(self._on_track_end)
        self.player.set_volume(self.volume)

    def set_event_callback(self, callback: Callable[[Event], None]) -> None:
        """Set callback for emitting events."""
        self._event_callback = callback

    def _emit(self, event_type: EventType, data: dict | None = None) -> None:
        """Emit an event."""
        if self._event_callback:
            self._event_callback(Event(event=event_type, data=data or {}))

    def _on_track_end(self) -> None:
        """Handle track ending."""
        task = asyncio.create_task(self._handle_track_end())
        # Add exception handler to prevent unhandled exceptions (Bug fix 6.2)
        task.add_done_callback(self._handle_task_exception)

    def _handle_task_exception(self, task: asyncio.Task) -> None:
        """Log exceptions from background tasks."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            _logger.error(f"Background task error: {exc}", exc_info=exc)

    async def _handle_track_end(self) -> None:
        """Process track end - advance queue or repeat."""
        if self.repeat == RepeatMode.TRACK:
            # Repeat current track
            await self._play_current()
            return

        # Try to advance to next track
        if not self._advance_queue():
            # No more tracks
            self.state = PlaybackState.STOPPED
            self.position = 0.0
            self._emit(EventType.QUEUE_FINISHED)
            return

        await self._play_current()

    def _advance_queue(self) -> bool:
        """Advance to next track in queue. Returns False if queue exhausted."""
        if not self.queue:
            return False

        if self.shuffle:
            # Bug fix 6.1: Handle ValueError when queue_position not in shuffle_order
            try:
                current_shuffle_idx = self.shuffle_order.index(self.queue_position)
            except ValueError:
                # Position not in shuffle order, fallback to linear
                _logger.warning(
                    "Queue position not in shuffle order, falling back to linear"
                )
                return self._advance_linear()

            next_shuffle_idx = current_shuffle_idx + 1

            if next_shuffle_idx >= len(self.shuffle_order):
                if self.repeat == RepeatMode.QUEUE:
                    # Reshuffle and start over
                    self._regenerate_shuffle()
                    next_shuffle_idx = 0
                else:
                    return False

            self.queue_position = self.shuffle_order[next_shuffle_idx]
        else:
            return self._advance_linear()

        return True

    def _advance_linear(self) -> bool:
        """Advance to next track in linear order."""
        next_pos = self.queue_position + 1
        if next_pos >= len(self.queue):
            if self.repeat == RepeatMode.QUEUE:
                next_pos = 0
            else:
                return False
        self.queue_position = next_pos
        return True

    def _go_previous(self) -> bool:
        """Go to previous track. Returns False if at start."""
        if not self.queue:
            return False

        if self.shuffle:
            # Bug fix 6.1: Handle ValueError when queue_position not in shuffle_order
            try:
                current_shuffle_idx = self.shuffle_order.index(self.queue_position)
            except ValueError:
                # Position not in shuffle order, fallback to linear
                _logger.warning(
                    "Queue position not in shuffle order, falling back to linear"
                )
                return self._go_previous_linear()

            prev_shuffle_idx = current_shuffle_idx - 1

            if prev_shuffle_idx < 0:
                if self.repeat == RepeatMode.QUEUE:
                    prev_shuffle_idx = len(self.shuffle_order) - 1
                else:
                    return False

            self.queue_position = self.shuffle_order[prev_shuffle_idx]
        else:
            return self._go_previous_linear()

        return True

    def _go_previous_linear(self) -> bool:
        """Go to previous track in linear order."""
        prev_pos = self.queue_position - 1
        if prev_pos < 0:
            if self.repeat == RepeatMode.QUEUE:
                prev_pos = len(self.queue) - 1
            else:
                return False
        self.queue_position = prev_pos
        return True

    def _regenerate_shuffle(self) -> None:
        """Regenerate shuffle order."""
        self.shuffle_order = list(range(len(self.queue)))
        random.shuffle(self.shuffle_order)

    async def _play_current(self) -> None:
        """Play the current track in queue."""
        if not self.queue or self.queue_position >= len(self.queue):
            return

        uri = self.queue[self.queue_position]

        try:
            self.player.load(uri)
            self.player.play()
            self.state = PlaybackState.PLAYING
            self.position = 0.0

            track_info = self._get_track_info(uri)
            self._emit(
                EventType.TRACK_CHANGED,
                {"track": track_info.to_dict(), "queue_position": self.queue_position},
            )
            self._emit(EventType.PLAYBACK_STARTED, {"track": track_info.to_dict()})

        except FileNotFoundError:
            self._emit(
                EventType.ERROR,
                {
                    "code": ErrorCode.FILE_NOT_FOUND.value,
                    "message": f"File not found: {uri}",
                    "track": uri,
                },
            )
            # Skip to next track
            if self._advance_queue():
                await self._play_current()

        except Exception as e:
            self._emit(
                EventType.ERROR,
                {
                    "code": ErrorCode.DECODE_ERROR.value,
                    "message": str(e),
                    "track": uri,
                },
            )
            # Skip to next track
            if self._advance_queue():
                await self._play_current()

    def _get_track_info(self, uri: str) -> TrackInfo:
        """Get track metadata."""
        duration = get_track_duration(uri)
        path = Path(uri)

        # Try to extract artist/title from filename
        name = path.stem
        parts = name.split(" - ", 1)
        if len(parts) == 2:
            artist, title = parts
        else:
            artist = None
            title = name

        return TrackInfo(
            uri=uri,
            title=title,
            artist=artist,
            duration=duration,
        )

    async def start_position_updates(self, interval: float = 1.0) -> None:
        """Start emitting position updates."""
        if self._position_task:
            return

        async def update_loop():
            while True:
                await asyncio.sleep(interval)
                if self.state == PlaybackState.PLAYING:
                    self.position = self.player.get_position()
                    current = self.queue[self.queue_position] if self.queue else None
                    duration = get_track_duration(current) if current else 0.0
                    self._emit(
                        EventType.POSITION_UPDATE,
                        {"position": self.position, "duration": duration or 0.0},
                    )

        self._position_task = asyncio.create_task(update_loop())
        # Add exception handler (Bug fix 6.2)
        self._position_task.add_done_callback(self._handle_task_exception)

    async def stop_position_updates(self) -> None:
        """Stop position updates."""
        if self._position_task:
            self._position_task.cancel()
            try:
                await self._position_task
            except asyncio.CancelledError:
                pass
            self._position_task = None

    # Command handlers

    async def cmd_play(self, file: str | None = None) -> dict:
        """Play a file or resume playback."""
        if file:
            # Add file and play it
            if not is_supported_format(file):
                raise ValueError(f"Unsupported format: {file}")

            self.queue.append(file)
            self.queue_position = len(self.queue) - 1

            if self.shuffle:
                # Bug fix 6.4: Insert at random position after current, not append
                if self.shuffle_order:
                    try:
                        current_idx = (
                            self.shuffle_order.index(self.queue_position - 1)
                            if self.queue_position > 0
                            else -1
                        )
                        insert_pos = random.randint(
                            current_idx + 1, len(self.shuffle_order)
                        )
                    except ValueError:
                        insert_pos = len(self.shuffle_order)
                else:
                    insert_pos = 0
                self.shuffle_order.insert(insert_pos, len(self.queue) - 1)

            self._emit(EventType.QUEUE_UPDATED, {"queue": self._get_queue_data()})
            await self._play_current()
        elif self.state == PlaybackState.PAUSED:
            # Resume
            self.player.unpause()
            self.state = PlaybackState.PLAYING
            self._emit(EventType.PLAYBACK_STARTED, {})
        elif self.state == PlaybackState.STOPPED and self.queue:
            # Start from current position
            await self._play_current()

        return {"state": self.state.value}

    async def cmd_pause(self) -> dict:
        """Pause playback."""
        if self.state == PlaybackState.PLAYING:
            self.player.pause()
            self.state = PlaybackState.PAUSED
            self.position = self.player.get_position()
            self._emit(EventType.PLAYBACK_PAUSED, {"position": self.position})
        return {"state": self.state.value}

    async def cmd_stop(self) -> dict:
        """Stop playback and reset position."""
        self.player.stop()
        self.state = PlaybackState.STOPPED
        self.position = 0.0
        self._emit(EventType.PLAYBACK_STOPPED)
        return {"state": self.state.value}

    async def cmd_next(self) -> dict:
        """Skip to next track."""
        if self._advance_queue():
            await self._play_current()
            return {"queue_position": self.queue_position}
        else:
            return {"error": "End of queue"}

    async def cmd_prev(self) -> dict:
        """Go to previous track."""
        if self._go_previous():
            await self._play_current()
            return {"queue_position": self.queue_position}
        else:
            return {"error": "Start of queue"}

    async def cmd_seek(self, pos: float) -> dict:
        """Seek to position (absolute or relative with +/-)."""
        if isinstance(pos, str):
            if pos.startswith("+"):
                pos = self.position + float(pos[1:])
            elif pos.startswith("-"):
                pos = self.position - float(pos[1:])
            else:
                pos = float(pos)

        pos = max(0.0, pos)
        self.player.seek(pos)
        self.position = pos
        return {"position": self.position}

    async def cmd_volume(self, level: int) -> dict:
        """Set volume (0-100)."""
        self.volume = max(0, min(100, level))
        self.player.set_volume(self.volume)
        return {"volume": self.volume}

    async def cmd_add(self, uri: str) -> dict:
        """Add track to queue."""
        if not is_supported_format(uri):
            raise ValueError(f"Unsupported format: {uri}")

        self.queue.append(uri)
        if self.shuffle:
            # Bug fix 6.4: Insert at random position after current, not append
            if self.shuffle_order:
                try:
                    current_idx = self.shuffle_order.index(self.queue_position)
                    insert_pos = random.randint(
                        current_idx + 1, len(self.shuffle_order)
                    )
                except ValueError:
                    insert_pos = len(self.shuffle_order)
            else:
                insert_pos = 0
            self.shuffle_order.insert(insert_pos, len(self.queue) - 1)

        self._emit(EventType.QUEUE_UPDATED, {"queue": self._get_queue_data()})
        return {"queue_length": len(self.queue)}

    async def cmd_remove(self, index: int) -> dict:
        """Remove track from queue."""
        if index < 0 or index >= len(self.queue):
            raise IndexError(f"Invalid queue index: {index}")

        removed = self.queue.pop(index)

        # Adjust queue position if needed
        if index < self.queue_position:
            self.queue_position -= 1
        elif index == self.queue_position:
            # Currently playing track removed
            if self.state == PlaybackState.PLAYING:
                if self.queue_position < len(self.queue):
                    await self._play_current()
                else:
                    self.player.stop()
                    self.state = PlaybackState.STOPPED

        # Update shuffle order
        if self.shuffle and self.shuffle_order:
            try:
                self.shuffle_order.remove(index)
            except ValueError:
                pass
            self.shuffle_order = [i if i < index else i - 1 for i in self.shuffle_order]

        self._emit(EventType.QUEUE_UPDATED, {"queue": self._get_queue_data()})
        return {"removed": removed}

    async def cmd_move(self, from_idx: int, to_idx: int) -> dict:
        """Move track in queue."""
        if from_idx < 0 or from_idx >= len(self.queue):
            raise IndexError(f"Invalid from index: {from_idx}")
        if to_idx < 0 or to_idx >= len(self.queue):
            raise IndexError(f"Invalid to index: {to_idx}")

        track = self.queue.pop(from_idx)
        self.queue.insert(to_idx, track)

        # Adjust queue position
        if from_idx == self.queue_position:
            self.queue_position = to_idx
        elif from_idx < self.queue_position <= to_idx:
            self.queue_position -= 1
        elif to_idx <= self.queue_position < from_idx:
            self.queue_position += 1

        self._emit(EventType.QUEUE_UPDATED, {"queue": self._get_queue_data()})
        return {"queue_position": self.queue_position}

    async def cmd_clear(self) -> dict:
        """Clear the queue."""
        self.player.stop()
        self.state = PlaybackState.STOPPED
        self.queue.clear()
        self.queue_position = 0
        self.shuffle_order.clear()
        self.position = 0.0
        self._emit(EventType.QUEUE_UPDATED, {"queue": self._get_queue_data()})
        return {"cleared": True}

    async def cmd_shuffle(self, enabled: bool) -> dict:
        """Enable or disable shuffle mode."""
        self.shuffle = enabled
        if enabled:
            self._regenerate_shuffle()
            # Put current track first in shuffle
            if self.queue_position in self.shuffle_order:
                self.shuffle_order.remove(self.queue_position)
                self.shuffle_order.insert(0, self.queue_position)
        else:
            self.shuffle_order.clear()
        return {"shuffle": self.shuffle}

    async def cmd_repeat(self, mode: str) -> dict:
        """Set repeat mode."""
        self.repeat = RepeatMode(mode)
        return {"repeat": self.repeat.value}

    async def cmd_rate(self, speed: float) -> dict:
        """Set playback rate (0.25 to 4.0)."""
        self.rate = max(0.25, min(4.0, speed))
        self.player.set_rate(self.rate)
        return {"rate": self.rate}

    async def cmd_jump(self, index: int) -> dict:
        """Jump to track at index in queue."""
        if index < 0 or index >= len(self.queue):
            raise IndexError(f"Invalid queue index: {index}")

        self.queue_position = index
        await self._play_current()
        return {"queue_position": self.queue_position}

    async def cmd_queue(self) -> dict:
        """Get queue contents."""
        return self._get_queue_data()

    async def cmd_status(self) -> dict:
        """Get current playback status."""
        current_uri = self.queue[self.queue_position] if self.queue else None
        track_info = self._get_track_info(current_uri) if current_uri else None

        status = StatusInfo(
            state=self.state.value,
            track=track_info,
            position=self.position if self.state != PlaybackState.STOPPED else 0.0,
            duration=track_info.duration or 0.0 if track_info else 0.0,
            volume=self.volume,
            shuffle=self.shuffle,
            repeat=self.repeat,
            queue_length=len(self.queue),
            queue_position=self.queue_position,
            rate=self.rate,
        )
        return status.to_dict()

    async def cmd_info(self, index: int | None = None) -> dict:
        """Get track metadata."""
        if index is None:
            index = self.queue_position

        if index < 0 or index >= len(self.queue):
            raise IndexError(f"Invalid index: {index}")

        uri = self.queue[index]
        return self._get_track_info(uri).to_dict()

    def _get_queue_data(self) -> dict:
        """Get queue as serializable dict."""
        return {
            "tracks": [self._get_track_info(uri).to_dict() for uri in self.queue],
            "current_index": self.queue_position,
        }

    def to_dict(self) -> dict:
        """Serialize session state for persistence."""
        return {
            "name": self.name,
            "queue": self.queue,
            "position": self.position,
            "current_index": self.queue_position,
            "shuffle": self.shuffle,
            "shuffle_order": self.shuffle_order,
            "repeat": self.repeat.value,
            "volume": self.volume,
            "rate": self.rate,
        }

    @classmethod
    def from_dict(cls, data: dict, name: str | None = None) -> Session:
        """Restore session from serialized state."""
        session = cls(name=name or data["name"])
        session.queue = data.get("queue", [])
        session.position = data.get("position", 0.0)
        session.queue_position = data.get("current_index", 0)
        session.shuffle = data.get("shuffle", False)
        session.shuffle_order = data.get("shuffle_order", [])
        session.repeat = RepeatMode(data.get("repeat", "none"))
        session.volume = data.get("volume", 80)
        session.player.set_volume(session.volume)
        session.rate = data.get("rate", 1.0)
        session.player.set_rate(session.rate)
        return session
