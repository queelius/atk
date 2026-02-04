"""Tests for ATK protocol messages."""

from __future__ import annotations

import json

import pytest

from atk.protocol.messages import (
    PROTOCOL_VERSION,
    ErrorCode,
    ErrorInfo,
    Event,
    EventType,
    PlaylistInfo,
    RepeatMode,
    Request,
    Response,
    StatusInfo,
    TrackInfo,
    parse_message,
)


class TestRequest:
    """Tests for Request message."""

    def test_create_request(self):
        """Test creating a request."""
        req = Request(cmd="play", args={"file": "/path/to/file.mp3"})

        assert req.cmd == "play"
        assert req.args == {"file": "/path/to/file.mp3"}
        assert req.v == PROTOCOL_VERSION
        assert req.id  # Should have generated UUID

    def test_request_to_dict(self):
        """Test request serialization."""
        req = Request(cmd="pause", args={}, id="test-id")
        data = req.to_dict()

        assert data["v"] == 1
        assert data["id"] == "test-id"
        assert data["cmd"] == "pause"
        assert data["args"] == {}

    def test_request_from_dict(self):
        """Test request deserialization."""
        data = {"v": 1, "id": "abc", "cmd": "stop", "args": {}}
        req = Request.from_dict(data)

        assert req.v == 1
        assert req.id == "abc"
        assert req.cmd == "stop"

    def test_request_serialize_json(self):
        """Test request JSON serialization."""
        req = Request(cmd="volume", args={"level": 50}, id="test")
        json_str = req.serialize()
        data = json.loads(json_str)

        assert data["cmd"] == "volume"
        assert data["args"]["level"] == 50


class TestResponse:
    """Tests for Response message."""

    def test_success_response(self):
        """Test creating success response."""
        resp = Response.success("req-123", {"state": "playing"})

        assert resp.ok is True
        assert resp.id == "req-123"
        assert resp.data == {"state": "playing"}
        assert resp.error is None

    def test_failure_response(self):
        """Test creating failure response."""
        error = ErrorInfo(
            code=ErrorCode.FILE_NOT_FOUND,
            category="io",
            message="File not found: /test.mp3",
        )
        resp = Response.failure("req-456", error)

        assert resp.ok is False
        assert resp.error.code == ErrorCode.FILE_NOT_FOUND
        assert resp.error.category == "io"

    def test_response_to_dict_success(self):
        """Test success response serialization."""
        resp = Response.success("id", {"volume": 80})
        data = resp.to_dict()

        assert data["ok"] is True
        assert data["data"]["volume"] == 80
        assert "error" not in data

    def test_response_to_dict_failure(self):
        """Test failure response serialization."""
        error = ErrorInfo(
            code=ErrorCode.INVALID_ARGS,
            category="protocol",
            message="Missing required arg",
        )
        resp = Response.failure("id", error)
        data = resp.to_dict()

        assert data["ok"] is False
        assert data["error"]["code"] == "INVALID_ARGS"
        assert "data" not in data

    def test_response_from_dict(self):
        """Test response deserialization."""
        data = {"v": 1, "id": "test", "ok": True, "data": {"result": "ok"}}
        resp = Response.from_dict(data)

        assert resp.ok is True
        assert resp.data["result"] == "ok"


class TestEvent:
    """Tests for Event message."""

    def test_create_event(self):
        """Test creating an event."""
        event = Event(
            event=EventType.TRACK_CHANGED,
            data={"track": {"uri": "/test.mp3"}},
        )

        assert event.event == EventType.TRACK_CHANGED
        assert event.data["track"]["uri"] == "/test.mp3"
        assert event.v == PROTOCOL_VERSION

    def test_event_to_dict(self):
        """Test event serialization."""
        event = Event(event=EventType.PLAYBACK_PAUSED, data={"position": 45.5})
        data = event.to_dict()

        assert data["event"] == "playback_paused"
        assert data["data"]["position"] == 45.5

    def test_event_from_dict(self):
        """Test event deserialization."""
        data = {"v": 1, "event": "queue_updated", "data": {"queue": []}}
        event = Event.from_dict(data)

        assert event.event == EventType.QUEUE_UPDATED


class TestTrackInfo:
    """Tests for TrackInfo."""

    def test_track_info_minimal(self):
        """Test track info with minimal data."""
        track = TrackInfo(uri="/path/to/track.mp3")

        assert track.uri == "/path/to/track.mp3"
        assert track.title is None
        assert track.artist is None

    def test_track_info_full(self):
        """Test track info with all fields."""
        track = TrackInfo(
            uri="/music/song.mp3",
            title="Test Song",
            artist="Test Artist",
            album="Test Album",
            duration=180.5,
        )

        assert track.title == "Test Song"
        assert track.duration == 180.5

    def test_track_info_to_dict_excludes_none(self):
        """Test that None values are excluded from dict."""
        track = TrackInfo(uri="/test.mp3", title="Title")
        data = track.to_dict()

        assert "uri" in data
        assert "title" in data
        assert "artist" not in data  # Should be excluded


