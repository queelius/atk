"""ATK Protocol - JSON Lines IPC protocol for audio control."""

from .messages import (
    PROTOCOL_VERSION,
    Request,
    Response,
    Event,
    ErrorInfo,
    ErrorCode,
    EventType,
    RepeatMode,
    TrackInfo,
    SessionInfo,
    QueueInfo,
    StatusInfo,
    parse_message,
    serialize_message,
)
from .client import PipeClient

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
    "SessionInfo",
    "QueueInfo",
    "StatusInfo",
    "parse_message",
    "serialize_message",
    "PipeClient",
]
