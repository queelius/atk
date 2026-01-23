"""Asyncio-based named pipe handler for IPC."""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path
from typing import Callable, Awaitable

from ..protocol.messages import Request, Response, Event, ErrorInfo, ErrorCode, parse_message


class PipeHandler:
    """Handles named pipe I/O for a single command/response pair."""

    def __init__(
        self,
        cmd_pipe: Path,
        resp_pipe: Path,
        handler: Callable[[Request], Awaitable[Response]],
    ):
        self.cmd_pipe = cmd_pipe
        self.resp_pipe = resp_pipe
        self._handler = handler
        self._running = False
        self._task: asyncio.Task | None = None
        self._subscribers: list[asyncio.Queue[Event]] = []

    async def start(self) -> None:
        """Create pipes and start handling requests."""
        # Create parent directories
        self.cmd_pipe.parent.mkdir(parents=True, exist_ok=True)

        # Create named pipes if they don't exist
        for pipe in (self.cmd_pipe, self.resp_pipe):
            if not pipe.exists():
                os.mkfifo(pipe, mode=0o600)

        self._running = True
        self._task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        """Stop handling and clean up pipes."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        # Clean up pipes
        for pipe in (self.cmd_pipe, self.resp_pipe):
            if pipe.exists():
                pipe.unlink()

    async def _read_loop(self) -> None:
        """Main loop reading commands from pipe."""
        loop = asyncio.get_event_loop()

        while self._running:
            try:
                # Read from command pipe using thread executor
                def read_commands() -> list[str]:
                    lines = []
                    try:
                        # Open pipe for reading (blocks until writer connects)
                        with open(self.cmd_pipe, "r") as f:
                            for line in f:
                                line = line.strip()
                                if line:
                                    lines.append(line)
                    except (OSError, IOError):
                        pass
                    return lines

                lines = await loop.run_in_executor(None, read_commands)

                for line in lines:
                    await self._process_line(line)

            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log error but continue
                print(f"Pipe handler error: {e}")
                await asyncio.sleep(0.1)

    async def _process_line(self, line: str) -> None:
        """Process a single command line."""
        try:
            msg = parse_message(line)
            if not isinstance(msg, Request):
                return

            # Handle request
            response = await self._handler(msg)
            await self._send_response(response)

        except Exception as e:
            # Send error response
            try:
                data = __import__("json").loads(line)
                request_id = data.get("id", "unknown")
            except Exception:
                request_id = "unknown"

            error = ErrorInfo(
                code=ErrorCode.INVALID_MESSAGE,
                category="protocol",
                message=str(e),
            )
            response = Response.failure(request_id, error)
            await self._send_response(response)

    async def _send_response(self, response: Response) -> None:
        """Send response to response pipe."""
        loop = asyncio.get_event_loop()

        def write_response() -> None:
            try:
                with open(self.resp_pipe, "w") as f:
                    f.write(response.serialize() + "\n")
                    f.flush()
            except (OSError, IOError) as e:
                print(f"Failed to write response: {e}")

        await loop.run_in_executor(None, write_response)

    async def emit_event(self, event: Event) -> None:
        """Send event to all subscribers."""
        for queue in self._subscribers:
            await queue.put(event)

    def add_subscriber(self) -> asyncio.Queue[Event]:
        """Add a new event subscriber and return their queue."""
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.append(queue)
        return queue

    def remove_subscriber(self, queue: asyncio.Queue[Event]) -> None:
        """Remove a subscriber."""
        if queue in self._subscribers:
            self._subscribers.remove(queue)


class MultiClientPipeHandler:
    """
    Handles multiple simultaneous clients on the same pipe pair.

    Uses a sentinel file to detect new connections and spawns
    tasks to handle each client.
    """

    def __init__(
        self,
        cmd_pipe: Path,
        resp_pipe: Path,
        handler: Callable[[Request], Awaitable[Response]],
    ):
        self.cmd_pipe = cmd_pipe
        self.resp_pipe = resp_pipe
        self._handler = handler
        self._running = False
        self._task: asyncio.Task | None = None
        self._subscribers: dict[str, asyncio.Queue[Event]] = {}
        self._response_queues: dict[str, asyncio.Queue[Response | Event]] = {}

    async def start(self) -> None:
        """Create pipes and start handling requests."""
        self.cmd_pipe.parent.mkdir(parents=True, exist_ok=True)

        for pipe in (self.cmd_pipe, self.resp_pipe):
            if pipe.exists():
                pipe.unlink()
            os.mkfifo(pipe, mode=0o600)

        self._running = True
        self._task = asyncio.create_task(self._main_loop())

    async def stop(self) -> None:
        """Stop handling and clean up."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        for pipe in (self.cmd_pipe, self.resp_pipe):
            if pipe.exists():
                pipe.unlink()

    async def _main_loop(self) -> None:
        """Main loop handling all clients."""
        loop = asyncio.get_event_loop()

        # Start response writer task
        writer_task = asyncio.create_task(self._response_writer())

        try:
            while self._running:
                def read_line() -> str | None:
                    try:
                        with open(self.cmd_pipe, "r") as f:
                            line = f.readline()
                            return line.strip() if line else None
                    except (OSError, IOError):
                        return None

                line = await loop.run_in_executor(None, read_line)
                if line:
                    await self._handle_request(line)

        except asyncio.CancelledError:
            pass
        finally:
            writer_task.cancel()
            try:
                await writer_task
            except asyncio.CancelledError:
                pass

    async def _handle_request(self, line: str) -> None:
        """Handle a single request line."""
        try:
            msg = parse_message(line)
            if not isinstance(msg, Request):
                return

            # Special handling for subscribe command
            if msg.cmd == "subscribe":
                queue: asyncio.Queue[Response | Event] = asyncio.Queue()
                self._subscribers[msg.id] = queue
                self._response_queues[msg.id] = queue

                # Send initial OK response
                response = Response.success(msg.id, {"subscribed": True})
                await queue.put(response)
                return

            # Regular command - get response and queue it
            response = await self._handler(msg)

            # Create queue for this request if needed
            if msg.id not in self._response_queues:
                self._response_queues[msg.id] = asyncio.Queue()

            await self._response_queues[msg.id].put(response)

        except Exception as e:
            try:
                data = __import__("json").loads(line)
                request_id = data.get("id", "unknown")
            except Exception:
                request_id = "unknown"

            error = ErrorInfo(
                code=ErrorCode.INVALID_MESSAGE,
                category="protocol",
                message=str(e),
            )
            response = Response.failure(request_id, error)

            if request_id not in self._response_queues:
                self._response_queues[request_id] = asyncio.Queue()
            await self._response_queues[request_id].put(response)

    async def _response_writer(self) -> None:
        """Write responses to the response pipe."""
        loop = asyncio.get_event_loop()

        while self._running:
            # Collect all pending responses
            to_send: list[str] = []
            to_remove: list[str] = []

            for req_id, queue in list(self._response_queues.items()):
                try:
                    # Non-blocking check
                    msg = queue.get_nowait()
                    to_send.append(msg.serialize())

                    # Remove non-subscriber queues after response
                    if req_id not in self._subscribers:
                        to_remove.append(req_id)

                except asyncio.QueueEmpty:
                    pass

            for req_id in to_remove:
                del self._response_queues[req_id]

            if to_send:
                def write_lines() -> None:
                    try:
                        with open(self.resp_pipe, "w") as f:
                            for line in to_send:
                                f.write(line + "\n")
                            f.flush()
                    except (OSError, IOError):
                        pass

                await loop.run_in_executor(None, write_lines)

            await asyncio.sleep(0.05)  # Small delay to batch responses

    async def emit_event(self, event: Event) -> None:
        """Emit event to all subscribers."""
        for queue in self._subscribers.values():
            await queue.put(event)
