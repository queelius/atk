"""Single-instance ATK daemon."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from ..config import get_data_dir
from ..protocol.messages import (
    ErrorCode,
    ErrorInfo,
    Event,
    PlaylistInfo,
    Request,
    Response,
)
from .pipe_handler import MultiClientPipeHandler
from .player import list_audio_devices
from .session import Session

_logger = logging.getLogger("atk.daemon")


class Daemon:
    """Single-instance ATK daemon managing one playback session."""

    def __init__(self, runtime_dir: Path):
        self.runtime_dir = runtime_dir
        self.cmd_pipe = runtime_dir / "atk.cmd"
        self.resp_pipe = runtime_dir / "atk.resp"
        self.session = Session(name="default")
        self._pipe_handler: MultiClientPipeHandler | None = None
        self._has_subscribers = False

    async def start(self) -> None:
        """Start the daemon and its pipe handler."""
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        self._pipe_handler = MultiClientPipeHandler(
            cmd_pipe=self.cmd_pipe,
            resp_pipe=self.resp_pipe,
            handler=self._handle_request,
        )

        # Set up event emission
        self.session.set_event_callback(self._emit_event)

        await self._pipe_handler.start()
        await self.session.start_position_updates()

        _logger.info(f"Daemon started, pipes at {self.runtime_dir}")

    async def stop(self) -> None:
        """Stop the daemon and pipe handler."""
        await self.session.stop_position_updates()
        self.session.player.stop()

        if self._pipe_handler:
            await self._pipe_handler.stop()

        # Clean up pipes
        for pipe in [self.cmd_pipe, self.resp_pipe]:
            if pipe.exists():
                pipe.unlink()

        _logger.info("Daemon stopped")

    def _emit_event(self, event: Event) -> None:
        """Emit event to pipe handler."""
        if self._pipe_handler and self._has_subscribers:
            asyncio.create_task(self._pipe_handler.emit_event(event))

    async def _handle_request(self, request: Request) -> Response:
        """Handle incoming commands."""
        cmd = request.cmd
        args = request.args

        try:
            # Playback control
            if cmd == "play":
                data = await self.session.cmd_play(args.get("file"))
            elif cmd == "pause":
                data = await self.session.cmd_pause()
            elif cmd == "stop":
                data = await self.session.cmd_stop()
            elif cmd == "next":
                data = await self.session.cmd_next()
            elif cmd == "prev":
                data = await self.session.cmd_prev()
            elif cmd == "seek":
                pos = args.get("pos", 0)
                data = await self.session.cmd_seek(pos)
            elif cmd == "volume":
                level = args.get("level", 80)
                data = await self.session.cmd_volume(level)
            elif cmd == "rate":
                speed = args.get("speed", 1.0)
                data = await self.session.cmd_rate(speed)

            # Queue management
            elif cmd == "add":
                uri = args.get("uri")
                if not uri:
                    raise ValueError("URI required")
                data = await self.session.cmd_add(uri)
            elif cmd == "remove":
                index = args.get("index")
                if index is None:
                    raise ValueError("Index required")
                data = await self.session.cmd_remove(index)
            elif cmd == "move":
                from_idx = args.get("from")
                to_idx = args.get("to")
                if from_idx is None or to_idx is None:
                    raise ValueError("From and to indices required")
                data = await self.session.cmd_move(from_idx, to_idx)
            elif cmd == "clear":
                data = await self.session.cmd_clear()
            elif cmd == "queue":
                data = await self.session.cmd_queue()
            elif cmd == "jump":
                index = args.get("index")
                if index is None:
                    raise ValueError("Index required")
                data = await self.session.cmd_jump(index)

            # Playback modes
            elif cmd == "shuffle":
                enabled = args.get("enabled", False)
                data = await self.session.cmd_shuffle(enabled)
            elif cmd == "repeat":
                mode = args.get("mode", "none")
                data = await self.session.cmd_repeat(mode)

            # Status & info
            elif cmd == "status":
                data = await self.session.cmd_status()
            elif cmd == "info":
                index = args.get("index")
                data = await self.session.cmd_info(index)

            # Subscribe to events
            elif cmd == "subscribe":
                self._has_subscribers = True
                return Response.success(request.id, {"subscribed": True})

            # Playlists
            elif cmd == "save":
                name = args.get("name")
                fmt = args.get("format", "json")
                if not name:
                    raise ValueError("Name required")
                data = await self._save_playlist(name, fmt)
            elif cmd == "load":
                name = args.get("name")
                if not name:
                    raise ValueError("Name required")
                data = await self._load_playlist(name)
            elif cmd == "playlists":
                data = await self._list_playlists()

            # Daemon commands
            elif cmd == "ping":
                data = {"pong": True}
            elif cmd == "shutdown":
                # Signal shutdown - handled by main loop
                asyncio.get_event_loop().call_soon(
                    lambda: asyncio.create_task(self._signal_shutdown())
                )
                return Response.success(request.id, {"shutting_down": True})

            # Audio device commands
            elif cmd == "devices":
                devices = list_audio_devices()
                # Convert bytes to hex strings for JSON serialization
                for dev in devices:
                    if isinstance(dev.get("id"), bytes):
                        dev["id"] = dev["id"].hex()
                data = {"devices": devices}
            elif cmd == "set-device":
                device_id_str = args.get("device_id")
                # device_id can be None to reset to default
                device_id: bytes | None = None
                if (
                    device_id_str is not None
                    and isinstance(device_id_str, str)
                    and device_id_str
                ):
                    # Convert hex string back to bytes
                    device_id = bytes.fromhex(device_id_str)
                self.session.player.set_device(device_id)
                data = {"device_id": device_id_str}

            else:
                return Response.failure(
                    request.id,
                    ErrorInfo(
                        code=ErrorCode.UNKNOWN_COMMAND,
                        category="protocol",
                        message=f"Unknown command: {cmd}",
                    ),
                )

            return Response.success(request.id, data)

        except FileNotFoundError as e:
            return Response.failure(
                request.id,
                ErrorInfo(
                    code=ErrorCode.FILE_NOT_FOUND,
                    category="io",
                    message=str(e),
                ),
            )
        except IndexError as e:
            return Response.failure(
                request.id,
                ErrorInfo(
                    code=ErrorCode.INVALID_INDEX,
                    category="queue",
                    message=str(e),
                ),
            )
        except ValueError as e:
            return Response.failure(
                request.id,
                ErrorInfo(
                    code=ErrorCode.INVALID_ARGS,
                    category="protocol",
                    message=str(e),
                ),
            )
        except Exception as e:
            _logger.exception(f"Error handling command {cmd}")
            return Response.failure(
                request.id,
                ErrorInfo(
                    code=ErrorCode.INVALID_ARGS,
                    category="internal",
                    message=str(e),
                ),
            )

    async def _signal_shutdown(self) -> None:
        """Signal the daemon to shut down."""
        # This will be caught by the main loop
        import os
        import signal

        os.kill(os.getpid(), signal.SIGTERM)

    async def _save_playlist(self, name: str, fmt: str = "json") -> dict:
        """Save current queue as a playlist."""
        playlists_dir = get_data_dir() / "playlists"
        playlists_dir.mkdir(parents=True, exist_ok=True)

        if fmt == "json":
            playlist_file = playlists_dir / f"{name}.json"
            data = {
                "name": name,
                "tracks": self.session.queue,
            }
            with open(playlist_file, "w") as f:
                json.dump(data, f, indent=2)
        elif fmt == "m3u":
            playlist_file = playlists_dir / f"{name}.m3u"
            with open(playlist_file, "w") as f:
                f.write("#EXTM3U\n")
                for uri in self.session.queue:
                    f.write(f"{uri}\n")
        elif fmt == "txt":
            playlist_file = playlists_dir / f"{name}.txt"
            with open(playlist_file, "w") as f:
                for uri in self.session.queue:
                    f.write(f"{uri}\n")
        else:
            raise ValueError(f"Unsupported format: {fmt}")

        return {"saved": str(playlist_file), "track_count": len(self.session.queue)}

    async def _load_playlist(self, name: str) -> dict:
        """Load a playlist into the queue."""
        playlists_dir = get_data_dir() / "playlists"

        # Try different formats
        for ext in [".json", ".m3u", ".txt"]:
            playlist_file = playlists_dir / f"{name}{ext}"
            if playlist_file.exists():
                break
        else:
            raise FileNotFoundError(f"Playlist not found: {name}")

        tracks: list[str] = []

        if playlist_file.suffix == ".json":
            with open(playlist_file, "r") as f:
                data = json.load(f)
                tracks = data.get("tracks", [])
        elif playlist_file.suffix in [".m3u", ".txt"]:
            with open(playlist_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        tracks.append(line)

        # Clear and load
        await self.session.cmd_clear()
        for track in tracks:
            try:
                await self.session.cmd_add(track)
            except ValueError:
                # Skip unsupported formats
                _logger.warning(f"Skipping unsupported track: {track}")

        return {"loaded": str(playlist_file), "track_count": len(self.session.queue)}

    async def _list_playlists(self) -> dict:
        """List all saved playlists."""
        playlists_dir = get_data_dir() / "playlists"
        playlists: list[dict] = []

        if playlists_dir.exists():
            for f in playlists_dir.iterdir():
                if f.suffix in [".json", ".m3u", ".txt"]:
                    name = f.stem
                    track_count = 0

                    # Count tracks
                    if f.suffix == ".json":
                        try:
                            with open(f, "r") as fp:
                                data = json.load(fp)
                                track_count = len(data.get("tracks", []))
                        except Exception:
                            pass
                    else:
                        try:
                            with open(f, "r") as fp:
                                track_count = sum(
                                    1
                                    for line in fp
                                    if line.strip() and not line.startswith("#")
                                )
                        except Exception:
                            pass

                    playlists.append(
                        PlaylistInfo(
                            name=name,
                            track_count=track_count,
                            format=f.suffix[1:],
                        ).to_dict()
                    )

        return {"playlists": playlists}
