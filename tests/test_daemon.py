"""Tests for ATK Daemon (queue, playback, commands)."""

from __future__ import annotations

import pytest

from atk.daemon import Daemon


@pytest.fixture
def daemon(mock_miniaudio, tmp_path):
    """Create a Daemon instance for testing."""
    return Daemon(tmp_path / "runtime")


class TestDaemonBasic:
    def test_create_daemon(self, daemon):
        assert daemon.state == "stopped"
        assert daemon.queue == []
        assert daemon.volume == 80
        assert daemon.rate == 1.0

    def test_defaults(self, daemon):
        assert daemon.shuffle is False
        assert daemon.repeat == "none"
        assert daemon.queue_pos == 0


class TestDaemonQueue:
    @pytest.mark.asyncio
    async def test_add(self, daemon, sample_audio_file):
        result = await daemon._cmd_add({"uri": str(sample_audio_file)})
        assert result["queue_length"] == 1
        assert len(daemon.queue) == 1

    @pytest.mark.asyncio
    async def test_remove(self, daemon, sample_audio_file):
        await daemon._cmd_add({"uri": str(sample_audio_file)})
        await daemon._cmd_add({"uri": str(sample_audio_file)})
        result = await daemon._cmd_remove({"index": 0})
        assert "removed" in result
        assert len(daemon.queue) == 1

    @pytest.mark.asyncio
    async def test_remove_invalid(self, daemon):
        with pytest.raises(IndexError):
            await daemon._cmd_remove({"index": 0})

    @pytest.mark.asyncio
    async def test_clear(self, daemon, sample_audio_file):
        await daemon._cmd_add({"uri": str(sample_audio_file)})
        result = await daemon._cmd_clear({})
        assert result["cleared"] is True
        assert len(daemon.queue) == 0
        assert daemon.state == "stopped"

    @pytest.mark.asyncio
    async def test_move(self, daemon, sample_audio_file):
        f = str(sample_audio_file)
        daemon.queue = [f"{f}?1", f"{f}?2", f"{f}?3"]
        await daemon._cmd_move({"from": 0, "to": 2})
        assert daemon.queue[2].endswith("?1")
        assert daemon.queue[0].endswith("?2")

    @pytest.mark.asyncio
    async def test_queue(self, daemon, sample_audio_file):
        await daemon._cmd_add({"uri": str(sample_audio_file)})
        result = await daemon._cmd_queue({})
        assert "tracks" in result
        assert "current_index" in result
        assert len(result["tracks"]) == 1


