"""CLI command implementations."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from ..config import get_runtime_dir
from ..protocol.messages import Event, Response, parse_message


def is_daemon_running() -> bool:
    """Check if daemon is running."""
    runtime = get_runtime_dir()
    return (runtime / "atk.cmd").exists()


def start_daemon() -> None:
    """Start the daemon in background."""
    # Use subprocess to start daemon
    subprocess.Popen(
        [sys.executable, "-m", "atk.daemon.main"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for daemon to start
    runtime = get_runtime_dir()
    for _ in range(50):  # 5 seconds max
        if (runtime / "atk.cmd").exists():
            return
        time.sleep(0.1)

    raise RuntimeError("Failed to start daemon")


def ensure_daemon() -> None:
    """Ensure daemon is running, start if needed."""
    if not is_daemon_running():
        start_daemon()


def send_command(cmd: str, args: dict | None = None) -> Response:
    """Send command to daemon and get response."""
    ensure_daemon()

    from ..protocol.messages import Request

    runtime = get_runtime_dir()
    cmd_pipe = runtime / "atk.cmd"
    resp_pipe = runtime / "atk.resp"

    request = Request(cmd=cmd, args=args or {})

    # Write request
    with open(cmd_pipe, "w") as f:
        f.write(request.serialize() + "\n")
        f.flush()

    # Read response
    import select

    with open(resp_pipe, "r") as f:
        ready, _, _ = select.select([f], [], [], 5.0)
        if not ready:
            raise TimeoutError("No response from daemon")

        while True:
            line = f.readline().strip()
            if not line:
                continue
            msg = parse_message(line)
            if isinstance(msg, Response) and msg.id == request.id:
                return msg


def subscribe_to_events():
    """Generator that yields events from daemon."""
    ensure_daemon()

    from ..protocol.messages import Request

    runtime = get_runtime_dir()
    cmd_pipe = runtime / "atk.cmd"
    resp_pipe = runtime / "atk.resp"

    request = Request(cmd="subscribe")

    # Write subscribe request
    with open(cmd_pipe, "w") as f:
        f.write(request.serialize() + "\n")
        f.flush()

    # Continuously read events
    import select

    with open(resp_pipe, "r") as f:
        while True:
            ready, _, _ = select.select([f], [], [], 1.0)
            if ready:
                line = f.readline().strip()
                if line:
                    msg = parse_message(line)
                    if isinstance(msg, Event):
                        yield msg
                    elif isinstance(msg, Response) and msg.id == request.id:
                        # Initial response, continue
                        if not msg.ok:
                            raise RuntimeError(f"Subscribe failed: {msg.error}")


# Command implementations


def cmd_daemon_stop() -> None:
    """Stop the daemon."""
    import os
    import signal

    runtime = get_runtime_dir()
    pid_file = runtime / "daemon.pid"

    if not pid_file.exists():
        raise RuntimeError("Daemon is not running")

    pid = int(pid_file.read_text().strip())
    os.kill(pid, signal.SIGTERM)


def cmd_play(file: str | None) -> Response:
    """Play file or resume."""
    args = {"file": file} if file else {}
    return send_command("play", args)


def cmd_pause() -> Response:
    """Pause playback."""
    return send_command("pause")


def cmd_stop() -> Response:
    """Stop playback."""
    return send_command("stop")


def cmd_next() -> Response:
    """Next track."""
    return send_command("next")


def cmd_prev() -> Response:
    """Previous track."""
    return send_command("prev")


def cmd_seek(position: str) -> Response:
    """Seek to position."""
    # Parse position (supports +5, -10, 1:30, 90)
    pos: float | str

    if position.startswith("+") or position.startswith("-"):
        pos = position  # Relative seek handled by session
    elif ":" in position:
        # Parse MM:SS or HH:MM:SS
        parts = position.split(":")
        if len(parts) == 2:
            pos = int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            pos = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        else:
            raise ValueError(f"Invalid time format: {position}")
    else:
        pos = float(position)

    return send_command("seek", {"pos": pos})


def cmd_volume(level: int) -> Response:
    """Set volume."""
    return send_command("volume", {"level": level})


def cmd_add(uri: str) -> Response:
    """Add to queue."""
    # Expand path if local file
    path = Path(uri).expanduser()
    if path.exists():
        uri = str(path.resolve())
    return send_command("add", {"uri": uri})


def cmd_remove(index: int) -> Response:
    """Remove from queue."""
    return send_command("remove", {"index": index})


def cmd_move(from_idx: int, to_idx: int) -> Response:
    """Move in queue."""
    return send_command("move", {"from": from_idx, "to": to_idx})


def cmd_clear() -> Response:
    """Clear queue."""
    return send_command("clear")


def cmd_queue() -> Response:
    """Get queue."""
    return send_command("queue")


def cmd_jump(index: int) -> Response:
    """Jump to track at index."""
    return send_command("jump", {"index": index})


def cmd_status() -> Response:
    """Get status."""
    return send_command("status")


def cmd_info(index: int | None) -> Response:
    """Get track info."""
    args = {"index": index} if index is not None else {}
    return send_command("info", args)


def cmd_shuffle(enabled: bool | None) -> Response:
    """Toggle shuffle."""
    if enabled is None:
        # Toggle current state
        status = send_command("status")
        if status.ok and status.data:
            enabled = not status.data.get("shuffle", False)
        else:
            enabled = True

    return send_command("shuffle", {"enabled": enabled})


def cmd_repeat(mode: str | None) -> Response:
    """Set repeat mode."""
    if mode is None:
        # Cycle: none -> queue -> track -> none
        status = send_command("status")
        if status.ok and status.data:
            current = status.data.get("repeat", "none")
            mode = {"none": "queue", "queue": "track", "track": "none"}.get(
                current, "none"
            )
        else:
            mode = "queue"

    return send_command("repeat", {"mode": mode})


def cmd_rate(speed: float) -> Response:
    """Set playback rate (0.25 to 4.0)."""
    return send_command("rate", {"speed": speed})


def cmd_save(name: str, fmt: str = "json") -> Response:
    """Save queue as playlist."""
    return send_command("save", {"name": name, "format": fmt})


def cmd_load(name: str) -> Response:
    """Load playlist."""
    return send_command("load", {"name": name})


def cmd_playlists() -> Response:
    """List saved playlists."""
    return send_command("playlists")


def cmd_ping() -> Response:
    """Ping daemon."""
    return send_command("ping")


def cmd_shutdown() -> Response:
    """Shutdown daemon gracefully."""
    return send_command("shutdown")


def cmd_devices() -> Response:
    """List available audio devices."""
    return send_command("devices")


def cmd_set_device(device_id: str | None) -> Response:
    """Set playback device by ID."""
    return send_command("set-device", {"device_id": device_id})
