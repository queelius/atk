"""ATK TUI Application."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import Static, Footer

from .widgets import (
    NowPlaying,
    ProgressDisplay,
    QueuePreview,
    StatusBar,
    HelpBar,
)
from ..protocol.messages import Event, EventType, parse_message, Request
from ..config import get_runtime_dir


class ATKApp(App):
    """ATK Terminal User Interface."""

    CSS = """
    Screen {
        background: $surface;
    }

    #main-container {
        height: 100%;
    }

    #content {
        height: 1fr;
        padding: 1;
    }

    .hidden {
        display: none;
    }
    """

    BINDINGS = [
        Binding("space", "toggle_playback", "Play/Pause", show=True),
        Binding("left", "seek_back", "Seek -5s"),
        Binding("right", "seek_forward", "Seek +5s"),
        Binding("up", "volume_up", "Volume +5"),
        Binding("down", "volume_down", "Volume -5"),
        Binding("n", "next_track", "Next"),
        Binding("p", "prev_track", "Previous"),
        Binding("s", "toggle_shuffle", "Shuffle"),
        Binding("r", "cycle_repeat", "Repeat"),
        Binding("a", "add_file", "Add file"),
        Binding("d", "remove_current", "Remove"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, session: str = "default"):
        super().__init__()
        self.session = session
        self._runtime_dir = get_runtime_dir()
        self._event_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        with Container(id="main-container"):
            with Vertical(id="content"):
                yield NowPlaying(id="now-playing")
                yield ProgressDisplay(id="progress")
                yield QueuePreview(id="queue-preview")
        yield HelpBar()

    async def on_mount(self) -> None:
        """Start event listening and initial status fetch."""
        # Ensure daemon is running
        from ..cli.commands import ensure_daemon, send_registry_command

        ensure_daemon()

        # Ensure session exists
        cmd_pipe = self._runtime_dir / "sessions" / f"{self.session}.cmd"
        if not cmd_pipe.exists():
            send_registry_command("spawn", {"name": self.session})
            await asyncio.sleep(0.3)

        # Initial status fetch
        await self._fetch_status()

        # Start event listener
        self._event_task = asyncio.create_task(self._listen_events())

        # Start periodic status updates (backup for events)
        self._status_task = asyncio.create_task(self._periodic_status())

    async def on_unmount(self) -> None:
        """Clean up tasks."""
        if self._event_task:
            self._event_task.cancel()
        if self._status_task:
            self._status_task.cancel()

    async def _fetch_status(self) -> None:
        """Fetch current status from session."""
        try:
            from ..cli.commands import send_session_command

            response = send_session_command(self.session, "status")
            if response.ok and response.data:
                self._update_from_status(response.data)

            # Also fetch queue
            queue_response = send_session_command(self.session, "queue")
            if queue_response.ok and queue_response.data:
                self._update_queue(queue_response.data)

        except Exception as e:
            self.notify(f"Failed to fetch status: {e}", severity="error")

    def _update_from_status(self, data: dict) -> None:
        """Update UI from status data."""
        status_bar = self.query_one("#status-bar", StatusBar)
        status_bar.state = data.get("state", "stopped")
        status_bar.volume = data.get("volume", 80)
        status_bar.shuffle = data.get("shuffle", False)
        status_bar.repeat = data.get("repeat", "none")
        status_bar.session_name = self.session

        now_playing = self.query_one("#now-playing", NowPlaying)
        track = data.get("track")
        if track:
            now_playing.title = track.get("title") or track.get("uri", "Unknown").split("/")[-1]
            now_playing.artist = track.get("artist", "")
            now_playing.album = track.get("album", "")
        else:
            now_playing.title = "No track loaded"
            now_playing.artist = ""
            now_playing.album = ""

        progress = self.query_one("#progress", ProgressDisplay)
        progress.position = data.get("position", 0.0)
        progress.duration = data.get("duration", 0.0)

    def _update_queue(self, data: dict) -> None:
        """Update queue display."""
        queue_preview = self.query_one("#queue-preview", QueuePreview)
        tracks = data.get("tracks", [])
        current = data.get("current_index", 0)
        queue_preview.update_queue(tracks, current)

    async def _listen_events(self) -> None:
        """Listen for events from session."""
        cmd_pipe = self._runtime_dir / "sessions" / f"{self.session}.cmd"
        resp_pipe = self._runtime_dir / "sessions" / f"{self.session}.resp"

        if not cmd_pipe.exists():
            return

        # Send subscribe request
        request = Request(cmd="subscribe")
        loop = asyncio.get_event_loop()

        def write_subscribe():
            with open(cmd_pipe, "w") as f:
                f.write(request.serialize() + "\n")
                f.flush()

        await loop.run_in_executor(None, write_subscribe)

        # Read events
        def read_line():
            try:
                with open(resp_pipe, "r") as f:
                    return f.readline().strip()
            except Exception:
                return None

        while True:
            try:
                line = await loop.run_in_executor(None, read_line)
                if line:
                    msg = parse_message(line)
                    if isinstance(msg, Event):
                        self._handle_event(msg)
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(1)

    def _handle_event(self, event: Event) -> None:
        """Handle incoming event."""
        if event.event == EventType.TRACK_CHANGED:
            track = event.data.get("track", {})
            now_playing = self.query_one("#now-playing", NowPlaying)
            now_playing.title = track.get("title") or track.get("uri", "Unknown").split("/")[-1]
            now_playing.artist = track.get("artist", "")
            now_playing.album = track.get("album", "")

        elif event.event == EventType.POSITION_UPDATE:
            progress = self.query_one("#progress", ProgressDisplay)
            progress.position = event.data.get("position", 0.0)
            progress.duration = event.data.get("duration", 0.0)

        elif event.event == EventType.PLAYBACK_STARTED:
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.state = "playing"

        elif event.event == EventType.PLAYBACK_PAUSED:
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.state = "paused"

        elif event.event == EventType.PLAYBACK_STOPPED:
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.state = "stopped"

        elif event.event == EventType.QUEUE_UPDATED:
            queue_data = event.data.get("queue", {})
            self._update_queue(queue_data)

        elif event.event == EventType.ERROR:
            self.notify(
                f"Error: {event.data.get('message', 'Unknown error')}",
                severity="error",
            )

    async def _periodic_status(self) -> None:
        """Periodically fetch status as backup."""
        while True:
            await asyncio.sleep(5)
            try:
                await self._fetch_status()
            except Exception:
                pass

    def _send_command(self, cmd: str, args: dict | None = None) -> None:
        """Send command to session."""
        try:
            from ..cli.commands import send_session_command

            response = send_session_command(self.session, cmd, args)
            if not response.ok:
                error_msg = response.error.message if response.error else "Unknown error"
                self.notify(f"Error: {error_msg}", severity="error")
        except Exception as e:
            self.notify(f"Command failed: {e}", severity="error")

    # Action handlers

    def action_toggle_playback(self) -> None:
        """Toggle play/pause."""
        status_bar = self.query_one("#status-bar", StatusBar)
        if status_bar.state == "playing":
            self._send_command("pause")
        else:
            self._send_command("play")

    def action_seek_back(self) -> None:
        """Seek back 5 seconds."""
        self._send_command("seek", {"pos": "-5"})

    def action_seek_forward(self) -> None:
        """Seek forward 5 seconds."""
        self._send_command("seek", {"pos": "+5"})

    def action_volume_up(self) -> None:
        """Increase volume by 5."""
        status_bar = self.query_one("#status-bar", StatusBar)
        new_vol = min(100, status_bar.volume + 5)
        self._send_command("volume", {"level": new_vol})
        status_bar.volume = new_vol

    def action_volume_down(self) -> None:
        """Decrease volume by 5."""
        status_bar = self.query_one("#status-bar", StatusBar)
        new_vol = max(0, status_bar.volume - 5)
        self._send_command("volume", {"level": new_vol})
        status_bar.volume = new_vol

    def action_next_track(self) -> None:
        """Skip to next track."""
        self._send_command("next")

    def action_prev_track(self) -> None:
        """Go to previous track."""
        self._send_command("prev")

    def action_toggle_shuffle(self) -> None:
        """Toggle shuffle mode."""
        status_bar = self.query_one("#status-bar", StatusBar)
        new_shuffle = not status_bar.shuffle
        self._send_command("shuffle", {"enabled": new_shuffle})
        status_bar.shuffle = new_shuffle

    def action_cycle_repeat(self) -> None:
        """Cycle repeat mode."""
        status_bar = self.query_one("#status-bar", StatusBar)
        modes = ["none", "queue", "track"]
        current_idx = modes.index(status_bar.repeat) if status_bar.repeat in modes else 0
        new_mode = modes[(current_idx + 1) % len(modes)]
        self._send_command("repeat", {"mode": new_mode})
        status_bar.repeat = new_mode

    def action_add_file(self) -> None:
        """Add file (placeholder - would need file picker)."""
        self.notify("File picker not implemented yet", severity="warning")

    def action_remove_current(self) -> None:
        """Remove current track from queue."""
        from ..cli.commands import send_session_command

        response = send_session_command(self.session, "status")
        if response.ok and response.data:
            idx = response.data.get("queue_position", 0)
            self._send_command("remove", {"index": idx})


def main(session: str = "default") -> None:
    """Main entry point for TUI."""
    app = ATKApp(session=session)
    app.run()


if __name__ == "__main__":
    main()