class TestDaemonPlayback:
    @pytest.mark.asyncio
    async def test_play_file(self, daemon, sample_audio_file):
        result = await daemon._cmd_play({"file": str(sample_audio_file)})
        assert result["state"] == "playing"
        assert daemon.state == "playing"
        assert len(daemon.queue) == 1

    @pytest.mark.asyncio
    async def test_pause(self, daemon, sample_audio_file):
        await daemon._cmd_play({"file": str(sample_audio_file)})
        result = await daemon._cmd_pause({})
        assert result["state"] == "paused"
        assert daemon.state == "paused"

    @pytest.mark.asyncio
    async def test_resume(self, daemon, sample_audio_file):
        await daemon._cmd_play({"file": str(sample_audio_file)})
        await daemon._cmd_pause({})
        result = await daemon._cmd_play({})
        assert result["state"] == "playing"

    @pytest.mark.asyncio
    async def test_stop(self, daemon, sample_audio_file):
        await daemon._cmd_play({"file": str(sample_audio_file)})
        result = await daemon._cmd_stop({})
        assert result["state"] == "stopped"

    @pytest.mark.asyncio
    async def test_seek(self, daemon, sample_audio_file):
        await daemon._cmd_play({"file": str(sample_audio_file)})
        result = await daemon._cmd_seek({"pos": 30.0})
        assert result["position"] == 30.0

    @pytest.mark.asyncio
    async def test_seek_relative_forward(self, daemon, sample_audio_file):
        await daemon._cmd_play({"file": str(sample_audio_file)})
        daemon.player.seek(20.0)
        result = await daemon._cmd_seek({"pos": "+10"})
        assert result["position"] == pytest.approx(30.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_seek_relative_backward(self, daemon, sample_audio_file):
        await daemon._cmd_play({"file": str(sample_audio_file)})
        daemon.player.seek(30.0)
        result = await daemon._cmd_seek({"pos": "-10"})
        assert result["position"] == pytest.approx(20.0, abs=1.0)


class TestDaemonVolume:
    @pytest.mark.asyncio
    async def test_set_volume(self, daemon):
        result = await daemon._cmd_volume({"level": 50})
        assert result["volume"] == 50
        assert daemon.volume == 50

    @pytest.mark.asyncio
    async def test_volume_clamp_max(self, daemon):
        result = await daemon._cmd_volume({"level": 150})
        assert result["volume"] == 100

    @pytest.mark.asyncio
    async def test_volume_clamp_min(self, daemon):
        result = await daemon._cmd_volume({"level": -10})
        assert result["volume"] == 0


class TestDaemonShuffle:
    @pytest.mark.asyncio
    async def test_enable(self, daemon, sample_audio_file):
        for _ in range(3):
            await daemon._cmd_add({"uri": str(sample_audio_file)})
        result = await daemon._cmd_shuffle({"enabled": True})
        assert result["shuffle"] is True
        assert daemon.shuffle is True
        assert len(daemon.shuffle_order) == 3

    @pytest.mark.asyncio
    async def test_disable(self, daemon):
        daemon.shuffle = True
        daemon.shuffle_order = [2, 0, 1]
        result = await daemon._cmd_shuffle({"enabled": False})
        assert result["shuffle"] is False
        assert daemon.shuffle_order == []


class TestDaemonRepeat:
    @pytest.mark.asyncio
    async def test_set_queue(self, daemon):
        result = await daemon._cmd_repeat({"mode": "queue"})
        assert result["repeat"] == "queue"

    @pytest.mark.asyncio
    async def test_set_track(self, daemon):
        result = await daemon._cmd_repeat({"mode": "track"})
        assert result["repeat"] == "track"

    @pytest.mark.asyncio
    async def test_set_none(self, daemon):
        daemon.repeat = "queue"
        result = await daemon._cmd_repeat({"mode": "none"})
        assert result["repeat"] == "none"

    @pytest.mark.asyncio
    async def test_invalid_mode(self, daemon):
        with pytest.raises(ValueError):
            await daemon._cmd_repeat({"mode": "bogus"})


class TestDaemonRate:
    @pytest.mark.asyncio
    async def test_set_rate(self, daemon):
        result = await daemon._cmd_rate({"speed": 1.5})
        assert result["rate"] == 1.5
        assert daemon.rate == 1.5

    @pytest.mark.asyncio
    async def test_rate_clamp_max(self, daemon):
        result = await daemon._cmd_rate({"speed": 5.0})
        assert result["rate"] == 4.0

    @pytest.mark.asyncio
    async def test_rate_clamp_min(self, daemon):
        result = await daemon._cmd_rate({"speed": 0.1})
        assert result["rate"] == 0.25


class TestDaemonStatus:
    @pytest.mark.asyncio
    async def test_status_playing(self, daemon, sample_audio_file):
        await daemon._cmd_play({"file": str(sample_audio_file)})
        await daemon._cmd_volume({"level": 75})
        result = await daemon._cmd_status({})
        assert result["state"] == "playing"
        assert result["volume"] == 75
        assert result["shuffle"] is False
        assert result["repeat"] == "none"
        assert result["queue_length"] == 1
        assert result["queue_position"] == 0

    @pytest.mark.asyncio
    async def test_status_empty(self, daemon):
        result = await daemon._cmd_status({})
        assert result["state"] == "stopped"
        assert result["track"] is None
        assert result["queue_length"] == 0

    @pytest.mark.asyncio
    async def test_status_includes_rate(self, daemon, sample_audio_file):
        await daemon._cmd_play({"file": str(sample_audio_file)})
        await daemon._cmd_rate({"speed": 1.5})
        result = await daemon._cmd_status({})
        assert result["rate"] == 1.5


class TestDaemonJump:
    @pytest.mark.asyncio
    async def test_jump(self, daemon, sample_audio_file):
        for _ in range(3):
            await daemon._cmd_add({"uri": str(sample_audio_file)})
        result = await daemon._cmd_jump({"index": 2})
        assert result["queue_position"] == 2
        assert daemon.queue_pos == 2

    @pytest.mark.asyncio
    async def test_jump_invalid(self, daemon, sample_audio_file):
        await daemon._cmd_add({"uri": str(sample_audio_file)})
        with pytest.raises(IndexError):
            await daemon._cmd_jump({"index": 5})


class TestDaemonShuffleRaceCondition:
    @pytest.mark.asyncio
    async def test_advance_with_missing_position(self, daemon, sample_audio_file):
        for _ in range(3):
            await daemon._cmd_add({"uri": str(sample_audio_file)})
        daemon.shuffle = True
        daemon.shuffle_order = [0, 2]  # Missing 1
        daemon.queue_pos = 1  # Not in shuffle_order
        result = daemon._advance()
        assert result is True  # Fallback to linear

    @pytest.mark.asyncio
    async def test_previous_with_missing_position(self, daemon, sample_audio_file):
        for _ in range(3):
            await daemon._cmd_add({"uri": str(sample_audio_file)})
        daemon.shuffle = True
        daemon.shuffle_order = [0, 2]
        daemon.queue_pos = 1
        result = daemon._go_previous()
        assert result is True


class TestDaemonDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_valid(self, daemon):
        resp = await daemon._dispatch('{"id": "1", "cmd": "ping", "args": {}}')
        assert resp["ok"] is True
        assert resp["data"]["pong"] is True

    @pytest.mark.asyncio
    async def test_dispatch_unknown(self, daemon):
        resp = await daemon._dispatch('{"id": "1", "cmd": "bogus", "args": {}}')
        assert resp["ok"] is False
        assert "Unknown" in resp["error"]["message"]

    @pytest.mark.asyncio
    async def test_dispatch_bad_json(self, daemon):
        resp = await daemon._dispatch("not json")
        assert resp["ok"] is False

    @pytest.mark.asyncio
    async def test_dispatch_no_cmd(self, daemon):
        resp = await daemon._dispatch('{"id": "1", "args": {}}')
        assert resp["ok"] is False

    @pytest.mark.asyncio
    async def test_subscribe(self, daemon):
        result = await daemon._cmd_subscribe({})
        assert result["subscribed"] is True
        assert daemon._has_subscribers is True


class TestDaemonPlaylists:
    @pytest.mark.asyncio
    async def test_save_and_load(self, daemon, sample_audio_file, temp_data_dir):
        await daemon._cmd_add({"uri": str(sample_audio_file)})
        save_result = await daemon._cmd_save({"name": "test_pl"})
        assert save_result["track_count"] == 1

        await daemon._cmd_clear({})
        assert len(daemon.queue) == 0

        load_result = await daemon._cmd_load({"name": "test_pl"})
        assert load_result["track_count"] == 1
        assert len(daemon.queue) == 1

    @pytest.mark.asyncio
    async def test_list_playlists(self, daemon, sample_audio_file, temp_data_dir):
        await daemon._cmd_add({"uri": str(sample_audio_file)})
        await daemon._cmd_save({"name": "pl1"})
        result = await daemon._cmd_playlists({})
        names = [p["name"] for p in result["playlists"]]
        assert "pl1" in names

    @pytest.mark.asyncio
    async def test_load_not_found(self, daemon, temp_data_dir):
        with pytest.raises(FileNotFoundError):
            await daemon._cmd_load({"name": "nonexistent"})


class TestDaemonDevices:
    @pytest.mark.asyncio
    async def test_devices(self, daemon, mock_miniaudio):
        # list_devices is called but returns empty in mock
        result = await daemon._cmd_devices({})
        assert "devices" in result

    @pytest.mark.asyncio
    async def test_set_device(self, daemon):
        result = await daemon._cmd_set_device({"device_id": "0102"})
        assert result["device_id"] == "0102"

    @pytest.mark.asyncio
    async def test_set_device_default(self, daemon):
        result = await daemon._cmd_set_device({"device_id": None})
        assert result["device_id"] is None
