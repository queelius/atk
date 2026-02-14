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
        from ..cli import ensure_daemon

        try:
            ensure_daemon()
        except Exception as e:
            _logger.error("Failed to start daemon: %s", e)
            self.notify(f"Failed to start daemon: {e}", severity="error")
            return

        await self._fetch_status()
        self._event_task = asyncio.create_task(self._listen_events())
        self._status_task = asyncio.create_task(self._periodic_status())

    async def on_unmount(self) -> None:
        for task in (self._event_task, self._status_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _fetch_status(self) -> None:
        try:
            from ..cli import send_command

            resp = await asyncio.to_thread(send_command, "status")
            if resp.get("ok") and resp.get("data"):
                self._update_from_status(resp["data"])
            qresp = await asyncio.to_thread(send_command, "queue")
            if qresp.get("ok") and qresp.get("data"):
                self._update_queue(qresp["data"])
        except Exception as e:
            _logger.warning("Failed to fetch status: %s", e)

    def _update_from_status(self, data: dict) -> None:
        try:
            sb = self.query_one("#status-bar", StatusBar)
            sb.state = data.get("state", "stopped")
            sb.volume = data.get("volume", 80)
            sb.shuffle = data.get("shuffle", False)
            sb.repeat = data.get("repeat", "none")

            np_ = self.query_one("#now-playing", NowPlaying)
            track = data.get("track")
            if track:
                np_.title = (
                    track.get("title") or track.get("uri", "Unknown").split("/")[-1]
                )
                np_.artist = track.get("artist", "")
                np_.album = track.get("album", "")
            else:
                np_.title = "No track loaded"
                np_.artist = ""
                np_.album = ""

            prog = self.query_one("#progress", ProgressDisplay)
            prog.position = data.get("position", 0.0)
            prog.duration = data.get("duration", 0.0)
        except Exception as e:
            _logger.warning("Error updating status: %s", e)

    def _update_queue(self, data: dict) -> None:
        try:
            qp = self.query_one("#queue-preview", QueuePreview)
            qp.update_queue(data.get("tracks", []), data.get("current_index", 0))
        except Exception as e:
            _logger.warning("Error updating queue: %s", e)

    async def _listen_events(self) -> None:
        from ..cli import subscribe_to_events

        while self._retry_count < self._max_retries:
            try:
                for evt in subscribe_to_events():
                    self._handle_event(evt)
                    self._retry_count = 0
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
                await asyncio.sleep(min(2**self._retry_count, 10))

    def _handle_event(self, evt: dict) -> None:
        try:
            etype = evt.get("event", "")
            data = evt.get("data", {})

            if etype == "track_changed":
                track = data.get("track", {})
                np_ = self.query_one("#now-playing", NowPlaying)
                np_.title = (
                    track.get("title") or track.get("uri", "Unknown").split("/")[-1]
                )
                np_.artist = track.get("artist", "")
                np_.album = track.get("album", "")

            elif etype == "position_update":
                prog = self.query_one("#progress", ProgressDisplay)
                prog.position = data.get("position", 0.0)
                prog.duration = data.get("duration", 0.0)

            elif etype == "playback_started":
                self.query_one("#status-bar", StatusBar).state = "playing"

            elif etype == "playback_paused":
                self.query_one("#status-bar", StatusBar).state = "paused"

            elif etype == "playback_stopped":
                self.query_one("#status-bar", StatusBar).state = "stopped"

            elif etype == "queue_updated":
                self._update_queue(data.get("queue", {}))

            elif etype == "error":
                self.notify(
                    f"Error: {data.get('message', 'Unknown')}", severity="error"
                )
        except Exception as e:
            _logger.warning("Error handling event: %s", e)

    async def _periodic_status(self) -> None:
        while True:
            await asyncio.sleep(5)
            try:
                await self._fetch_status()
            except asyncio.CancelledError:
                break
            except Exception as e:
                _logger.warning("Periodic status error: %s", e)

    def _send_command(self, cmd: str, args: dict | None = None) -> None:
        async def do_send():
            try:
                from ..cli import send_command

                resp = await asyncio.to_thread(send_command, cmd, args)
                if not resp.get("ok"):
                    err = resp.get("error", {}).get("message", "Unknown error")
                    self.notify(f"Error: {err}", severity="error")
            except Exception as e:
                self.notify(f"Command failed: {e}", severity="error")

        asyncio.create_task(do_send())

    # Action handlers

    def action_toggle_playback(self) -> None:
        sb = self.query_one("#status-bar", StatusBar)
        self._send_command("pause" if sb.state == "playing" else "play")

    def action_seek_back(self) -> None:
        self._send_command("seek", {"pos": "-5"})

    def action_seek_forward(self) -> None:
        self._send_command("seek", {"pos": "+5"})

    def action_volume_up(self) -> None:
        sb = self.query_one("#status-bar", StatusBar)
        new = min(100, sb.volume + 5)
        self._send_command("volume", {"level": new})
        sb.volume = new

    def action_volume_down(self) -> None:
        sb = self.query_one("#status-bar", StatusBar)
        new = max(0, sb.volume - 5)
        self._send_command("volume", {"level": new})
        sb.volume = new

    def action_next_track(self) -> None:
        self._send_command("next")

    def action_prev_track(self) -> None:
        self._send_command("prev")

    def action_toggle_shuffle(self) -> None:
        sb = self.query_one("#status-bar", StatusBar)
        new = not sb.shuffle
        self._send_command("shuffle", {"enabled": new})
        sb.shuffle = new

    def action_cycle_repeat(self) -> None:
        sb = self.query_one("#status-bar", StatusBar)
        modes = ["none", "queue", "track"]
        idx = modes.index(sb.repeat) if sb.repeat in modes else 0
        new = modes[(idx + 1) % len(modes)]
        self._send_command("repeat", {"mode": new})
        sb.repeat = new

    def action_open_file_picker(self) -> None:
        self.query_one(FilePicker).add_class("visible")

    def file_selected(self, path: str) -> None:
        self._send_command("play", {"file": path})
        self.notify(f"Playing: {Path(path).name}")

    def action_remove_current(self) -> None:
        async def do_remove():
            from ..cli import send_command

            resp = await asyncio.to_thread(send_command, "status")
            if resp.get("ok") and resp.get("data"):
                idx = resp["data"].get("queue_position", 0)
                self._send_command("remove", {"index": idx})

        asyncio.create_task(do_remove())

    def action_jump_track(self) -> None:
        self.notify("Use 'atk jump <index>' from CLI", severity="information")


def main() -> None:
    ATKApp().run()
