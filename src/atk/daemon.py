"""ATK daemon — pipe handler, command dispatch, queue state, entry point."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import signal
import sys
from pathlib import Path

from .config import get_data_dir, get_runtime_dir, get_state_dir
from .player import Player, is_supported, list_devices

_logger = logging.getLogger("atk")


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------


class Daemon:
    """Single-instance ATK daemon managing playback, queue, and pipe IPC."""

    def __init__(self, runtime_dir: Path):
        self.runtime_dir = runtime_dir
        self.cmd_pipe = runtime_dir / "atk.cmd"
        self.resp_pipe = runtime_dir / "atk.resp"
        self.player = Player()

        # Queue state
        self.queue: list[str] = []
        self.queue_pos = 0
        self.shuffle = False
        self.shuffle_order: list[int] = []
        self.repeat = "none"  # none | queue | track
        self.volume = 80
        self.state = "stopped"  # stopped | playing | paused
        self.rate = 1.0

        # IPC state
        self._running = False
        self._read_task: asyncio.Task | None = None
        self._writer_task: asyncio.Task | None = None
        self._position_task: asyncio.Task | None = None
        self._resp_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        self._has_subscribers = False

        self.player.set_end_callback(self._on_track_end)
        self.player.set_volume(self.volume)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        for pipe in (self.cmd_pipe, self.resp_pipe):
            if pipe.exists():
                pipe.unlink()
            os.mkfifo(pipe, mode=0o600)

        self._running = True
        self._read_task = asyncio.create_task(self._read_loop())
        self._writer_task = asyncio.create_task(self._write_loop())
        self._position_task = asyncio.create_task(self._position_loop())
        _logger.info("Daemon started, pipes at %s", self.runtime_dir)

    async def stop(self) -> None:
        self._running = False
        self.player.stop()
        for task in (self._read_task, self._writer_task, self._position_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        for pipe in (self.cmd_pipe, self.resp_pipe):
            if pipe.exists():
                pipe.unlink()
        _logger.info("Daemon stopped")

    # ── Pipe I/O ───────────────────────────────────────────────────────────

    async def _read_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:

                def read_line() -> str | None:
                    try:
                        with open(self.cmd_pipe, "r") as f:
                            return f.readline().strip()
                    except OSError:
                        return None

                line = await loop.run_in_executor(None, read_line)
                if line:
                    resp = await self._dispatch(line)
                    await self._resp_queue.put(json.dumps(resp))
            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.error("Read loop error: %s", e)
                await asyncio.sleep(0.1)

    async def _write_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                line = await asyncio.wait_for(self._resp_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            def write(text: str) -> None:
                try:
                    with open(self.resp_pipe, "w") as f:
                        f.write(text + "\n")
                        f.flush()
                except OSError:
                    pass

            try:
                await loop.run_in_executor(None, write, line)
            except asyncio.CancelledError:
                break

    async def _position_loop(self) -> None:
        while self._running:
            await asyncio.sleep(1)
            if self.state == "playing" and self._has_subscribers:
                await self._emit(
                    "position_update",
                    {
                        "position": self.player.get_position(),
                        "duration": self.player.get_duration(),
                    },
                )

    async def _emit(self, event: str, data: dict | None = None) -> None:
        msg = json.dumps({"event": event, "data": data or {}})
        try:
            self._resp_queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    # ── Command dispatch ───────────────────────────────────────────────────

    async def _dispatch(self, line: str) -> dict:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            return {"id": "unknown", "ok": False, "error": {"message": str(e)}}

        req_id = msg.get("id", "unknown")
        cmd = msg.get("cmd")
        args = msg.get("args", {})

        if not cmd:
            return {"id": req_id, "ok": False, "error": {"message": "No command"}}

        handlers = {
            "play": self._cmd_play,
            "pause": self._cmd_pause,
            "stop": self._cmd_stop,
            "next": self._cmd_next,
            "prev": self._cmd_prev,
            "seek": self._cmd_seek,
            "volume": self._cmd_volume,
            "rate": self._cmd_rate,
            "add": self._cmd_add,
            "remove": self._cmd_remove,
            "move": self._cmd_move,
            "clear": self._cmd_clear,
            "queue": self._cmd_queue,
            "jump": self._cmd_jump,
            "shuffle": self._cmd_shuffle,
            "repeat": self._cmd_repeat,
            "status": self._cmd_status,
            "info": self._cmd_info,
            "subscribe": self._cmd_subscribe,
            "save": self._cmd_save,
            "load": self._cmd_load,
            "playlists": self._cmd_playlists,
            "devices": self._cmd_devices,
            "set-device": self._cmd_set_device,
            "ping": self._cmd_ping,
            "shutdown": self._cmd_shutdown,
        }

        handler = handlers.get(cmd)
        if not handler:
            return {
                "id": req_id,
                "ok": False,
                "error": {"message": f"Unknown command: {cmd}"},
            }

        try:
            data = await handler(args)
            return {"id": req_id, "ok": True, "data": data}
        except FileNotFoundError as e:
            return {"id": req_id, "ok": False, "error": {"message": str(e)}}
        except (IndexError, ValueError) as e:
            return {"id": req_id, "ok": False, "error": {"message": str(e)}}
        except Exception as e:
            _logger.exception("Error handling %s", cmd)
            return {"id": req_id, "ok": False, "error": {"message": str(e)}}

    # ── Playback commands ──────────────────────────────────────────────────

    async def _cmd_play(self, args: dict) -> dict:
        file = args.get("file")
        if file:
            if not is_supported(file):
                raise ValueError(f"Unsupported format: {file}")
            self.queue.append(file)
            self.queue_pos = len(self.queue) - 1
            if self.shuffle:
                self._shuffle_insert(len(self.queue) - 1)
            await self._emit("queue_updated", {"queue": self._queue_data()})
            await self._play_current()
        elif self.state == "paused":
            self.player.unpause()
            self.state = "playing"
            await self._emit("playback_started")
        elif self.state == "stopped" and self.queue:
            await self._play_current()
        return {"state": self.state}

    async def _cmd_pause(self, args: dict) -> dict:
        if self.state == "playing":
            self.player.pause()
            self.state = "paused"
            await self._emit(
                "playback_paused", {"position": self.player.get_position()}
            )
        return {"state": self.state}

    async def _cmd_stop(self, args: dict) -> dict:
        self.player.stop()
        self.state = "stopped"
        await self._emit("playback_stopped")
        return {"state": self.state}

    async def _cmd_next(self, args: dict) -> dict:
        if self._advance():
            await self._play_current()
            return {"queue_position": self.queue_pos}
        return {"error": "End of queue"}

    async def _cmd_prev(self, args: dict) -> dict:
        if self._go_previous():
            await self._play_current()
            return {"queue_position": self.queue_pos}
        return {"error": "Start of queue"}

    async def _cmd_seek(self, args: dict) -> dict:
        pos = args.get("pos", 0)
        current = self.player.get_position()
        if isinstance(pos, str):
            if pos.startswith("+"):
                pos = current + float(pos[1:])
            elif pos.startswith("-"):
                pos = current - float(pos[1:])
            else:
                pos = float(pos)
        pos = max(0.0, float(pos))
        self.player.seek(pos)
        return {"position": pos}

    async def _cmd_volume(self, args: dict) -> dict:
        level = max(0, min(100, int(args.get("level", 80))))
        self.volume = level
        self.player.set_volume(level)
        return {"volume": self.volume}

    async def _cmd_rate(self, args: dict) -> dict:
        speed = max(0.25, min(4.0, float(args.get("speed", 1.0))))
        mode = args.get("mode")  # "stretch" or "tape", optional
        self.rate = speed
        self.player.set_rate(speed, mode)
        return {"rate": self.rate}

    # ── Queue commands ─────────────────────────────────────────────────────

    async def _cmd_add(self, args: dict) -> dict:
        uri = args.get("uri")
        if not uri:
            raise ValueError("URI required")
        if not is_supported(uri):
            raise ValueError(f"Unsupported format: {uri}")
        self.queue.append(uri)
        if self.shuffle:
            self._shuffle_insert(len(self.queue) - 1)
        await self._emit("queue_updated", {"queue": self._queue_data()})
        return {"queue_length": len(self.queue)}

    async def _cmd_remove(self, args: dict) -> dict:
        idx = args.get("index")
        if idx is None:
            raise ValueError("Index required")
        idx = int(idx)
        if idx < 0 or idx >= len(self.queue):
            raise IndexError(f"Invalid queue index: {idx}")

        removed = self.queue.pop(idx)
        if idx < self.queue_pos:
            self.queue_pos -= 1
        elif idx == self.queue_pos and self.state == "playing":
            if self.queue_pos < len(self.queue):
                await self._play_current()
            else:
                self.player.stop()
                self.state = "stopped"

        if self.shuffle and self.shuffle_order:
            try:
                self.shuffle_order.remove(idx)
            except ValueError:
                pass
            self.shuffle_order = [i if i < idx else i - 1 for i in self.shuffle_order]

        await self._emit("queue_updated", {"queue": self._queue_data()})
        return {"removed": removed}

    async def _cmd_move(self, args: dict) -> dict:
        from_idx, to_idx = int(args.get("from", -1)), int(args.get("to", -1))
        if not (0 <= from_idx < len(self.queue) and 0 <= to_idx < len(self.queue)):
            raise IndexError("Invalid index")
        track = self.queue.pop(from_idx)
        self.queue.insert(to_idx, track)
        if from_idx == self.queue_pos:
            self.queue_pos = to_idx
        elif from_idx < self.queue_pos <= to_idx:
            self.queue_pos -= 1
        elif to_idx <= self.queue_pos < from_idx:
            self.queue_pos += 1
        await self._emit("queue_updated", {"queue": self._queue_data()})
        return {"queue_position": self.queue_pos}

    async def _cmd_clear(self, args: dict) -> dict:
        self.player.stop()
        self.state = "stopped"
        self.queue.clear()
        self.queue_pos = 0
        self.shuffle_order.clear()
        await self._emit("queue_updated", {"queue": self._queue_data()})
        return {"cleared": True}

    async def _cmd_queue(self, args: dict) -> dict:
        return self._queue_data()

    async def _cmd_jump(self, args: dict) -> dict:
        idx = int(args.get("index", -1))
        if idx < 0 or idx >= len(self.queue):
            raise IndexError(f"Invalid queue index: {idx}")
        self.queue_pos = idx
        await self._play_current()
        return {"queue_position": self.queue_pos}

    # ── Mode commands ──────────────────────────────────────────────────────

    async def _cmd_shuffle(self, args: dict) -> dict:
        self.shuffle = bool(args.get("enabled", False))
        if self.shuffle:
            self.shuffle_order = list(range(len(self.queue)))
            random.shuffle(self.shuffle_order)
            if self.queue_pos in self.shuffle_order:
                self.shuffle_order.remove(self.queue_pos)
                self.shuffle_order.insert(0, self.queue_pos)
        else:
            self.shuffle_order.clear()
        return {"shuffle": self.shuffle}

    async def _cmd_repeat(self, args: dict) -> dict:
        mode = args.get("mode", "none")
        if mode not in ("none", "queue", "track"):
            raise ValueError(f"Invalid repeat mode: {mode}")
        self.repeat = mode
        return {"repeat": self.repeat}

    # ── Status commands ────────────────────────────────────────────────────

    async def _cmd_status(self, args: dict) -> dict:
        uri = self.queue[self.queue_pos] if self.queue else None
        track = self._track_info(uri) if uri else None
        return {
            "state": self.state,
            "track": track,
            "position": self.player.get_position() if self.state != "stopped" else 0.0,
            "duration": self.player.get_duration() if uri else 0.0,
            "volume": self.volume,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "queue_length": len(self.queue),
            "queue_position": self.queue_pos,
            "rate": self.rate,
        }

    async def _cmd_info(self, args: dict) -> dict:
        idx = args.get("index", self.queue_pos)
        if idx is None:
            idx = self.queue_pos
        idx = int(idx)
        if idx < 0 or idx >= len(self.queue):
            raise IndexError(f"Invalid index: {idx}")
        return self._track_info(self.queue[idx])

    async def _cmd_subscribe(self, args: dict) -> dict:
        self._has_subscribers = True
        return {"subscribed": True}

    # ── Playlist commands ──────────────────────────────────────────────────

    async def _cmd_save(self, args: dict) -> dict:
        name = args.get("name")
        if not name:
            raise ValueError("Name required")
        fmt = args.get("format", "json")
        pldir = get_data_dir() / "playlists"
        pldir.mkdir(parents=True, exist_ok=True)

        if fmt == "json":
            path = pldir / f"{name}.json"
            path.write_text(json.dumps({"name": name, "tracks": self.queue}, indent=2))
        elif fmt == "m3u":
            path = pldir / f"{name}.m3u"
            path.write_text("#EXTM3U\n" + "\n".join(self.queue) + "\n")
        elif fmt == "txt":
            path = pldir / f"{name}.txt"
            path.write_text("\n".join(self.queue) + "\n")
        else:
            raise ValueError(f"Unsupported format: {fmt}")
        return {"saved": str(path), "track_count": len(self.queue)}

    async def _cmd_load(self, args: dict) -> dict:
        name = args.get("name")
        if not name:
            raise ValueError("Name required")
        pldir = get_data_dir() / "playlists"
        path = None
        for ext in (".json", ".m3u", ".txt"):
            p = pldir / f"{name}{ext}"
            if p.exists():
                path = p
                break
        if not path:
            raise FileNotFoundError(f"Playlist not found: {name}")

        if path.suffix == ".json":
            tracks = json.loads(path.read_text()).get("tracks", [])
        else:
            tracks = [
                line
                for line in path.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            ]

        await self._cmd_clear({})
        for t in tracks:
            if is_supported(t):
                self.queue.append(t)
        if self.shuffle:
            self.shuffle_order = list(range(len(self.queue)))
            random.shuffle(self.shuffle_order)
        return {"loaded": str(path), "track_count": len(self.queue)}

    async def _cmd_playlists(self, args: dict) -> dict:
        pldir = get_data_dir() / "playlists"
        playlists: list[dict] = []
        if pldir.exists():
            for f in pldir.iterdir():
                if f.suffix in (".json", ".m3u", ".txt"):
                    count = 0
                    try:
                        if f.suffix == ".json":
                            count = len(json.loads(f.read_text()).get("tracks", []))
                        else:
                            count = sum(
                                1
                                for ln in f.read_text().splitlines()
                                if ln.strip() and not ln.startswith("#")
                            )
                    except Exception:
                        pass
                    playlists.append(
                        {"name": f.stem, "track_count": count, "format": f.suffix[1:]}
                    )
        return {"playlists": playlists}

    # ── Device commands ────────────────────────────────────────────────────

    async def _cmd_devices(self, args: dict) -> dict:
        devs = list_devices()
        for d in devs:
            if isinstance(d.get("id"), bytes):
                d["id"] = d["id"].hex()
        return {"devices": devs}

    async def _cmd_set_device(self, args: dict) -> dict:
        did = args.get("device_id")
        dev_bytes: bytes | None = bytes.fromhex(did) if did else None
        self.player.set_device(dev_bytes)
        return {"device_id": did}

    async def _cmd_ping(self, args: dict) -> dict:
        return {"pong": True}

    async def _cmd_shutdown(self, args: dict) -> dict:
        asyncio.get_event_loop().call_soon(lambda: os.kill(os.getpid(), signal.SIGTERM))
        return {"shutting_down": True}

    # ── Queue helpers ──────────────────────────────────────────────────────

    async def _play_current(self) -> None:
        if not self.queue or self.queue_pos >= len(self.queue):
            return
        uri = self.queue[self.queue_pos]
        try:
            self.player.load(uri)
            self.player.play()
            self.state = "playing"
            track = self._track_info(uri)
            await self._emit(
                "track_changed", {"track": track, "queue_position": self.queue_pos}
            )
            await self._emit("playback_started", {"track": track})
        except (FileNotFoundError, ValueError) as e:
            await self._emit("error", {"message": str(e), "track": uri})
            if self._advance():
                await self._play_current()

    def _on_track_end(self) -> None:
        task = asyncio.create_task(self._handle_track_end())
        task.add_done_callback(
            lambda t: _logger.error("Track end error: %s", t.exception())
            if not t.cancelled() and t.exception()
            else None
        )

    async def _handle_track_end(self) -> None:
        if self.repeat == "track":
            await self._play_current()
            return
        if self._advance():
            await self._play_current()
        else:
            self.state = "stopped"
            await self._emit("queue_finished")

    def _advance(self) -> bool:
        if not self.queue:
            return False
        if self.shuffle:
            try:
                idx = self.shuffle_order.index(self.queue_pos)
            except ValueError:
                return self._advance_linear()
            nxt = idx + 1
            if nxt >= len(self.shuffle_order):
                if self.repeat == "queue":
                    random.shuffle(self.shuffle_order)
                    nxt = 0
                else:
                    return False
            self.queue_pos = self.shuffle_order[nxt]
        else:
            return self._advance_linear()
        return True

    def _advance_linear(self) -> bool:
        nxt = self.queue_pos + 1
        if nxt >= len(self.queue):
            if self.repeat == "queue":
                nxt = 0
            else:
                return False
        self.queue_pos = nxt
        return True

    def _go_previous(self) -> bool:
        if not self.queue:
            return False
        if self.shuffle:
            try:
                idx = self.shuffle_order.index(self.queue_pos)
            except ValueError:
                return self._go_prev_linear()
            prev = idx - 1
            if prev < 0:
                if self.repeat == "queue":
                    prev = len(self.shuffle_order) - 1
                else:
                    return False
            self.queue_pos = self.shuffle_order[prev]
        else:
            return self._go_prev_linear()
        return True

    def _go_prev_linear(self) -> bool:
        prev = self.queue_pos - 1
        if prev < 0:
            if self.repeat == "queue":
                prev = len(self.queue) - 1
            else:
                return False
        self.queue_pos = prev
        return True

    def _shuffle_insert(self, track_idx: int) -> None:
        """Insert new track at random position after current in shuffle order."""
        if self.shuffle_order:
            try:
                cur = self.shuffle_order.index(self.queue_pos)
                pos = random.randint(cur + 1, len(self.shuffle_order))
            except ValueError:
                pos = len(self.shuffle_order)
        else:
            pos = 0
        self.shuffle_order.insert(pos, track_idx)

    def _track_info(self, uri: str) -> dict:
        name = Path(uri).stem
        parts = name.split(" - ", 1)
        if len(parts) == 2:
            return {"uri": uri, "artist": parts[0], "title": parts[1]}
        return {"uri": uri, "title": name}

    def _queue_data(self) -> dict:
        return {
            "tracks": [self._track_info(u) for u in self.queue],
            "current_index": self.queue_pos,
        }


# ---------------------------------------------------------------------------
# Daemon runner + entry point
# ---------------------------------------------------------------------------


class _Runner:
    """Daemon process lifecycle: logging, PID file, signal handling."""

    def __init__(self) -> None:
        self.runtime_dir = get_runtime_dir()
        self.state_dir = get_state_dir()
        self.daemon: Daemon | None = None
        self._shutdown = asyncio.Event()

    async def run(self) -> None:
        self._setup_logging()
        _logger.info("Starting ATK daemon")

        if self._is_running():
            _logger.error("Another daemon is already running")
            sys.exit(1)

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        (self.runtime_dir / "daemon.pid").write_text(str(os.getpid()))

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown.set)

        self.daemon = Daemon(self.runtime_dir)
        await self.daemon.start()
        await self._shutdown.wait()
        await self.daemon.stop()

        pid = self.runtime_dir / "daemon.pid"
        if pid.exists():
            pid.unlink()
        _logger.info("ATK daemon stopped")

    def _setup_logging(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
        fh = logging.FileHandler(self.state_dir / "daemon.log")
        fh.setFormatter(fmt)
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        root = logging.getLogger("atk")
        root.setLevel(logging.INFO)
        root.addHandler(fh)
        root.addHandler(ch)

    def _is_running(self) -> bool:
        pid_file = self.runtime_dir / "daemon.pid"
        if not pid_file.exists():
            return False
        try:
            os.kill(int(pid_file.read_text().strip()), 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            # Stale — clean up
            for f in (
                pid_file,
                self.runtime_dir / "atk.cmd",
                self.runtime_dir / "atk.resp",
            ):
                if f.exists():
                    f.unlink()
            return False


def main() -> None:
    """Entry point for atk-daemon."""
    try:
        asyncio.run(_Runner().run())
    except KeyboardInterrupt:
        pass
