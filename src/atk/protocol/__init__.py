"""ATK Protocol - JSON Lines IPC protocol for audio control."""

from .client import PipeClient
from .messages import (
    PROTOCOL_VERSION,
    ErrorCode,
    ErrorInfo,
    Event,
    EventType,
    PlaylistInfo,
    QueueInfo,
    RepeatMode,
    Request,
    Response,
    StatusInfo,
    TrackInfo,
    parse_message,
    serialize_message,
)

__all__ = [
    "PROTOCOL_VERSION",
    "Request",
    "Response",
    "Event",
    "ErrorInfo",
    "ErrorCode",
    "EventType",
    "RepeatMode",
    "TrackInfo",
    "QueueInfo",
    "StatusInfo",
    "PlaylistInfo",
    "parse_message",
    "serialize_message",
    "PipeClient",
]
