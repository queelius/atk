"""Session registry for managing multiple playback sessions."""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Callable, Awaitable

from ..protocol.messages import (
    Request,
    Response,
    Event,
    ErrorInfo,
    ErrorCode,
    SessionInfo,
)
from .session import Session, PlaybackState
from .pipe_handler import MultiClientPipeHandler


class Registry:
    """Manages playback sessions and their lifecycle."""

    def __init__(self, runtime_dir: Path):
        self.runtime_dir = runtime_dir
        self.sessions_dir = runtime_dir / "sessions"
        self._sessions: dict[str, SessionContext] = {}
        self._pipe_handler: MultiClientPipeHandler | None = None

    async def start(self) -> None:
        """Start the registry and its pipe handler."""
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        self._pipe_handler = MultiClientPipeHandler(
            cmd_pipe=self.runtime_dir / "registry.cmd",
            resp_pipe=self.runtime_dir / "registry.resp",
            handler=self._handle_request,
        )
        await self._pipe_handler.start()

    async def stop(self) -> None:
        """Stop the registry and all sessions."""
        # Stop all sessions
        for ctx in list(self._sessions.values()):
            await ctx.stop()

        # Stop registry pipe handler
        if self._pipe_handler:
            await self._pipe_handler.stop()

        # Clean up runtime directory
        if self.sessions_dir.exists():
            for f in self.sessions_dir.iterdir():
                f.unlink()
            self.sessions_dir.rmdir()

        for f in ["registry.cmd", "registry.resp"]:
            pipe = self.runtime_dir / f
            if pipe.exists():
                pipe.unlink()

    async def _handle_request(self, request: Request) -> Response:
        """Handle registry commands."""
        cmd = request.cmd
        args = request.args

        try:
            if cmd == "list":
                return await self._cmd_list(request.id)
            elif cmd == "spawn":
                return await self._cmd_spawn(request.id, args.get("name"))
            elif cmd == "kill":
                name = args.get("name")
                if not name:
                    raise ValueError("Session name required")
                return await self._cmd_kill(request.id, name)
            else:
                return Response.failure(
                    request.id,
                    ErrorInfo(
                        code=ErrorCode.UNKNOWN_COMMAND,
                        category="protocol",
                        message=f"Unknown command: {cmd}",
                    ),
                )
        except Exception as e:
            return Response.failure(
                request.id,
                ErrorInfo(
                    code=ErrorCode.INVALID_ARGS,
                    category="protocol",
                    message=str(e),
                ),
            )

    async def _cmd_list(self, request_id: str) -> Response:
        """List all active sessions."""
        sessions = []
        for name, ctx in self._sessions.items():
            session = ctx.session
            track = None
            if session.queue and session.queue_position < len(session.queue):
                uri = session.queue[session.queue_position]
                track = session._get_track_info(uri)

            sessions.append(
                SessionInfo(
                    name=name,
                    state=session.state.value,
                    track=track,
                ).to_dict()
            )

        return Response.success(request_id, {"sessions": sessions})

    async def _cmd_spawn(self, request_id: str, name: str | None) -> Response:
        """Spawn a new session."""
        # Generate name if not provided
        if not name:
            name = secrets.token_hex(6)

        # Check for existing session
        if name in self._sessions:
            return Response.failure(
                request_id,
                ErrorInfo(
                    code=ErrorCode.SESSION_EXISTS,
                    category="session",
                    message=f"Session '{name}' already exists",
                ),
            )

        # Create session
        ctx = SessionContext(name, self.sessions_dir)
        await ctx.start()
        self._sessions[name] = ctx

        return Response.success(
            request_id,
            {
                "name": name,
                "pipes": {
                    "cmd": str(ctx.cmd_pipe),
                    "resp": str(ctx.resp_pipe),
                },
            },
        )

    async def _cmd_kill(self, request_id: str, name: str) -> Response:
        """Kill a session."""
        if name not in self._sessions:
            return Response.failure(
                request_id,
                ErrorInfo(
                    code=ErrorCode.SESSION_NOT_FOUND,
                    category="session",
                    message=f"Session '{name}' not found",
                ),
            )

        ctx = self._sessions.pop(name)
        await ctx.stop()

        return Response.success(request_id, {"killed": name})

    def get_session(self, name: str) -> Session | None:
        """Get a session by name."""
        ctx = self._sessions.get(name)
        return ctx.session if ctx else None

    def get_or_create_default(self) -> Session:
        """Get or create the default session."""
        if "default" not in self._sessions:
            asyncio.create_task(self._cmd_spawn("internal", "default"))
        return self._sessions["default"].session


