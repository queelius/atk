"""Tests for ATK Session."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from atk.daemon.session import Session, PlaybackState
from atk.protocol.messages import RepeatMode, Event, EventType


class TestSessionBasic:
    """Basic session tests."""

    def test_create_session(self, mock_miniaudio):
        """Test creating a session."""
        session = Session(name="test")

        assert session.name == "test"
        assert session.state == PlaybackState.STOPPED
        assert session.queue == []
        assert session.volume == 80

    def test_session_defaults(self, mock_miniaudio):
        """Test session default values."""
        session = Session(name="test")

        assert session.shuffle is False
        assert session.repeat == RepeatMode.NONE
        assert session.queue_position == 0
        assert session.position == 0.0
        # DSP defaults
        assert session.rate == 1.0
        assert session.pitch == 0.0
        assert session.bass == 0.0
        assert session.treble == 0.0
        assert session.loop_enabled is False


class TestSessionQueue:
    """Queue management tests."""

    @pytest.mark.asyncio
    async def test_add_to_queue(self, mock_miniaudio, sample_audio_file):
        """Test adding tracks to queue."""
        session = Session(name="test")
        result = await session.cmd_add(str(sample_audio_file))

        assert result["queue_length"] == 1
        assert len(session.queue) == 1
        assert session.queue[0] == str(sample_audio_file)

    @pytest.mark.asyncio
    async def test_remove_from_queue(self, mock_miniaudio, sample_audio_file):
        """Test removing tracks from queue."""
        session = Session(name="test")
        await session.cmd_add(str(sample_audio_file))
        await session.cmd_add(str(sample_audio_file))

        result = await session.cmd_remove(0)

        assert "removed" in result
        assert len(session.queue) == 1

    @pytest.mark.asyncio
    async def test_remove_invalid_index(self, mock_miniaudio):
        """Test removing with invalid index raises error."""
        session = Session(name="test")

        with pytest.raises(IndexError):
            await session.cmd_remove(0)

    @pytest.mark.asyncio
    async def test_clear_queue(self, mock_miniaudio, sample_audio_file):
        """Test clearing queue."""
        session = Session(name="test")
        await session.cmd_add(str(sample_audio_file))
        await session.cmd_add(str(sample_audio_file))

        result = await session.cmd_clear()

        assert result["cleared"] is True
        assert len(session.queue) == 0
        assert session.state == PlaybackState.STOPPED

    @pytest.mark.asyncio
    async def test_move_in_queue(self, mock_miniaudio, sample_audio_file):
        """Test moving tracks in queue."""
        session = Session(name="test")
        # Add multiple tracks
        file1 = str(sample_audio_file)
        session.queue = [f"{file1}?1", f"{file1}?2", f"{file1}?3"]

        await session.cmd_move(0, 2)

        assert session.queue[2].endswith("?1")
        assert session.queue[0].endswith("?2")

    @pytest.mark.asyncio
    async def test_get_queue(self, mock_miniaudio, sample_audio_file):
        """Test getting queue contents."""
        session = Session(name="test")
        await session.cmd_add(str(sample_audio_file))

        result = await session.cmd_queue()

        assert "tracks" in result
        assert "current_index" in result
        assert len(result["tracks"]) == 1


class TestSessionPlayback:
    """Playback control tests."""

    @pytest.mark.asyncio
    async def test_play_file(self, mock_miniaudio, sample_audio_file):
        """Test playing a file."""
        session = Session(name="test")
        result = await session.cmd_play(str(sample_audio_file))

        assert result["state"] == "playing"
        assert session.state == PlaybackState.PLAYING
        assert len(session.queue) == 1

    @pytest.mark.asyncio
    async def test_pause(self, mock_miniaudio, sample_audio_file):
        """Test pausing playback."""
        session = Session(name="test")
        await session.cmd_play(str(sample_audio_file))
        result = await session.cmd_pause()

        assert result["state"] == "paused"
        assert session.state == PlaybackState.PAUSED

    @pytest.mark.asyncio
    async def test_resume(self, mock_miniaudio, sample_audio_file):
        """Test resuming from pause."""
        session = Session(name="test")
        await session.cmd_play(str(sample_audio_file))
        await session.cmd_pause()

        result = await session.cmd_play()

        assert result["state"] == "playing"
        assert session.state == PlaybackState.PLAYING

    @pytest.mark.asyncio
    async def test_stop(self, mock_miniaudio, sample_audio_file):
        """Test stopping playback."""
        session = Session(name="test")
        await session.cmd_play(str(sample_audio_file))
        result = await session.cmd_stop()

        assert result["state"] == "stopped"
        assert session.state == PlaybackState.STOPPED
        assert session.position == 0.0

    @pytest.mark.asyncio
    async def test_seek_absolute(self, mock_miniaudio, sample_audio_file):
        """Test absolute seek."""
        session = Session(name="test")
        await session.cmd_play(str(sample_audio_file))

        result = await session.cmd_seek(30.0)

        assert result["position"] == 30.0

    @pytest.mark.asyncio
    async def test_seek_relative_forward(self, mock_miniaudio, sample_audio_file):
        """Test relative forward seek."""
        session = Session(name="test")
        await session.cmd_play(str(sample_audio_file))
        session.position = 20.0

        result = await session.cmd_seek("+10")

        assert result["position"] == 30.0

    @pytest.mark.asyncio
    async def test_seek_relative_backward(self, mock_miniaudio, sample_audio_file):
        """Test relative backward seek."""
        session = Session(name="test")
        await session.cmd_play(str(sample_audio_file))
        session.position = 30.0

        result = await session.cmd_seek("-10")

        assert result["position"] == 20.0


class TestSessionVolume:
    """Volume control tests."""

    @pytest.mark.asyncio
    async def test_set_volume(self, mock_miniaudio):
        """Test setting volume."""
        session = Session(name="test")
        result = await session.cmd_volume(50)

        assert result["volume"] == 50
        assert session.volume == 50

    @pytest.mark.asyncio
    async def test_volume_clamp_max(self, mock_miniaudio):
        """Test volume is clamped to max."""
        session = Session(name="test")
        result = await session.cmd_volume(150)

        assert result["volume"] == 100

    @pytest.mark.asyncio
    async def test_volume_clamp_min(self, mock_miniaudio):
        """Test volume is clamped to min."""
        session = Session(name="test")
        result = await session.cmd_volume(-10)

        assert result["volume"] == 0


class TestSessionShuffle:
    """Shuffle mode tests."""

    @pytest.mark.asyncio
    async def test_enable_shuffle(self, mock_miniaudio, sample_audio_file):
        """Test enabling shuffle."""
        session = Session(name="test")
        await session.cmd_add(str(sample_audio_file))
        await session.cmd_add(str(sample_audio_file))
        await session.cmd_add(str(sample_audio_file))

        result = await session.cmd_shuffle(True)

        assert result["shuffle"] is True
        assert session.shuffle is True
        assert len(session.shuffle_order) == 3

    @pytest.mark.asyncio
    async def test_disable_shuffle(self, mock_miniaudio):
        """Test disabling shuffle."""
        session = Session(name="test")
        session.shuffle = True
        session.shuffle_order = [2, 0, 1]

        result = await session.cmd_shuffle(False)

        assert result["shuffle"] is False
        assert session.shuffle is False
        assert session.shuffle_order == []


class TestSessionRepeat:
    """Repeat mode tests."""

    @pytest.mark.asyncio
    async def test_set_repeat_queue(self, mock_miniaudio):
        """Test setting repeat to queue."""
        session = Session(name="test")
        result = await session.cmd_repeat("queue")

        assert result["repeat"] == "queue"
        assert session.repeat == RepeatMode.QUEUE

    @pytest.mark.asyncio
    async def test_set_repeat_track(self, mock_miniaudio):
        """Test setting repeat to track."""
        session = Session(name="test")
        result = await session.cmd_repeat("track")

        assert result["repeat"] == "track"
        assert session.repeat == RepeatMode.TRACK

    @pytest.mark.asyncio
    async def test_set_repeat_none(self, mock_miniaudio):
        """Test setting repeat to none."""
        session = Session(name="test")
        session.repeat = RepeatMode.QUEUE
        result = await session.cmd_repeat("none")

        assert result["repeat"] == "none"
        assert session.repeat == RepeatMode.NONE


class TestSessionStatus:
    """Status tests."""

    @pytest.mark.asyncio
    async def test_get_status(self, mock_miniaudio, sample_audio_file):
        """Test getting session status."""
        session = Session(name="test")
        await session.cmd_play(str(sample_audio_file))
        await session.cmd_volume(75)

        result = await session.cmd_status()

        assert result["state"] == "playing"
        assert result["volume"] == 75
        assert result["shuffle"] is False
        assert result["repeat"] == "none"
        assert result["queue_length"] == 1
        assert result["queue_position"] == 0

    @pytest.mark.asyncio
    async def test_get_status_empty_queue(self, mock_miniaudio):
        """Test status with empty queue."""
        session = Session(name="test")
        result = await session.cmd_status()

        assert result["state"] == "stopped"
        assert result["track"] is None
        assert result["queue_length"] == 0


class TestSessionPersistence:
    """Session serialization tests."""

    def test_to_dict(self, mock_miniaudio, sample_audio_file):
        """Test session serialization."""
        session = Session(name="test")
        session.queue = [str(sample_audio_file)]
        session.volume = 60
        session.shuffle = True

        data = session.to_dict()

        assert data["name"] == "test"
        assert data["volume"] == 60
        assert data["shuffle"] is True
        assert len(data["queue"]) == 1

    def test_from_dict(self, mock_miniaudio, sample_audio_file):
        """Test session deserialization."""
        data = {
            "name": "restored",
            "queue": [str(sample_audio_file)],
            "position": 30.0,
            "current_index": 0,
            "shuffle": True,
            "shuffle_order": [0],
            "repeat": "queue",
            "volume": 70,
        }

        session = Session.from_dict(data)

        assert session.name == "restored"
        assert session.volume == 70
        assert session.shuffle is True
        assert session.repeat == RepeatMode.QUEUE


class TestSessionEvents:
    """Event emission tests."""

    @pytest.mark.asyncio
    async def test_event_callback(self, mock_miniaudio, sample_audio_file):
        """Test that events are emitted via callback."""
        session = Session(name="test")
        events = []

        def capture_event(event: Event):
            events.append(event)

        session.set_event_callback(capture_event)
        await session.cmd_play(str(sample_audio_file))

        # Should have track_changed and playback_started
        event_types = [e.event for e in events]
        assert EventType.TRACK_CHANGED in event_types
        assert EventType.PLAYBACK_STARTED in event_types

    @pytest.mark.asyncio
    async def test_pause_emits_event(self, mock_miniaudio, sample_audio_file):
        """Test pause emits event."""
        session = Session(name="test")
        events = []
        session.set_event_callback(lambda e: events.append(e))

        await session.cmd_play(str(sample_audio_file))
        events.clear()
        await session.cmd_pause()

        assert any(e.event == EventType.PLAYBACK_PAUSED for e in events)

    @pytest.mark.asyncio
    async def test_queue_update_emits_event(self, mock_miniaudio, sample_audio_file):
        """Test queue changes emit events."""
        session = Session(name="test")
        events = []
        session.set_event_callback(lambda e: events.append(e))

        await session.cmd_add(str(sample_audio_file))

        assert any(e.event == EventType.QUEUE_UPDATED for e in events)


class TestSessionRate:
    """Playback rate tests."""

    @pytest.mark.asyncio
    async def test_set_rate(self, mock_miniaudio):
        """Test setting playback rate."""
        session = Session(name="test")
        result = await session.cmd_rate(1.5)

        assert result["rate"] == 1.5
        assert session.rate == 1.5

    @pytest.mark.asyncio
    async def test_rate_clamp_max(self, mock_miniaudio):
        """Test rate is clamped to max."""
        session = Session(name="test")
        result = await session.cmd_rate(5.0)

        assert result["rate"] == 4.0

    @pytest.mark.asyncio
    async def test_rate_clamp_min(self, mock_miniaudio):
        """Test rate is clamped to min."""
        session = Session(name="test")
        result = await session.cmd_rate(0.1)

        assert result["rate"] == 0.25


class TestSessionPitch:
    """Pitch shift tests."""

    @pytest.mark.asyncio
    async def test_set_pitch(self, mock_miniaudio):
        """Test setting pitch shift."""
        session = Session(name="test")
        result = await session.cmd_pitch(5.0)

        assert result["pitch"] == 5.0
        assert session.pitch == 5.0

    @pytest.mark.asyncio
    async def test_pitch_clamp_max(self, mock_miniaudio):
        """Test pitch is clamped to max."""
        session = Session(name="test")
        result = await session.cmd_pitch(15.0)

        assert result["pitch"] == 12.0

    @pytest.mark.asyncio
    async def test_pitch_clamp_min(self, mock_miniaudio):
        """Test pitch is clamped to min."""
        session = Session(name="test")
        result = await session.cmd_pitch(-15.0)

        assert result["pitch"] == -12.0


class TestSessionEQ:
    """EQ (bass/treble) tests."""

    @pytest.mark.asyncio
    async def test_set_bass(self, mock_miniaudio):
        """Test setting bass EQ."""
        session = Session(name="test")
        result = await session.cmd_bass(6.0)

        assert result["bass"] == 6.0
        assert session.bass == 6.0

    @pytest.mark.asyncio
    async def test_set_treble(self, mock_miniaudio):
        """Test setting treble EQ."""
        session = Session(name="test")
        result = await session.cmd_treble(-3.0)

        assert result["treble"] == -3.0
        assert session.treble == -3.0

    @pytest.mark.asyncio
    async def test_bass_clamp(self, mock_miniaudio):
        """Test bass is clamped to range."""
        session = Session(name="test")
        result = await session.cmd_bass(20.0)
        assert result["bass"] == 12.0

        result = await session.cmd_bass(-20.0)
        assert result["bass"] == -12.0

    @pytest.mark.asyncio
    async def test_treble_clamp(self, mock_miniaudio):
        """Test treble is clamped to range."""
        session = Session(name="test")
        result = await session.cmd_treble(20.0)
        assert result["treble"] == 12.0

        result = await session.cmd_treble(-20.0)
        assert result["treble"] == -12.0


class TestSessionFade:
    """Fade tests."""

    @pytest.mark.asyncio
    async def test_fade(self, mock_miniaudio):
        """Test starting a fade."""
        session = Session(name="test")
        result = await session.cmd_fade(0, 3.0)

        assert result["fading_to"] == 0
        assert result["duration"] == 3.0


class TestSessionLoop:
    """A/B loop tests."""

    @pytest.mark.asyncio
    async def test_set_loop_points(self, mock_miniaudio):
        """Test setting A/B loop points."""
        session = Session(name="test")
        result = await session.cmd_loop(a=10.0, b=30.0)

        assert result["loop_a"] == 10.0
        assert result["loop_b"] == 30.0
        assert result["loop_enabled"] is True
        assert session.loop_a == 10.0
        assert session.loop_b == 30.0
        assert session.loop_enabled is True

    @pytest.mark.asyncio
    async def test_disable_loop(self, mock_miniaudio):
        """Test disabling loop."""
        session = Session(name="test")
        session.loop_a = 10.0
        session.loop_b = 30.0
        session.loop_enabled = True

        result = await session.cmd_loop(enabled=False)

        assert result["loop_enabled"] is False
        assert session.loop_enabled is False

    @pytest.mark.asyncio
    async def test_enable_loop(self, mock_miniaudio):
        """Test enabling loop."""
        session = Session(name="test")
        session.loop_a = 10.0
        session.loop_b = 30.0

        result = await session.cmd_loop(enabled=True)

        assert result["loop_enabled"] is True


class TestSessionStatusWithDSP:
    """Status tests including DSP parameters."""

    @pytest.mark.asyncio
    async def test_status_includes_dsp(self, mock_miniaudio, sample_audio_file):
        """Test status includes DSP parameters."""
        session = Session(name="test")
        await session.cmd_play(str(sample_audio_file))
        await session.cmd_rate(1.5)
        await session.cmd_pitch(2.0)
        await session.cmd_bass(3.0)
        await session.cmd_treble(-2.0)
        await session.cmd_loop(a=10.0, b=30.0)

        result = await session.cmd_status()

        assert result["rate"] == 1.5
        assert result["pitch"] == 2.0
        assert result["bass"] == 3.0
        assert result["treble"] == -2.0
        assert result["loop_a"] == 10.0
        assert result["loop_b"] == 30.0
        assert result["loop_enabled"] is True


class TestSessionPersistenceWithDSP:
    """Persistence tests including DSP parameters."""

    def test_to_dict_includes_dsp(self, mock_miniaudio):
        """Test serialization includes DSP parameters."""
        session = Session(name="test")
        session.rate = 1.5
        session.pitch = 2.0
        session.bass = 3.0
        session.treble = -2.0
        session.loop_a = 10.0
        session.loop_b = 30.0
        session.loop_enabled = True

        data = session.to_dict()

        assert data["rate"] == 1.5
        assert data["pitch"] == 2.0
        assert data["bass"] == 3.0
        assert data["treble"] == -2.0
        assert data["loop_a"] == 10.0
        assert data["loop_b"] == 30.0
        assert data["loop_enabled"] is True

    def test_from_dict_restores_dsp(self, mock_miniaudio):
        """Test deserialization restores DSP parameters."""
        data = {
            "name": "restored",
            "queue": [],
            "position": 0.0,
            "current_index": 0,
            "shuffle": False,
            "shuffle_order": [],
            "repeat": "none",
            "volume": 80,
            "rate": 1.5,
            "pitch": 2.0,
            "bass": 3.0,
            "treble": -2.0,
            "loop_a": 10.0,
            "loop_b": 30.0,
            "loop_enabled": True,
        }

        session = Session.from_dict(data)

        assert session.rate == 1.5
        assert session.pitch == 2.0
        assert session.bass == 3.0
        assert session.treble == -2.0
        assert session.loop_a == 10.0
        assert session.loop_b == 30.0
        assert session.loop_enabled is True
