"""CLI command implementations."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..config import get_runtime_dir
from ..protocol.messages import Response, Event, parse_message


def is_daemon_running() -> bool:
    """Check if daemon is running."""
    runtime = get_runtime_dir()
    return (runtime / "registry.cmd").exists()


def start_daemon() -> None:
    """Start the daemon in background."""
    import os

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
        if (runtime / "registry.cmd").exists():
            return
        time.sleep(0.1)

    raise RuntimeError("Failed to start daemon")


def ensure_daemon() -> None:
    """Ensure daemon is running, start if needed."""
    if not is_daemon_running():
        start_daemon()


def send_registry_command(cmd: str, args: dict | None = None) -> Response:
    """Send command to registry and get response."""
    ensure_daemon()

    from ..protocol.messages import Request

    runtime = get_runtime_dir()
    cmd_pipe = runtime / "registry.cmd"
    resp_pipe = runtime / "registry.resp"

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


def send_session_command(
    session: str,
    cmd: str,
    args: dict | None = None,
) -> Response:
    """Send command to session and get response."""
    ensure_daemon()

    from ..protocol.messages import Request

    runtime = get_runtime_dir()
    cmd_pipe = runtime / "sessions" / f"{session}.cmd"
    resp_pipe = runtime / "sessions" / f"{session}.resp"

    # Check if session exists
    if not cmd_pipe.exists():
        # Try to spawn default session
        if session == "default":
            spawn_resp = send_registry_command("spawn", {"name": "default"})
            if not spawn_resp.ok:
                raise RuntimeError(f"Failed to create default session: {spawn_resp.error}")
            time.sleep(0.2)  # Wait for pipes to be created
        else:
            raise RuntimeError(f"Session '{session}' not found")

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
            raise TimeoutError("No response from session")

        while True:
            line = f.readline().strip()
            if not line:
                continue
            msg = parse_message(line)
            if isinstance(msg, Response) and msg.id == request.id:
                return msg


def subscribe_to_session(session: str):
    """Generator that yields events from session."""
    ensure_daemon()

    from ..protocol.messages import Request

    runtime = get_runtime_dir()
    cmd_pipe = runtime / "sessions" / f"{session}.cmd"
    resp_pipe = runtime / "sessions" / f"{session}.resp"

    if not cmd_pipe.exists():
        raise RuntimeError(f"Session '{session}' not found")

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


# Helper to get session name with default fallback
def get_session(session: str | None) -> str:
    """Get session name, defaulting to 'default'."""
    return session or "default"


# Command implementations

def cmd_list() -> Response:
    """List all sessions."""
    return send_registry_command("list")


def cmd_new(name: str | None = None) -> Response:
    """Create new session."""
    return send_registry_command("spawn", {"name": name} if name else {})


def cmd_kill(name: str) -> Response:
    """Kill a session."""
    return send_registry_command("kill", {"name": name})


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


def cmd_play(file: str | None, session: str | None) -> Response:
    """Play file or resume."""
    args = {"file": file} if file else {}
    return send_session_command(get_session(session), "play", args)


def cmd_pause(session: str | None) -> Response:
    """Pause playback."""
    return send_session_command(get_session(session), "pause")


def cmd_stop(session: str | None) -> Response:
    """Stop playback."""
    return send_session_command(get_session(session), "stop")


def cmd_next(session: str | None) -> Response:
    """Next track."""
    return send_session_command(get_session(session), "next")


def cmd_prev(session: str | None) -> Response:
    """Previous track."""
    return send_session_command(get_session(session), "prev")


def cmd_seek(position: str, session: str | None) -> Response:
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

    return send_session_command(get_session(session), "seek", {"pos": pos})


def cmd_volume(level: int, session: str | None) -> Response:
    """Set volume."""
    return send_session_command(get_session(session), "volume", {"level": level})


def cmd_add(uri: str, session: str | None) -> Response:
    """Add to queue."""
    # Expand path if local file
    path = Path(uri).expanduser()
    if path.exists():
        uri = str(path.resolve())
    return send_session_command(get_session(session), "add", {"uri": uri})


def cmd_remove(index: int, session: str | None) -> Response:
    """Remove from queue."""
    return send_session_command(get_session(session), "remove", {"index": index})


def cmd_move(from_idx: int, to_idx: int, session: str | None) -> Response:
    """Move in queue."""
    return send_session_command(
        get_session(session), "move", {"from": from_idx, "to": to_idx}
    )


def cmd_clear(session: str | None) -> Response:
    """Clear queue."""
    return send_session_command(get_session(session), "clear")


def cmd_queue(session: str | None) -> Response:
    """Get queue."""
    return send_session_command(get_session(session), "queue")


def cmd_status(session: str | None) -> Response:
    """Get status."""
    return send_session_command(get_session(session), "status")


def cmd_info(index: int | None, session: str | None) -> Response:
    """Get track info."""
    args = {"index": index} if index is not None else {}
    return send_session_command(get_session(session), "info", args)


def cmd_shuffle(enabled: bool | None, session: str | None) -> Response:
    """Toggle shuffle."""
    if enabled is None:
        # Toggle current state
        status = send_session_command(get_session(session), "status")
        if status.ok and status.data:
            enabled = not status.data.get("shuffle", False)
        else:
            enabled = True

    return send_session_command(get_session(session), "shuffle", {"enabled": enabled})


def cmd_repeat(mode: str | None, session: str | None) -> Response:
    """Set repeat mode."""
    if mode is None:
        # Cycle: none -> queue -> track -> none
        status = send_session_command(get_session(session), "status")
        if status.ok and status.data:
            current = status.data.get("repeat", "none")
            mode = {"none": "queue", "queue": "track", "track": "none"}.get(
                current, "none"
            )
        else:
            mode = "queue"

    return send_session_command(get_session(session), "repeat", {"mode": mode})


def cmd_save(name: str, session: str | None) -> Response:
    """Save session state."""
    return send_session_command(get_session(session), "save", {"name": name})


def cmd_load(name: str, session: str | None) -> Response:
    """Load session state."""
    return send_session_command(get_session(session), "load", {"name": name})


def cmd_rate(speed: float, session: str | None) -> Response:
    """Set playback rate (0.25 to 4.0)."""
    return send_session_command(get_session(session), "rate", {"speed": speed})


def cmd_pitch(semitones: float, session: str | None) -> Response:
    """Set pitch shift in semitones (-12 to +12)."""
    return send_session_command(get_session(session), "pitch", {"semitones": semitones})


def cmd_bass(db: float, session: str | None) -> Response:
    """Set bass EQ adjustment in dB (-12 to +12)."""
    return send_session_command(get_session(session), "bass", {"db": db})


def cmd_treble(db: float, session: str | None) -> Response:
    """Set treble EQ adjustment in dB (-12 to +12)."""
    return send_session_command(get_session(session), "treble", {"db": db})


def cmd_fade(to: int, duration: float, session: str | None) -> Response:
    """Fade volume to target over duration."""
    return send_session_command(
        get_session(session), "fade", {"to": to, "duration": duration}
    )


def cmd_loop(
    a: float | None,
    b: float | None,
    enabled: bool | None,
    session: str | None,
) -> Response:
    """Set A/B loop points or enable/disable loop."""
    args: dict = {}
    if a is not None:
        args["a"] = a
    if b is not None:
        args["b"] = b
    if enabled is not None:
        args["enabled"] = enabled
    return send_session_command(get_session(session), "loop", args)