class SessionContext:
    """Context for a running session with its pipe handler."""

    def __init__(self, name: str, sessions_dir: Path):
        self.name = name
        self.cmd_pipe = sessions_dir / f"{name}.cmd"
        self.resp_pipe = sessions_dir / f"{name}.resp"
        self.session = Session(name=name)
        self._pipe_handler: MultiClientPipeHandler | None = None
        self._has_subscribers = False

    async def start(self) -> None:
        """Start the session and its pipe handler."""
        self._pipe_handler = MultiClientPipeHandler(
            cmd_pipe=self.cmd_pipe,
            resp_pipe=self.resp_pipe,
            handler=self._handle_request,
        )

        # Set up event emission
        self.session.set_event_callback(self._emit_event)

        await self._pipe_handler.start()
        await self.session.start_position_updates()

    async def stop(self) -> None:
        """Stop the session and pipe handler."""
        await self.session.stop_position_updates()
        self.session.player.stop()

        if self._pipe_handler:
            await self._pipe_handler.stop()

    def _emit_event(self, event: Event) -> None:
        """Emit event to pipe handler."""
        if self._pipe_handler and self._has_subscribers:
            asyncio.create_task(self._pipe_handler.emit_event(event))

    async def _handle_request(self, request: Request) -> Response:
        """Handle session commands."""
        cmd = request.cmd
        args = request.args

        try:
            # Map commands to session methods
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
            elif cmd == "shuffle":
                enabled = args.get("enabled", False)
                data = await self.session.cmd_shuffle(enabled)
            elif cmd == "repeat":
                mode = args.get("mode", "none")
                data = await self.session.cmd_repeat(mode)
            elif cmd == "rate":
                speed = args.get("speed", 1.0)
                data = await self.session.cmd_rate(speed)
            elif cmd == "pitch":
                semitones = args.get("semitones", 0.0)
                data = await self.session.cmd_pitch(semitones)
            elif cmd == "bass":
                db = args.get("db", 0.0)
                data = await self.session.cmd_bass(db)
            elif cmd == "treble":
                db = args.get("db", 0.0)
                data = await self.session.cmd_treble(db)
            elif cmd == "fade":
                to = args.get("to", 0)
                duration = args.get("duration", 1.0)
                data = await self.session.cmd_fade(to, duration)
            elif cmd == "loop":
                a = args.get("a")
                b = args.get("b")
                enabled = args.get("enabled")
                data = await self.session.cmd_loop(a=a, b=b, enabled=enabled)
            elif cmd == "queue":
                data = await self.session.cmd_queue()
            elif cmd == "status":
                data = await self.session.cmd_status()
            elif cmd == "info":
                index = args.get("index")
                data = await self.session.cmd_info(index)
            elif cmd == "subscribe":
                # Handled by pipe handler directly
                self._has_subscribers = True
                return Response.success(request.id, {"subscribed": True})
            elif cmd == "save":
                name = args.get("name")
                if not name:
                    raise ValueError("Name required")
                data = await self._save_state(name)
            elif cmd == "load":
                name = args.get("name")
                if not name:
                    raise ValueError("Name required")
                data = await self._load_state(name)
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
            return Response.failure(
                request.id,
                ErrorInfo(
                    code=ErrorCode.INVALID_ARGS,
                    category="internal",
                    message=str(e),
                ),
            )

    async def _save_state(self, name: str) -> dict:
        """Save session state to file."""
        import json
        from ..config import get_state_dir

        state_dir = get_state_dir() / "sessions"
        state_dir.mkdir(parents=True, exist_ok=True)

        state_file = state_dir / f"{name}.json"
        state = self.session.to_dict()

        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)

        return {"saved": str(state_file)}

    async def _load_state(self, name: str) -> dict:
        """Load session state from file."""
        import json
        from ..config import get_state_dir

        state_dir = get_state_dir() / "sessions"
        state_file = state_dir / f"{name}.json"

        if not state_file.exists():
            raise FileNotFoundError(f"State file not found: {state_file}")

        with open(state_file, "r") as f:
            state = json.load(f)

        # Apply state to current session
        self.session.queue = state.get("queue", [])
        self.session.queue_position = state.get("current_index", 0)
        self.session.shuffle = state.get("shuffle", False)
        self.session.shuffle_order = state.get("shuffle_order", [])
        self.session.repeat = state.get("repeat", "none")
        self.session.volume = state.get("volume", 80)
        self.session.player.set_volume(self.session.volume)

        return {"loaded": str(state_file)}
