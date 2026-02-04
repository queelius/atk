"""Tests for ATK CLI."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from atk.cli.main import cli
from atk.cli.output import (
    format_devices,
    format_playlists,
    format_queue,
    format_status,
    format_time,
    format_track,
)
from atk.protocol.messages import Response


class TestOutputFormatters:
    """Tests for CLI output formatting."""

    def test_format_time_seconds(self):
        """Test formatting seconds."""
        assert format_time(30) == "0:30"
        assert format_time(90) == "1:30"
        assert format_time(0) == "0:00"

    def test_format_time_minutes(self):
        """Test formatting minutes."""
        assert format_time(180) == "3:00"
        assert format_time(195) == "3:15"

    def test_format_time_hours(self):
        """Test formatting hours."""
        assert format_time(3600) == "1:00:00"
        assert format_time(3661) == "1:01:01"

    def test_format_time_negative(self):
        """Test formatting negative time."""
        assert format_time(-5) == "0:00"

    def test_format_track_basic(self):
        """Test formatting basic track."""
        track = {"uri": "/path/to/song.mp3"}
        assert "song.mp3" in format_track(track)

    def test_format_track_with_metadata(self):
        """Test formatting track with metadata."""
        track = {
            "uri": "/path/to/song.mp3",
            "title": "Test Song",
            "artist": "Test Artist",
            "duration": 180,
        }
        result = format_track(track)
        assert "Test Artist" in result
        assert "Test Song" in result
        assert "3:00" in result

    def test_format_track_none(self):
        """Test formatting None track."""
        assert format_track(None) == "(no track)"

    def test_format_status(self):
        """Test formatting status."""
        status = {
            "state": "playing",
            "track": {"title": "Test", "artist": "Artist"},
            "position": 30,
            "duration": 180,
            "volume": 80,
            "shuffle": True,
            "repeat": "queue",
            "queue_length": 5,
            "queue_position": 2,
        }
        result = format_status(status)
        assert "▶" in result  # Playing icon
        assert "80%" in result

    def test_format_status_with_rate(self):
        """Test formatting status with non-default rate."""
        status = {
            "state": "playing",
            "track": {"title": "Test"},
            "position": 30,
            "duration": 180,
            "volume": 80,
            "shuffle": False,
            "repeat": "none",
            "queue_length": 1,
            "queue_position": 0,
            "rate": 1.5,
        }
        result = format_status(status)
        assert "1.50x" in result

    def test_format_queue_empty(self):
        """Test formatting empty queue."""
        assert format_queue({"tracks": [], "current_index": 0}) == "(empty queue)"

    def test_format_queue_with_tracks(self):
        """Test formatting queue with tracks."""
        data = {
            "tracks": [
                {"uri": "track1.mp3", "title": "Track 1"},
                {"uri": "track2.mp3", "title": "Track 2"},
            ],
            "current_index": 0,
        }
        result = format_queue(data)
        assert "Track 1" in result
        assert "Track 2" in result
        assert "▶" in result  # Current track indicator

    def test_format_playlists_empty(self):
        """Test formatting empty playlists."""
        assert format_playlists({"playlists": []}) == "(no saved playlists)"

    def test_format_playlists_with_data(self):
        """Test formatting playlists with data."""
        data = {
            "playlists": [
                {"name": "favorites", "track_count": 10, "format": "json"},
                {"name": "workout", "track_count": 5, "format": "m3u"},
            ],
        }
        result = format_playlists(data)
        assert "favorites" in result
        assert "10 tracks" in result
        assert "workout" in result

    def test_format_devices_empty(self):
        """Test formatting empty devices list."""
        assert format_devices({"devices": []}) == "(no audio devices found)"

    def test_format_devices_with_data(self):
        """Test formatting devices list with data."""
        # Test with hex strings (as returned by daemon)
        data = {
            "devices": [
                {"name": "Built-in Audio", "id": "010203", "is_default": True},
                {"name": "USB Headphones", "id": "040506", "is_default": False},
            ],
        }
        result = format_devices(data)
        assert "Built-in Audio" in result
        assert "(default)" in result
        assert "USB Headphones" in result
        assert "010203" in result  # hex device ID

    def test_format_devices_with_bytes(self):
        """Test formatting devices list with raw bytes (direct from player)."""
        data = {
            "devices": [
                {"name": "Built-in Audio", "id": b"\x01\x02\x03", "is_default": True},
            ],
        }
        result = format_devices(data)
        assert "Built-in Audio" in result
        assert "010203" in result  # converted to hex


class TestCLICommands:
    """Tests for CLI commands."""

    @pytest.fixture
    def runner(self):
        """Create CLI runner."""
        return CliRunner()

    @pytest.fixture
    def mock_commands(self):
        """Mock command functions."""
        with patch("atk.cli.main.commands") as mock:
            yield mock

    def test_play_command(self, runner, mock_commands):
        """Test play command."""
        mock_commands.cmd_play.return_value = Response.success(
            "id", {"state": "playing"}
        )

        result = runner.invoke(cli, ["play"])
        assert result.exit_code == 0
        mock_commands.cmd_play.assert_called_once_with(None)

    def test_play_file_command(self, runner, mock_commands, tmp_path):
        """Test play with file."""
        mock_commands.cmd_play.return_value = Response.success(
            "id", {"state": "playing"}
        )

        # Create test file
        test_file = tmp_path / "test.mp3"
        test_file.touch()

        result = runner.invoke(cli, ["play", str(test_file)])
        assert result.exit_code == 0
        mock_commands.cmd_play.assert_called_once()

    def test_pause_command(self, runner, mock_commands):
        """Test pause command."""
        mock_commands.cmd_pause.return_value = Response.success(
            "id", {"state": "paused"}
        )

        result = runner.invoke(cli, ["pause"])
        assert result.exit_code == 0
        mock_commands.cmd_pause.assert_called_once()

    def test_stop_command(self, runner, mock_commands):
        """Test stop command."""
        mock_commands.cmd_stop.return_value = Response.success(
            "id", {"state": "stopped"}
        )

        result = runner.invoke(cli, ["stop"])
        assert result.exit_code == 0
        mock_commands.cmd_stop.assert_called_once()

    def test_next_command(self, runner, mock_commands):
        """Test next command."""
        mock_commands.cmd_next.return_value = Response.success(
            "id", {"queue_position": 1}
        )

        result = runner.invoke(cli, ["next"])
        assert result.exit_code == 0
        mock_commands.cmd_next.assert_called_once()

    def test_prev_command(self, runner, mock_commands):
        """Test prev command."""
        mock_commands.cmd_prev.return_value = Response.success(
            "id", {"queue_position": 0}
        )

        result = runner.invoke(cli, ["prev"])
        assert result.exit_code == 0
        mock_commands.cmd_prev.assert_called_once()

    def test_seek_command(self, runner, mock_commands):
        """Test seek command."""
        mock_commands.cmd_seek.return_value = Response.success("id", {"position": 30})

        result = runner.invoke(cli, ["seek", "30"])
        assert result.exit_code == 0
        mock_commands.cmd_seek.assert_called_once_with("30")

    def test_volume_command(self, runner, mock_commands):
        """Test volume command."""
        mock_commands.cmd_volume.return_value = Response.success("id", {"volume": 50})

        result = runner.invoke(cli, ["volume", "50"])
        assert result.exit_code == 0
        mock_commands.cmd_volume.assert_called_once_with(50)

    def test_add_command(self, runner, mock_commands):
        """Test add command."""
        mock_commands.cmd_add.return_value = Response.success("id", {"queue_length": 1})

        result = runner.invoke(cli, ["add", "/path/to/file.mp3"])
        assert result.exit_code == 0
        mock_commands.cmd_add.assert_called_once()

    def test_remove_command(self, runner, mock_commands):
        """Test remove command."""
        mock_commands.cmd_remove.return_value = Response.success(
            "id", {"removed": "track"}
        )

        result = runner.invoke(cli, ["remove", "0"])
        assert result.exit_code == 0
        mock_commands.cmd_remove.assert_called_once_with(0)

    def test_clear_command(self, runner, mock_commands):
        """Test clear command."""
        mock_commands.cmd_clear.return_value = Response.success("id", {"cleared": True})

        result = runner.invoke(cli, ["clear"])
        assert result.exit_code == 0
        mock_commands.cmd_clear.assert_called_once()

    def test_queue_command(self, runner, mock_commands):
        """Test queue command."""
        mock_commands.cmd_queue.return_value = Response.success(
            "id", {"tracks": [], "current_index": 0}
        )

        result = runner.invoke(cli, ["queue"])
        assert result.exit_code == 0
        mock_commands.cmd_queue.assert_called_once()

    def test_jump_command(self, runner, mock_commands):
        """Test jump command."""
        mock_commands.cmd_jump.return_value = Response.success(
            "id", {"queue_position": 2}
        )

        result = runner.invoke(cli, ["jump", "2"])
        assert result.exit_code == 0
        mock_commands.cmd_jump.assert_called_once_with(2)

    def test_status_command(self, runner, mock_commands):
        """Test status command."""
        mock_commands.cmd_status.return_value = Response.success(
            "id",
            {
                "state": "stopped",
                "track": None,
                "position": 0,
                "duration": 0,
                "volume": 80,
                "shuffle": False,
                "repeat": "none",
                "queue_length": 0,
                "queue_position": 0,
            },
        )

        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        mock_commands.cmd_status.assert_called_once()

    def test_shuffle_on_command(self, runner, mock_commands):
        """Test shuffle on command."""
        mock_commands.cmd_shuffle.return_value = Response.success(
            "id", {"shuffle": True}
        )

        result = runner.invoke(cli, ["shuffle", "on"])
        assert result.exit_code == 0
        mock_commands.cmd_shuffle.assert_called_once_with(True)

    def test_shuffle_off_command(self, runner, mock_commands):
        """Test shuffle off command."""
        mock_commands.cmd_shuffle.return_value = Response.success(
            "id", {"shuffle": False}
        )

        result = runner.invoke(cli, ["shuffle", "off"])
        assert result.exit_code == 0
        mock_commands.cmd_shuffle.assert_called_once_with(False)

    def test_repeat_command(self, runner, mock_commands):
        """Test repeat command."""
        mock_commands.cmd_repeat.return_value = Response.success(
            "id", {"repeat": "queue"}
        )

        result = runner.invoke(cli, ["repeat", "queue"])
        assert result.exit_code == 0
        mock_commands.cmd_repeat.assert_called_once_with("queue")

    def test_rate_command(self, runner, mock_commands):
        """Test rate command."""
        mock_commands.cmd_rate.return_value = Response.success("id", {"rate": 1.5})

        result = runner.invoke(cli, ["rate", "1.5"])
        assert result.exit_code == 0
        mock_commands.cmd_rate.assert_called_once_with(1.5)

    def test_save_command(self, runner, mock_commands):
        """Test save command."""
        mock_commands.cmd_save.return_value = Response.success(
            "id", {"saved": "/path/to/playlist.json", "track_count": 5}
        )

        result = runner.invoke(cli, ["save", "myplaylist"])
        assert result.exit_code == 0
        mock_commands.cmd_save.assert_called_once_with("myplaylist", "json")

    def test_load_command(self, runner, mock_commands):
        """Test load command."""
        mock_commands.cmd_load.return_value = Response.success(
            "id", {"loaded": "/path/to/playlist.json", "track_count": 5}
        )

        result = runner.invoke(cli, ["load", "myplaylist"])
        assert result.exit_code == 0
        mock_commands.cmd_load.assert_called_once_with("myplaylist")

    def test_playlists_command(self, runner, mock_commands):
        """Test playlists command."""
        mock_commands.cmd_playlists.return_value = Response.success(
            "id", {"playlists": []}
        )

        result = runner.invoke(cli, ["playlists"])
        assert result.exit_code == 0
        mock_commands.cmd_playlists.assert_called_once()

    def test_ping_command(self, runner, mock_commands):
        """Test ping command."""
        mock_commands.cmd_ping.return_value = Response.success("id", {"pong": True})

        result = runner.invoke(cli, ["ping"])
        assert result.exit_code == 0
        mock_commands.cmd_ping.assert_called_once()

    def test_json_output(self, runner, mock_commands):
        """Test --json output option."""
        mock_commands.cmd_status.return_value = Response.success(
            "id",
            {
                "state": "stopped",
                "track": None,
                "position": 0,
                "duration": 0,
                "volume": 80,
                "shuffle": False,
                "repeat": "none",
                "queue_length": 0,
                "queue_position": 0,
            },
        )

        result = runner.invoke(cli, ["--json", "status"])
        assert result.exit_code == 0
        # Should contain JSON output
        import json

        data = json.loads(result.output)
        assert data["ok"] is True

    def test_error_response(self, runner, mock_commands):
        """Test error response handling."""
        from atk.protocol.messages import ErrorCode, ErrorInfo

        mock_commands.cmd_jump.return_value = Response.failure(
            "id",
            ErrorInfo(
                code=ErrorCode.INVALID_INDEX,
                category="queue",
                message="Invalid queue index: 99",
            ),
        )

        result = runner.invoke(cli, ["jump", "99"])
        assert result.exit_code == 1
        assert "Error" in result.output

    def test_devices_command(self, runner, mock_commands):
        """Test devices command."""
        mock_commands.cmd_devices.return_value = Response.success(
            "id",
            {
                "devices": [
                    {"name": "Built-in Audio", "id": "0102", "is_default": True},
                ]
            },
        )

        result = runner.invoke(cli, ["devices"])
        assert result.exit_code == 0
        mock_commands.cmd_devices.assert_called_once()

    def test_set_device_command(self, runner, mock_commands):
        """Test set-device command."""
        mock_commands.cmd_set_device.return_value = Response.success(
            "id", {"device_id": "0102"}
        )

        result = runner.invoke(cli, ["set-device", "0102"])
        assert result.exit_code == 0
        mock_commands.cmd_set_device.assert_called_once_with("0102")

    def test_set_device_default(self, runner, mock_commands):
        """Test set-device command with no argument resets to default."""
        mock_commands.cmd_set_device.return_value = Response.success(
            "id", {"device_id": None}
        )

        result = runner.invoke(cli, ["set-device"])
        assert result.exit_code == 0
        mock_commands.cmd_set_device.assert_called_once_with(None)
