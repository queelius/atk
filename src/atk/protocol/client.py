"""Pipe client helper for communicating with ATK daemon."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import AsyncIterator, Callable

from .messages import Event, Request, Response, parse_message


def get_runtime_dir() -> Path:
    """Get the ATK runtime directory."""
    if env_dir := os.environ.get("ATK_RUNTIME_DIR"):
        return Path(env_dir)

    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return Path(xdg_runtime) / "atk"

    return Path(f"/tmp/atk-{os.getlogin()}")


class PipeClient:
    """Client for communicating with ATK daemon via named pipes."""

    def __init__(self, cmd_pipe: Path, resp_pipe: Path):
        self.cmd_pipe = cmd_pipe
        self.resp_pipe = resp_pipe
        self._pending: dict[str, asyncio.Future[Response]] = {}
        self._event_handlers: list[Callable[[Event], None]] = []
        self._reader_task: asyncio.Task | None = None
        self._running = False

    @classmethod
    def for_daemon(cls) -> PipeClient:
        """Create client for the daemon."""
        runtime = get_runtime_dir()
        return cls(
            cmd_pipe=runtime / "atk.cmd",
            resp_pipe=runtime / "atk.resp",
        )

    def add_event_handler(self, handler: Callable[[Event], None]) -> None:
        """Add handler for events."""
        self._event_handlers.append(handler)

    async def connect(self) -> None:
        """Open pipes and start listening for responses."""
        self._running = True
        self._reader_task = asyncio.create_task(self._read_responses())

    async def disconnect(self) -> None:
        """Stop listening and close pipes."""
        self._running = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

    async def _read_responses(self) -> None:
        """Read responses from the response pipe."""
        while self._running:
            try:
                # Open response pipe for reading (non-blocking via asyncio)
                loop = asyncio.get_event_loop()

                # Use thread executor for blocking pipe read
                def read_line() -> str | None:
                    try:
                        with open(self.resp_pipe, "r") as f:
                            return f.readline()
                    except (FileNotFoundError, BrokenPipeError):
                        return None

                line = await loop.run_in_executor(None, read_line)
                if not line:
                    await asyncio.sleep(0.1)
                    continue

                line = line.strip()
                if not line:
                    continue

                msg = parse_message(line)

                if isinstance(msg, Response):
                    if msg.id in self._pending:
                        self._pending[msg.id].set_result(msg)
                        del self._pending[msg.id]
                elif isinstance(msg, Event):
                    for handler in self._event_handlers:
                        handler(msg)

            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.1)

    async def send(
        self, cmd: str, args: dict | None = None, timeout: float = 5.0
    ) -> Response:
        """Send a command and wait for response."""
        request = Request(cmd=cmd, args=args or {})

        # Create future for response
        future: asyncio.Future[Response] = asyncio.Future()
        self._pending[request.id] = future

        # Write to command pipe
        loop = asyncio.get_event_loop()

        def write_request() -> None:
            with open(self.cmd_pipe, "w") as f:
                f.write(request.serialize() + "\n")
                f.flush()

        await loop.run_in_executor(None, write_request)

        # Wait for response
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            del self._pending[request.id]
            raise

    def send_sync(
        self, cmd: str, args: dict | None = None, timeout: float = 5.0
    ) -> Response:
        """Synchronous send for CLI use."""
        request = Request(cmd=cmd, args=args or {})

        # Write request
        with open(self.cmd_pipe, "w") as f:
            f.write(request.serialize() + "\n")
            f.flush()

        # Read response (blocking)
        import select

        with open(self.resp_pipe, "r") as f:
            # Use select for timeout
            ready, _, _ = select.select([f], [], [], timeout)
            if not ready:
                raise TimeoutError(f"No response within {timeout}s")

            while True:
                line = f.readline().strip()
                if not line:
                    continue
                msg = parse_message(line)
                if isinstance(msg, Response) and msg.id == request.id:
                    return msg
                elif isinstance(msg, Event):
                    # Skip events in sync mode
                    continue

    async def subscribe(self) -> AsyncIterator[Event]:
        """Subscribe to events and yield them."""
        request = Request(cmd="subscribe")

        def write_request() -> None:
            with open(self.cmd_pipe, "w") as f:
                f.write(request.serialize() + "\n")
                f.flush()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, write_request)

        # Read events
        def read_line() -> str | None:
            try:
                with open(self.resp_pipe, "r") as f:
                    return f.readline()
            except (FileNotFoundError, BrokenPipeError):
                return None

        while True:
            line = await loop.run_in_executor(None, read_line)
            if not line:
                await asyncio.sleep(0.1)
                continue

            line = line.strip()
            if not line:
                continue

            msg = parse_message(line)
            if isinstance(msg, Event):
                yield msg
            elif isinstance(msg, Response) and msg.id == request.id:
                # Initial subscribe response
                if not msg.ok:
                    raise RuntimeError(f"Subscribe failed: {msg.error}")


def is_daemon_running() -> bool:
    """Check if the daemon is running by testing pipe existence."""
    runtime = get_runtime_dir()
    cmd_pipe = runtime / "atk.cmd"
    resp_pipe = runtime / "atk.resp"
    return cmd_pipe.exists() and resp_pipe.exists()
