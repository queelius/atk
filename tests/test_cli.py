"""Tests for ATK CLI (formatters, commands, seek parser)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from atk.cli import (
    cli,
    fmt_devices,
    fmt_event,
    fmt_playlists,
    fmt_queue,
    fmt_status,
    fmt_time,
    fmt_track,
    parse_seek,
)


class TestFormatters:
    def test_time_seconds(self):
        assert fmt_time(30) == "0:30"
        assert fmt_time(90) == "1:30"
        assert fmt_time(0) == "0:00"

    def test_time_minutes(self):
        assert fmt_time(180) == "3:00"
        assert fmt_time(195) == "3:15"

    def test_time_hours(self):
        assert fmt_time(3600) == "1:00:00"
        assert fmt_time(3661) == "1:01:01"

    def test_time_negative(self):
        assert fmt_time(-5) == "0:00"

    def test_track_basic(self):
        assert "song.mp3" in fmt_track({"uri": "/path/to/song.mp3"})

    def test_track_with_metadata(self):
        track = {
            "uri": "/path/to/song.mp3",
            "title": "Test Song",
            "artist": "Test Artist",
            "duration": 180,
        }
        result = fmt_track(track)
        assert "Test Artist" in result
        assert "Test Song" in result
        assert "3:00" in result

    def test_track_none(self):
        assert fmt_track(None) == "(no track)"

    def test_track_no_duration(self):
        result = fmt_track({"title": "X"}, duration=False)
        assert "X" in result
        assert "[" not in result

    def test_status_playing(self):
        result = fmt_status(
            {
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
        )
        assert "\u25b6" in result
        assert "80%" in result

    def test_status_with_rate(self):
        result = fmt_status(
            {
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
        )
        assert "1.50x" in result

    def test_status_stopped(self):
        result = fmt_status(
            {
                "state": "stopped",
                "track": None,
                "volume": 80,
            }
        )
        assert "\u23f9" in result

    def test_queue_empty(self):
        assert fmt_queue({"tracks": [], "current_index": 0}) == "(empty queue)"

    def test_queue_with_tracks(self):
        result = fmt_queue(
            {
                "tracks": [
                    {"uri": "track1.mp3", "title": "Track 1"},
                    {"uri": "track2.mp3", "title": "Track 2"},
                ],
                "current_index": 0,
            }
        )
        assert "Track 1" in result
        assert "Track 2" in result
        assert "\u25b6" in result

    def test_playlists_empty(self):
        assert fmt_playlists({"playlists": []}) == "(no saved playlists)"

    def test_playlists_with_data(self):
        result = fmt_playlists(
            {
                "playlists": [
                    {"name": "favorites", "track_count": 10, "format": "json"},
                    {"name": "workout", "track_count": 5, "format": "m3u"},
                ],
            }
        )
        assert "favorites" in result
        assert "10 tracks" in result
        assert "workout" in result

    def test_devices_empty(self):
        assert fmt_devices({"devices": []}) == "(no audio devices found)"

    def test_devices_with_data(self):
        result = fmt_devices(
            {
                "devices": [
                    {"name": "Built-in Audio", "id": "010203", "is_default": True},
                    {"name": "USB Headphones", "id": "040506", "is_default": False},
                ],
            }
        )
        assert "Built-in Audio" in result
        assert "(default)" in result
        assert "USB Headphones" in result
        assert "010203" in result

    def test_devices_with_bytes(self):
        result = fmt_devices(
            {
                "devices": [
                    {
                        "name": "Built-in Audio",
                        "id": b"\x01\x02\x03",
                        "is_default": True,
                    },
                ],
            }
        )
        assert "010203" in result

    def test_event_track_changed(self):
        result = fmt_event(
            {"event": "track_changed", "data": {"track": {"title": "X"}}}
        )
        assert "track_changed" in result
        assert "X" in result

    def test_event_position(self):
        result = fmt_event(
            {"event": "position_update", "data": {"position": 30, "duration": 60}}
        )
        assert "0:30" in result

    def test_event_error(self):
        result = fmt_event({"event": "error", "data": {"message": "boom"}})
        assert "boom" in result


class TestParseSeek:
    def test_absolute_seconds(self):
        assert parse_seek("30") == 30.0

    def test_relative_forward(self):
        assert parse_seek("+5") == "+5"

    def test_relative_backward(self):
        assert parse_seek("-10") == "-10"

    def test_minutes_seconds(self):
        assert parse_seek("1:30") == 90.0

    def test_hours_minutes_seconds(self):
        assert parse_seek("1:02:30") == 3750.0

    def test_invalid_format(self):
        with pytest.raises(Exception):
            parse_seek("1:2:3:4")


class TestCLICommands:
    @pytest.fixture
    def runner(self):
        return CliRunner()

    def _ok(self, data=None):
        return {"id": "1", "ok": True, "data": data or {}}

    def _err(self, message="Unknown error"):
        return {"id": "1", "ok": False, "error": {"message": message}}

    def test_play(self, runner):
        with patch("atk.cli.send_command", return_value=self._ok({"state": "playing"})):
            result = runner.invoke(cli, ["play"])
            assert result.exit_code == 0

    def test_play_file(self, runner, tmp_path):
        test_file = tmp_path / "test.mp3"
        test_file.touch()
        with patch(
            "atk.cli.send_command", return_value=self._ok({"state": "playing"})
        ) as mock:
            result = runner.invoke(cli, ["play", str(test_file)])
            assert result.exit_code == 0
            mock.assert_called_once()
            args = mock.call_args[0]
            assert args[0] == "play"

    def test_pause(self, runner):
        with patch("atk.cli.send_command", return_value=self._ok({"state": "paused"})):
            result = runner.invoke(cli, ["pause"])
            assert result.exit_code == 0

    def test_stop(self, runner):
        with patch("atk.cli.send_command", return_value=self._ok({"state": "stopped"})):
            result = runner.invoke(cli, ["stop"])
            assert result.exit_code == 0

    def test_next(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"queue_position": 1})
        ):
            result = runner.invoke(cli, ["next"])
            assert result.exit_code == 0

    def test_prev(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"queue_position": 0})
        ):
            result = runner.invoke(cli, ["prev"])
            assert result.exit_code == 0

    def test_seek(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"position": 30})
        ) as mock:
            result = runner.invoke(cli, ["seek", "30"])
            assert result.exit_code == 0
            mock.assert_called_once_with("seek", {"pos": 30.0})

    def test_seek_relative(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"position": 35})
        ) as mock:
            result = runner.invoke(cli, ["seek", "+5"])
            assert result.exit_code == 0
            mock.assert_called_once_with("seek", {"pos": "+5"})

    def test_volume(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"volume": 50})
        ) as mock:
            result = runner.invoke(cli, ["volume", "50"])
            assert result.exit_code == 0
            mock.assert_called_once_with("volume", {"level": 50})

    def test_add(self, runner):
        with patch("atk.cli.send_command", return_value=self._ok({"queue_length": 1})):
            result = runner.invoke(cli, ["add", "/path/to/file.mp3"])
            assert result.exit_code == 0

    def test_remove(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"removed": "track"})
        ) as mock:
            result = runner.invoke(cli, ["remove", "0"])
            assert result.exit_code == 0
            mock.assert_called_once_with("remove", {"index": 0})

    def test_clear(self, runner):
        with patch("atk.cli.send_command", return_value=self._ok({"cleared": True})):
            result = runner.invoke(cli, ["clear"])
            assert result.exit_code == 0

    def test_queue(self, runner):
        with patch(
            "atk.cli.send_command",
            return_value=self._ok({"tracks": [], "current_index": 0}),
        ):
            result = runner.invoke(cli, ["queue"])
            assert result.exit_code == 0

    def test_jump(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"queue_position": 2})
        ) as mock:
            result = runner.invoke(cli, ["jump", "2"])
            assert result.exit_code == 0
            mock.assert_called_once_with("jump", {"index": 2})

    def test_status(self, runner):
        with patch(
            "atk.cli.send_command",
            return_value=self._ok(
                {
                    "state": "stopped",
                    "track": None,
                    "volume": 80,
                    "shuffle": False,
                    "repeat": "none",
                    "queue_length": 0,
                }
            ),
        ):
            result = runner.invoke(cli, ["status"])
            assert result.exit_code == 0

    def test_shuffle_on(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"shuffle": True})
        ) as mock:
            result = runner.invoke(cli, ["shuffle", "on"])
            assert result.exit_code == 0
            mock.assert_called_once_with("shuffle", {"enabled": True})

    def test_shuffle_off(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"shuffle": False})
        ) as mock:
            result = runner.invoke(cli, ["shuffle", "off"])
            assert result.exit_code == 0
            mock.assert_called_once_with("shuffle", {"enabled": False})

    def test_shuffle_toggle(self, runner):
        """Toggle shuffle when no argument given."""
        with patch("atk.cli.send_command") as mock:
            mock.side_effect = [
                self._ok({"state": "playing", "shuffle": False}),  # status query
                self._ok({"shuffle": True}),  # shuffle command
            ]
            result = runner.invoke(cli, ["shuffle"])
            assert result.exit_code == 0

    def test_repeat(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"repeat": "queue"})
        ) as mock:
            result = runner.invoke(cli, ["repeat", "queue"])
            assert result.exit_code == 0
            mock.assert_called_once_with("repeat", {"mode": "queue"})

    def test_repeat_cycle(self, runner):
        """Cycle repeat mode when no argument given."""
        with patch("atk.cli.send_command") as mock:
            mock.side_effect = [
                self._ok({"state": "playing", "repeat": "none"}),  # status query
                self._ok({"repeat": "queue"}),  # repeat command
            ]
            result = runner.invoke(cli, ["repeat"])
            assert result.exit_code == 0

    def test_rate(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"rate": 1.5})
        ) as mock:
            result = runner.invoke(cli, ["rate", "1.5"])
            assert result.exit_code == 0
            args = mock.call_args[0]
            assert args[0] == "rate"
            assert args[1]["speed"] == 1.5

    def test_rate_tape(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"rate": 1.5})
        ) as mock:
            result = runner.invoke(cli, ["rate", "--tape", "1.5"])
            assert result.exit_code == 0
            assert mock.call_args[0][1]["mode"] == "tape"

    def test_save(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"track_count": 5})
        ) as mock:
            result = runner.invoke(cli, ["save", "myplaylist"])
            assert result.exit_code == 0
            args = mock.call_args[0]
            assert args[0] == "save"
            assert args[1]["name"] == "myplaylist"

    def test_load(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"track_count": 5})
        ) as mock:
            result = runner.invoke(cli, ["load", "myplaylist"])
            assert result.exit_code == 0
            mock.assert_called_once_with("load", {"name": "myplaylist"})

    def test_playlists(self, runner):
        with patch("atk.cli.send_command", return_value=self._ok({"playlists": []})):
            result = runner.invoke(cli, ["playlists"])
            assert result.exit_code == 0

    def test_ping(self, runner):
        with patch("atk.cli.send_command", return_value=self._ok({"pong": True})):
            result = runner.invoke(cli, ["ping"])
            assert result.exit_code == 0

    def test_devices(self, runner):
        with patch("atk.cli.send_command", return_value=self._ok({"devices": []})):
            result = runner.invoke(cli, ["devices"])
            assert result.exit_code == 0

    def test_set_device(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"device_id": "0102"})
        ) as mock:
            result = runner.invoke(cli, ["set-device", "0102"])
            assert result.exit_code == 0
            mock.assert_called_once_with("set-device", {"device_id": "0102"})

    def test_set_device_default(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._ok({"device_id": None})
        ) as mock:
            result = runner.invoke(cli, ["set-device"])
            assert result.exit_code == 0
            mock.assert_called_once_with("set-device", {"device_id": None})

    def test_json_output(self, runner):
        resp = self._ok({"state": "stopped", "track": None, "volume": 80})
        with patch("atk.cli.send_command", return_value=resp):
            result = runner.invoke(cli, ["--json", "status"])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data["ok"] is True

    def test_error_response(self, runner):
        with patch(
            "atk.cli.send_command", return_value=self._err("Invalid queue index: 99")
        ):
            result = runner.invoke(cli, ["jump", "99"])
            assert result.exit_code == 1
            assert "Error" in result.output
