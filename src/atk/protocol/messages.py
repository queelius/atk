"""Protocol message definitions for ATK IPC."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


PROTOCOL_VERSION = 1


class ErrorCode(str, Enum):
    """Error codes for protocol responses."""

    # IO errors
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    READ_ERROR = "READ_ERROR"

    # Session errors
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_EXISTS = "SESSION_EXISTS"

    # Playback errors
    INVALID_FORMAT = "INVALID_FORMAT"
    DECODE_ERROR = "DECODE_ERROR"
    STREAM_ERROR = "STREAM_ERROR"

    # Protocol errors
    INVALID_MESSAGE = "INVALID_MESSAGE"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    INVALID_ARGS = "INVALID_ARGS"

    # Queue errors
    QUEUE_EMPTY = "QUEUE_EMPTY"
    INVALID_INDEX = "INVALID_INDEX"


class EventType(str, Enum):
    """Event types emitted by sessions."""

    TRACK_CHANGED = "track_changed"
    PLAYBACK_STARTED = "playback_started"
    PLAYBACK_PAUSED = "playback_paused"
    PLAYBACK_STOPPED = "playback_stopped"
    QUEUE_UPDATED = "queue_updated"
    POSITION_UPDATE = "position_update"
    QUEUE_FINISHED = "queue_finished"
    ERROR = "error"


class RepeatMode(str, Enum):
    """Repeat modes for playback."""

    NONE = "none"
    QUEUE = "queue"
    TRACK = "track"


@dataclass
class ErrorInfo:
    """Error information in response."""

    code: ErrorCode
    category: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value if isinstance(self.code, ErrorCode) else self.code,
            "category": self.category,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ErrorInfo:
        return cls(
            code=ErrorCode(data["code"]) if data["code"] in [e.value for e in ErrorCode] else data["code"],
            category=data["category"],
            message=data["message"],
        )


@dataclass
class TrackInfo:
    """Track metadata."""

    uri: str
    title: str | None = None
    artist: str | None = None
    album: str | None = None
    duration: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackInfo:
        return cls(**data)


@dataclass
class SessionInfo:
    """Session summary for registry list."""

    name: str
    state: str  # "playing", "paused", "stopped"
    track: TrackInfo | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "track": self.track.to_dict() if self.track else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionInfo:
        return cls(
            name=data["name"],
            state=data["state"],
            track=TrackInfo.from_dict(data["track"]) if data.get("track") else None,
        )


@dataclass
class QueueInfo:
    """Queue contents."""

    tracks: list[TrackInfo]
    current_index: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tracks": [t.to_dict() for t in self.tracks],
            "current_index": self.current_index,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueueInfo:
        return cls(
            tracks=[TrackInfo.from_dict(t) for t in data["tracks"]],
            current_index=data["current_index"],
        )


@dataclass
class StatusInfo:
    """Full session status."""

    state: str
    track: TrackInfo | None
    position: float
    duration: float
    volume: int
    shuffle: bool
    repeat: RepeatMode
    queue_length: int
    queue_position: int
    # DSP parameters
    rate: float = 1.0
    pitch: float = 0.0
    bass: float = 0.0
    treble: float = 0.0
    loop_a: float | None = None
    loop_b: float | None = None
    loop_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "track": self.track.to_dict() if self.track else None,
            "position": self.position,
            "duration": self.duration,
            "volume": self.volume,
            "shuffle": self.shuffle,
            "repeat": self.repeat.value,
            "queue_length": self.queue_length,
            "queue_position": self.queue_position,
            "rate": self.rate,
            "pitch": self.pitch,
            "bass": self.bass,
            "treble": self.treble,
            "loop_a": self.loop_a,
            "loop_b": self.loop_b,
            "loop_enabled": self.loop_enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StatusInfo:
        return cls(
            state=data["state"],
            track=TrackInfo.from_dict(data["track"]) if data.get("track") else None,
            position=data["position"],
            duration=data["duration"],
            volume=data["volume"],
            shuffle=data["shuffle"],
            repeat=RepeatMode(data["repeat"]),
            queue_length=data["queue_length"],
            queue_position=data["queue_position"],
            rate=data.get("rate", 1.0),
            pitch=data.get("pitch", 0.0),
            bass=data.get("bass", 0.0),
            treble=data.get("treble", 0.0),
            loop_a=data.get("loop_a"),
            loop_b=data.get("loop_b"),
            loop_enabled=data.get("loop_enabled", False),
        )


@dataclass
class Request:
    """Protocol request message."""

    cmd: str
    args: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    v: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "id": self.id,
            "cmd": self.cmd,
            "args": self.args,
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Request:
        return cls(
            v=data.get("v", PROTOCOL_VERSION),
            id=data.get("id", str(uuid.uuid4())),
            cmd=data["cmd"],
            args=data.get("args", {}),
        )


@dataclass
class Response:
    """Protocol response message."""

    id: str
    ok: bool
    data: dict[str, Any] | None = None
    error: ErrorInfo | None = None
    v: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "v": self.v,
            "id": self.id,
            "ok": self.ok,
        }
        if self.ok:
            result["data"] = self.data or {}
        else:
            result["error"] = self.error.to_dict() if self.error else {}
        return result

    def serialize(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def success(cls, request_id: str, data: dict[str, Any] | None = None) -> Response:
        return cls(id=request_id, ok=True, data=data)

    @classmethod
    def failure(cls, request_id: str, error: ErrorInfo) -> Response:
        return cls(id=request_id, ok=False, error=error)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Response:
        return cls(
            v=data.get("v", PROTOCOL_VERSION),
            id=data["id"],
            ok=data["ok"],
            data=data.get("data"),
            error=ErrorInfo.from_dict(data["error"]) if data.get("error") else None,
        )


@dataclass
class Event:
    """Protocol event message (pushed to subscribers)."""

    event: EventType
    data: dict[str, Any] = field(default_factory=dict)
    v: int = PROTOCOL_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "event": self.event.value,
            "data": self.data,
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            v=data.get("v", PROTOCOL_VERSION),
            event=EventType(data["event"]),
            data=data.get("data", {}),
        )


def parse_message(line: str) -> Request | Response | Event:
    """Parse a JSON line into appropriate message type."""
    data = json.loads(line)

    if "cmd" in data:
        return Request.from_dict(data)
    elif "event" in data:
        return Event.from_dict(data)
    elif "ok" in data:
        return Response.from_dict(data)
    else:
        raise ValueError(f"Unknown message type: {line}")


def serialize_message(msg: Request | Response | Event) -> str:
    """Serialize a message to JSON line."""
    return msg.serialize()
