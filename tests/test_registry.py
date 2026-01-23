"""Tests for ATK Registry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from atk.daemon.registry import Registry, SessionContext
from atk.daemon.session import Session
from atk.protocol.messages import Request, Response


@pytest.fixture
def mock_session_context_start():
    """Mock SessionContext.start to avoid blocking pipe handlers."""
    async def mock_start(self):
        # Don't actually start the pipe handler
        pass

    async def mock_stop(self):
        # Don't try to stop non-existent handlers
        self.session.player.stop()

    with patch.object(SessionContext, "start", mock_start):
        with patch.object(SessionContext, "stop", mock_stop):
            yield


class TestRegistry:
    """Registry tests."""

    @pytest.fixture
    def registry(self, temp_runtime_dir, mock_miniaudio, mock_session_context_start):
        """Create a registry without starting pipes."""
        reg = Registry(temp_runtime_dir)
        reg.sessions_dir.mkdir(parents=True, exist_ok=True)
        return reg

    @pytest.mark.asyncio
    async def test_spawn_session(self, registry):
        """Test spawning a new session."""
        request = Request(cmd="spawn", args={"name": "test-session"})
        response = await registry._handle_request(request)

        assert response.ok is True
        assert response.data["name"] == "test-session"
        assert "pipes" in response.data
        assert "test-session" in registry._sessions

    @pytest.mark.asyncio
    async def test_spawn_auto_name(self, registry):
        """Test spawning session with auto-generated name."""
        request = Request(cmd="spawn", args={})
        response = await registry._handle_request(request)

        assert response.ok is True
        assert response.data["name"]
        assert len(response.data["name"]) > 0

    @pytest.mark.asyncio
    async def test_spawn_duplicate_error(self, registry):
        """Test spawning duplicate session returns error."""
        request1 = Request(cmd="spawn", args={"name": "dup"})
        await registry._handle_request(request1)

        request2 = Request(cmd="spawn", args={"name": "dup"})
        response = await registry._handle_request(request2)

        assert response.ok is False
        assert response.error.code.value == "SESSION_EXISTS"

    @pytest.mark.asyncio
    async def test_list_sessions(self, registry):
        """Test listing sessions."""
        await registry._handle_request(Request(cmd="spawn", args={"name": "music"}))
        await registry._handle_request(Request(cmd="spawn", args={"name": "alerts"}))

        request = Request(cmd="list")
        response = await registry._handle_request(request)

        assert response.ok is True
        sessions = response.data["sessions"]
        names = [s["name"] for s in sessions]
        assert "music" in names
        assert "alerts" in names

    @pytest.mark.asyncio
    async def test_list_empty(self, registry):
        """Test listing when no sessions exist."""
        request = Request(cmd="list")
        response = await registry._handle_request(request)

        assert response.ok is True
        assert response.data["sessions"] == []

    @pytest.mark.asyncio
    async def test_kill_session(self, registry):
        """Test killing a session."""
        await registry._handle_request(Request(cmd="spawn", args={"name": "tokill"}))
        assert "tokill" in registry._sessions

        request = Request(cmd="kill", args={"name": "tokill"})
        response = await registry._handle_request(request)

        assert response.ok is True
        assert "tokill" not in registry._sessions

    @pytest.mark.asyncio
    async def test_kill_nonexistent_error(self, registry):
        """Test killing nonexistent session returns error."""
        request = Request(cmd="kill", args={"name": "nonexistent"})
        response = await registry._handle_request(request)

        assert response.ok is False
        assert response.error.code.value == "SESSION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_unknown_command(self, registry):
        """Test unknown command returns error."""
        request = Request(cmd="unknown_command")
        response = await registry._handle_request(request)

        assert response.ok is False
        assert response.error.code.value == "UNKNOWN_COMMAND"


class TestSessionContext:
    """SessionContext tests."""

    @pytest.fixture
    def ctx(self, temp_runtime_dir, mock_miniaudio):
        """Create session context without starting pipes."""
        sessions_dir = temp_runtime_dir / "sessions"
        sessions_dir.mkdir(exist_ok=True)
        return SessionContext("test", sessions_dir)

    @pytest.mark.asyncio
    async def test_session_context_handles_commands(self, ctx):
        """Test session context processes commands."""
        request = Request(cmd="status")
        response = await ctx._handle_request(request)

        assert response.ok is True
        assert "state" in response.data

    @pytest.mark.asyncio
    async def test_session_context_volume(self, ctx):
        """Test session context handles volume command."""
        request = Request(cmd="volume", args={"level": 50})
        response = await ctx._handle_request(request)

        assert response.ok is True
        assert response.data["volume"] == 50

    @pytest.mark.asyncio
    async def test_session_context_invalid_command(self, ctx):
        """Test session context handles invalid commands."""
        request = Request(cmd="invalid_cmd")
        response = await ctx._handle_request(request)

        assert response.ok is False

    @pytest.mark.asyncio
    async def test_session_context_play_command(self, ctx, sample_audio_file):
        """Test session context handles play command."""
        request = Request(cmd="play", args={"file": str(sample_audio_file)})
        response = await ctx._handle_request(request)

        assert response.ok is True
        assert response.data["state"] == "playing"

    @pytest.mark.asyncio
    async def test_session_context_queue_commands(self, ctx, sample_audio_file):
        """Test session context handles queue commands."""
        request = Request(cmd="add", args={"uri": str(sample_audio_file)})
        response = await ctx._handle_request(request)
        assert response.ok is True

        request = Request(cmd="queue")
        response = await ctx._handle_request(request)
        assert response.ok is True
        assert len(response.data["tracks"]) == 1

    @pytest.mark.asyncio
    async def test_session_context_shuffle_repeat(self, ctx):
        """Test session context handles shuffle and repeat."""
        request = Request(cmd="shuffle", args={"enabled": True})
        response = await ctx._handle_request(request)
        assert response.ok is True
        assert response.data["shuffle"] is True

        request = Request(cmd="repeat", args={"mode": "track"})
        response = await ctx._handle_request(request)
        assert response.ok is True
        assert response.data["repeat"] == "track"

    @pytest.mark.asyncio
    async def test_session_context_clear(self, ctx, sample_audio_file):
        """Test session context handles clear command."""
        await ctx._handle_request(Request(cmd="add", args={"uri": str(sample_audio_file)}))
        await ctx._handle_request(Request(cmd="add", args={"uri": str(sample_audio_file)}))

        request = Request(cmd="clear")
        response = await ctx._handle_request(request)

        assert response.ok is True
        assert response.data["cleared"] is True

    @pytest.mark.asyncio
    async def test_session_context_pause_stop(self, ctx, sample_audio_file):
        """Test session context handles pause and stop."""
        await ctx._handle_request(Request(cmd="play", args={"file": str(sample_audio_file)}))

        request = Request(cmd="pause")
        response = await ctx._handle_request(request)
        assert response.ok is True
        assert response.data["state"] == "paused"

        request = Request(cmd="stop")
        response = await ctx._handle_request(request)
        assert response.ok is True
        assert response.data["state"] == "stopped"
