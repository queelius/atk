"""Custom widgets for ATK TUI."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import ProgressBar, Static


def format_time(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    if seconds < 0:
        return "0:00"

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


class NowPlaying(Static):
    """Widget displaying current track information."""

    DEFAULT_CSS = """
    NowPlaying {
        height: auto;
        padding: 1;
    }

    NowPlaying .title {
        text-style: bold;
    }

    NowPlaying .artist {
        color: $text-muted;
    }

    NowPlaying .album {
        color: $text-muted;
        text-style: italic;
    }
    """

    title: reactive[str] = reactive("No track loaded")
    artist: reactive[str] = reactive("")
    album: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield Static("Now Playing:", classes="label")
        yield Static(self.title, classes="title", id="track-title")
        yield Static(self.artist, classes="artist", id="track-artist")
        yield Static(self.album, classes="album", id="track-album")

    def watch_title(self, value: str) -> None:
        title_widget = self.query_one("#track-title", Static)
        title_widget.update(value)

    def watch_artist(self, value: str) -> None:
        artist_widget = self.query_one("#track-artist", Static)
        artist_widget.update(value)

    def watch_album(self, value: str) -> None:
        album_widget = self.query_one("#track-album", Static)
        album_widget.update(value)


class ProgressDisplay(Static):
    """Widget displaying playback progress."""

    DEFAULT_CSS = """
    ProgressDisplay {
        height: 3;
        padding: 0 1;
    }

    ProgressDisplay Horizontal {
        height: 1;
    }

    ProgressDisplay ProgressBar {
        width: 1fr;
        padding-right: 1;
    }

    ProgressDisplay .time {
        width: auto;
        min-width: 15;
        text-align: right;
    }
    """

    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ProgressBar(total=100, show_eta=False, id="progress-bar")
            yield Static("0:00 / 0:00", classes="time", id="time-display")

    def watch_position(self, value: float) -> None:
        self._update_display()

    def watch_duration(self, value: float) -> None:
        self._update_display()

    def _update_display(self) -> None:
        progress: float = 0
        if self.duration > 0:
            progress = min(100.0, (self.position / self.duration) * 100)

        bar = self.query_one("#progress-bar", ProgressBar)
        bar.progress = progress

        time_str = f"{format_time(self.position)} / {format_time(self.duration)}"
        time_display = self.query_one("#time-display", Static)
        time_display.update(time_str)


class QueuePreview(Static):
    """Widget displaying upcoming tracks in queue."""

    DEFAULT_CSS = """
    QueuePreview {
        height: auto;
        max-height: 8;
        padding: 1;
        border-top: solid $primary;
    }

    QueuePreview .header {
        text-style: bold;
        margin-bottom: 1;
    }

    QueuePreview .queue-item {
        color: $text-muted;
    }

    QueuePreview .queue-item.current {
        color: $text;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Up Next:", classes="header")
        yield Vertical(id="queue-list")

    def update_queue(self, tracks: list[dict], current_index: int) -> None:
        """Update the queue display."""
        queue_list = self.query_one("#queue-list", Vertical)
        queue_list.remove_children()

        # Show up to 5 upcoming tracks
        start = current_index
        end = min(start + 5, len(tracks))

        for i in range(start, end):
            track = tracks[i]
            prefix = "▶ " if i == current_index else "  "

            # Format track name
            artist = track.get("artist", "")
            title = track.get("title") or track.get("uri", "Unknown").split("/")[-1]
            text = f"{artist} - {title}" if artist else title

            classes = "queue-item current" if i == current_index else "queue-item"
            queue_list.mount(Static(f"{prefix}{i + 1}. {text}", classes=classes))

        if len(tracks) > end:
            remaining = len(tracks) - end
            queue_list.mount(
                Static(f"  ... and {remaining} more", classes="queue-item")
            )


class StatusBar(Static):
    """Widget displaying status indicators."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        dock: top;
        background: $primary;
        color: $text;
        padding: 0 1;
    }

    StatusBar Horizontal {
        height: 1;
    }

    StatusBar .session-name {
        text-style: bold;
    }

    StatusBar .spacer {
        width: 1fr;
    }

    StatusBar .indicators {
        width: auto;
    }
    """

    session_name: reactive[str] = reactive("default")
    state: reactive[str] = reactive("stopped")
    shuffle: reactive[bool] = reactive(False)
    repeat: reactive[str] = reactive("none")
    volume: reactive[int] = reactive(80)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("ATK", classes="logo")
            yield Static(" ", classes="spacer")
            yield Static(id="state-icon")
            yield Static(" ", classes="spacer")
            yield Static(id="session-display", classes="session-name")
            yield Static(classes="spacer")
            yield Static(id="indicators", classes="indicators")

    def watch_state(self, value: str) -> None:
        icon = {"playing": "▶", "paused": "⏸", "stopped": "⏹"}.get(value, "?")
        self.query_one("#state-icon", Static).update(icon)

    def watch_session_name(self, value: str) -> None:
        self.query_one("#session-display", Static).update(value)

    def watch_shuffle(self, _: bool) -> None:
        self._update_indicators()

    def watch_repeat(self, _: str) -> None:
        self._update_indicators()

    def watch_volume(self, _: int) -> None:
        self._update_indicators()

    def _update_indicators(self) -> None:
        parts = []

        if self.shuffle:
            parts.append("🔀")

        repeat_icons = {"queue": "🔁", "track": "🔂"}
        if self.repeat in repeat_icons:
            parts.append(repeat_icons[self.repeat])

        parts.append(f"{self.volume}%")

        self.query_one("#indicators", Static).update(" ".join(parts))


class HelpBar(Static):
    """Widget displaying keyboard shortcuts."""

    DEFAULT_CSS = """
    HelpBar {
        height: 1;
        dock: bottom;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "[Space] Play/Pause  [←/→] Seek  [↑/↓] Vol"
            "  [n/p] Track  [s] Shuffle  [r] Repeat  [q] Quit"
        )
