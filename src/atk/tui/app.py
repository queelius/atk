"""ATK TUI Application."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.widgets import DirectoryTree, Static

from ..config import get_runtime_dir
from ..protocol.messages import Event, EventType
from .widgets import (
    HelpBar,
    NowPlaying,
    ProgressDisplay,
    QueuePreview,
    StatusBar,
)

_logger = logging.getLogger("atk.tui")


class FilePicker(Static):
    """Simple file picker widget."""

    DEFAULT_CSS = """
    FilePicker {
        display: none;
        layer: overlay;
        width: 60%;
        height: 80%;
        margin: 2 4;
        border: solid $primary;
        background: $surface;
    }

    FilePicker.visible {
        display: block;
    }

    FilePicker DirectoryTree {
        height: 1fr;
    }

    FilePicker .title {
        text-style: bold;
        padding: 1;
        text-align: center;
    }
    """

    def __init__(self, start_path: str = "~"):
        super().__init__()
        self.start_path = Path(start_path).expanduser()

    def compose(self) -> ComposeResult:
        yield Static("Select File (Enter to select, Escape to cancel)", classes="title")
        yield DirectoryTree(str(self.start_path))

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        """Handle file selection."""
        self.app.file_selected(str(event.path))  # type: ignore[attr-defined]
        self.remove_class("visible")


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
        Binding("f", "open_file_picker", "Add file"),
        Binding("d", "remove_current", "Remove"),
        Binding("j", "jump_track", "Jump"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self):
        super().__init__()
        self._runtime_dir = get_runtime_dir()
        self._event_task: asyncio.Task | None = None
        self._status_task: asyncio.Task | None = None
        self._retry_count = 0
        self._max_retries = 5

    def compose(self) -> ComposeResult:
        yield StatusBar(id="status-bar")
        with Container(id="main-container"):
            with Vertical(id="content"):
                yield NowPlaying(id="now-playing")
                yield ProgressDisplay(id="progress")
                yield QueuePreview(id="queue-preview")
        yield HelpBar()
        yield FilePicker()

    async def on_mount(self) -> None:
        """Start event listening and initial status fetch."""
        from ..cli.commands import ensure_daemon

        try:
            ensure_daemon()
        except Exception as e:
            _logger.error(f"Failed to start daemon: {e}")
            self.notify(f"Failed to start daemon: {e}", severity="error")
            return

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
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
        if self._status_task:
            self._status_task.cancel()
            try:
                await self._status_task
            except asyncio.CancelledError:
                pass

    async def _fetch_status(self) -> None:
        """Fetch current status from daemon."""
        try:
            from ..cli.commands import send_command

            response = await asyncio.to_thread(send_command, "status")
            if response.ok and response.data:
                self._update_from_status(response.data)

            # Also fetch queue
            queue_response = await asyncio.to_thread(send_command, "queue")
            if queue_response.ok and queue_response.data:
                self._update_queue(queue_response.data)

        except Exception as e:
            _logger.warning(f"Failed to fetch status: {e}")

    def _update_from_status(self, data: dict) -> None:
        """Update UI from status data."""
        try:
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.state = data.get("state", "stopped")
            status_bar.volume = data.get("volume", 80)
            status_bar.shuffle = data.get("shuffle", False)
            status_bar.repeat = data.get("repeat", "none")

            now_playing = self.query_one("#now-playing", NowPlaying)
            track = data.get("track")
            if track:
                now_playing.title = (
                    track.get("title") or track.get("uri", "Unknown").split("/")[-1]
                )
                now_playing.artist = track.get("artist", "")
                now_playing.album = track.get("album", "")
            else:
                now_playing.title = "No track loaded"
                now_playing.artist = ""
                now_playing.album = ""

            progress = self.query_one("#progress", ProgressDisplay)
            progress.position = data.get("position", 0.0)
            progress.duration = data.get("duration", 0.0)
        except Exception as e:
            _logger.warning(f"Error updating status: {e}")

    def _update_queue(self, data: dict) -> None:
        """Update queue display."""
        try:
            queue_preview = self.query_one("#queue-preview", QueuePreview)
            tracks = data.get("tracks", [])
            current = data.get("current_index", 0)
            queue_preview.update_queue(tracks, current)
        except Exception as e:
            _logger.warning(f"Error updating queue: {e}")

    async def _listen_events(self) -> None:
        """Listen for events from daemon."""
        from ..cli.commands import subscribe_to_events

        while self._retry_count < self._max_retries:
            try:
                # Use thread for blocking event generator
                def event_gen():
                    for event in subscribe_to_events():
                        yield event

                asyncio.get_event_loop()
                for event in subscribe_to_events():
                    self._handle_event(event)
                    self._retry_count = 0  # Reset on success

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._retry_count += 1
                _logger.warning(
                    "Event loop error (retry %d/%d): %s",
                    self._retry_count,
                    self._max_retries,
                    e,
                )

                if self._retry_count >= self._max_retries:
                    self.notify("Lost connection to daemon", severity="warning")
                    break

                # Exponential backoff with limit (Bug fix 6.3)
                await asyncio.sleep(min(2**self._retry_count, 10))

    def _handle_event(self, event: Event) -> None:
        """Handle incoming event."""
        try:
            if event.event == EventType.TRACK_CHANGED:
                track = event.data.get("track", {})
                now_playing = self.query_one("#now-playing", NowPlaying)
                now_playing.title = (
                    track.get("title") or track.get("uri", "Unknown").split("/")[-1]
                )
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
                code = event.data.get("code", "?")
                msg = event.data.get("message", "Unknown error")
                self.notify(f"Error [{code}]: {msg}", severity="error")

        except Exception as e:
            _logger.warning(f"Error handling event: {e}")

    async def _periodic_status(self) -> None:
        """Periodically fetch status as backup."""
        while True:
            await asyncio.sleep(5)
            try:
                await self._fetch_status()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.warning(f"Periodic status error: {e}")

    def _send_command(self, cmd: str, args: dict | None = None) -> None:
        """Send command to daemon."""

        async def do_send():
            try:
                from ..cli.commands import send_command as _send

                response = await asyncio.to_thread(_send, cmd, args)
                if not response.ok:
                    error_msg = (
                        response.error.message if response.error else "Unknown error"
                    )
                    self.notify(f"Error: {error_msg}", severity="error")
            except Exception as e:
                self.notify(f"Command failed: {e}", severity="error")

        asyncio.create_task(do_send())

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
        current_idx = (
            modes.index(status_bar.repeat) if status_bar.repeat in modes else 0
        )
        new_mode = modes[(current_idx + 1) % len(modes)]
        self._send_command("repeat", {"mode": new_mode})
        status_bar.repeat = new_mode

    def action_open_file_picker(self) -> None:
        """Open file picker to add file."""
        picker = self.query_one(FilePicker)
        picker.add_class("visible")

    def file_selected(self, path: str) -> None:
        """Handle file selection from picker."""
        self._send_command("play", {"file": path})
        self.notify(f"Playing: {Path(path).name}")

    def action_remove_current(self) -> None:
        """Remove current track from queue."""

        async def do_remove():
            from ..cli.commands import send_command

            response = await asyncio.to_thread(send_command, "status")
            if response.ok and response.data:
                idx = response.data.get("queue_position", 0)
                self._send_command("remove", {"index": idx})

        asyncio.create_task(do_remove())

    def action_jump_track(self) -> None:
        """Jump to track (show notification - use CLI for now)."""
        self.notify(
            "Use 'atk jump <index>' from CLI to jump to track", severity="information"
        )


def main() -> None:
    """Main entry point for TUI."""
    app = ATKApp()
    app.run()


if __name__ == "__main__":
    main()