class TestStatusInfo:
    """Tests for StatusInfo."""

    def test_status_info(self):
        """Test status info creation."""
        status = StatusInfo(
            state="playing",
            track=TrackInfo(uri="/test.mp3"),
            position=30.0,
            duration=180.0,
            volume=80,
            shuffle=True,
            repeat=RepeatMode.QUEUE,
            queue_length=5,
            queue_position=2,
        )

        assert status.state == "playing"
        assert status.shuffle is True
        assert status.repeat == RepeatMode.QUEUE

    def test_status_info_to_dict(self):
        """Test status info serialization."""
        status = StatusInfo(
            state="paused",
            track=None,
            position=0.0,
            duration=0.0,
            volume=50,
            shuffle=False,
            repeat=RepeatMode.NONE,
            queue_length=0,
            queue_position=0,
        )
        data = status.to_dict()

        assert data["state"] == "paused"
        assert data["repeat"] == "none"
        assert data["track"] is None

    def test_status_info_with_rate(self):
        """Test status info with rate parameter."""
        status = StatusInfo(
            state="playing",
            track=None,
            position=0.0,
            duration=0.0,
            volume=80,
            shuffle=False,
            repeat=RepeatMode.NONE,
            queue_length=0,
            queue_position=0,
            rate=1.5,
        )
        data = status.to_dict()

        assert data["rate"] == 1.5


class TestPlaylistInfo:
    """Tests for PlaylistInfo."""

    def test_playlist_info(self):
        """Test playlist info creation."""
        pl = PlaylistInfo(name="favorites", track_count=10, format="json")

        assert pl.name == "favorites"
        assert pl.track_count == 10
        assert pl.format == "json"

    def test_playlist_info_to_dict(self):
        """Test playlist info serialization."""
        pl = PlaylistInfo(name="test", track_count=5)
        data = pl.to_dict()

        assert data["name"] == "test"
        assert data["track_count"] == 5
        assert data["format"] == "json"  # default


class TestParseMessage:
    """Tests for message parsing."""

    def test_parse_request(self):
        """Test parsing request message."""
        line = '{"v": 1, "id": "abc", "cmd": "play", "args": {}}'
        msg = parse_message(line)

        assert isinstance(msg, Request)
        assert msg.cmd == "play"

    def test_parse_response(self):
        """Test parsing response message."""
        line = '{"v": 1, "id": "abc", "ok": true, "data": {}}'
        msg = parse_message(line)

        assert isinstance(msg, Response)
        assert msg.ok is True

    def test_parse_event(self):
        """Test parsing event message."""
        line = '{"v": 1, "event": "playback_stopped", "data": {}}'
        msg = parse_message(line)

        assert isinstance(msg, Event)
        assert msg.event == EventType.PLAYBACK_STOPPED

    def test_parse_invalid_json(self):
        """Test parsing invalid JSON raises error."""
        with pytest.raises(json.JSONDecodeError):
            parse_message("not valid json")

    def test_parse_unknown_message_type(self):
        """Test parsing unknown message type raises error."""
        with pytest.raises(ValueError, match="Unknown message type"):
            parse_message('{"v": 1, "unknown": "field"}')


class TestErrorCode:
    """Tests for error codes."""

    def test_error_codes_are_strings(self):
        """Test that error codes are string enums."""
        assert ErrorCode.FILE_NOT_FOUND.value == "FILE_NOT_FOUND"
        assert str(ErrorCode.INVALID_ARGS) == "ErrorCode.INVALID_ARGS"

    def test_error_info_to_dict(self):
        """Test error info serialization."""
        error = ErrorInfo(
            code=ErrorCode.QUEUE_EMPTY,
            category="queue",
            message="Queue is empty",
        )
        data = error.to_dict()

        assert data["code"] == "QUEUE_EMPTY"
        assert data["category"] == "queue"


class TestRepeatMode:
    """Tests for repeat mode enum."""

    def test_repeat_modes(self):
        """Test repeat mode values."""
        assert RepeatMode.NONE.value == "none"
        assert RepeatMode.QUEUE.value == "queue"
        assert RepeatMode.TRACK.value == "track"

    def test_repeat_mode_from_string(self):
        """Test creating repeat mode from string."""
        mode = RepeatMode("queue")
        assert mode == RepeatMode.QUEUE
