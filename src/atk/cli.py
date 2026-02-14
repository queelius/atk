"""ATK CLI — pipe client, Click commands, output formatters."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import click

from .config import get_runtime_dir

# ---------------------------------------------------------------------------
# Pipe client
# ---------------------------------------------------------------------------


def is_daemon_running() -> bool:
    return (get_runtime_dir() / "atk.cmd").exists()


def start_daemon() -> None:
    subprocess.Popen(
        [sys.executable, "-m", "atk.daemon"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    runtime = get_runtime_dir()
    for _ in range(50):
        if (runtime / "atk.cmd").exists():
            return
        time.sleep(0.1)
    raise RuntimeError("Failed to start daemon")


def ensure_daemon() -> None:
    if not is_daemon_running():
        start_daemon()


def send_command(cmd: str, args: dict | None = None) -> dict:
    """Send JSON command to daemon pipe, return response dict."""
    ensure_daemon()
    runtime = get_runtime_dir()
    req_id = str(uuid.uuid4())
    request = json.dumps({"id": req_id, "cmd": cmd, "args": args or {}})

    with open(runtime / "atk.cmd", "w") as f:
        f.write(request + "\n")
        f.flush()

    with open(runtime / "atk.resp", "r") as f:
        ready, _, _ = select.select([f], [], [], 5.0)
        if not ready:
            raise TimeoutError("No response from daemon")
        while True:
            line = f.readline().strip()
            if not line:
                continue
            resp = json.loads(line)
            if resp.get("id") == req_id:
                return resp


def subscribe_to_events():
    """Generator yielding event dicts from daemon."""
    ensure_daemon()
    runtime = get_runtime_dir()
    req_id = str(uuid.uuid4())
    request = json.dumps({"id": req_id, "cmd": "subscribe", "args": {}})

    with open(runtime / "atk.cmd", "w") as f:
        f.write(request + "\n")
        f.flush()

    with open(runtime / "atk.resp", "r") as f:
        while True:
            ready, _, _ = select.select([f], [], [], 1.0)
            if ready:
                line = f.readline().strip()
                if not line:
                    continue
                msg = json.loads(line)
                if "event" in msg:
                    yield msg
                elif msg.get("id") == req_id and not msg.get("ok"):
                    raise RuntimeError(f"Subscribe failed: {msg.get('error')}")


# ---------------------------------------------------------------------------
# Output formatters
# ---------------------------------------------------------------------------


def fmt_time(seconds: float) -> str:
    if seconds < 0:
        return "0:00"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_track(track: dict | None, duration: bool = True) -> str:
    if not track:
        return "(no track)"
    parts = []
    if track.get("artist"):
        parts.append(track["artist"])
    if track.get("title"):
        parts.append(track["title"])
    elif track.get("uri"):
        parts.append(track["uri"].split("/")[-1])
    text = " - ".join(parts) if parts else track.get("uri", "(unknown)")
    if duration and track.get("duration"):
        text += f" [{fmt_time(track['duration'])}]"
    return text


def fmt_status(data: dict) -> str:
    lines = []
    state = data.get("state", "stopped")
    icon = {"playing": "▶", "paused": "⏸", "stopped": "⏹"}.get(state, "?")
    lines.append(f"{icon} {fmt_track(data.get('track'), duration=False)}")

    if data.get("track"):
        pos, dur = data.get("position", 0), data.get("duration", 0)
        if dur > 0:
            filled = int(40 * pos / dur)
            bar = "▓" * filled + "░" * (40 - filled)
            lines.append(f"  {bar} {fmt_time(pos)} / {fmt_time(dur)}")

    vol = data.get("volume", 0)
    shuf = "🔀" if data.get("shuffle") else "  "
    rep = {"none": "  ", "queue": "🔁", "track": "🔂"}.get(
        data.get("repeat", "none"), "  "
    )
    lines.append(f"  Volume: {vol}%  {shuf} {rep}")

    rate = data.get("rate", 1.0)
    if rate != 1.0:
        lines.append(f"  Rate: {rate:.2f}x")

    qlen = data.get("queue_length", 0)
    if qlen:
        lines.append(f"  Queue: {data.get('queue_position', 0) + 1}/{qlen}")
    return "\n".join(lines)


def fmt_queue(data: dict) -> str:
    tracks = data.get("tracks", [])
    if not tracks:
        return "(empty queue)"
    cur = data.get("current_index", 0)
    lines = []
    for i, t in enumerate(tracks):
        prefix = "▶ " if i == cur else "  "
        lines.append(f"{prefix}{i + 1}. {fmt_track(t)}")
    return "\n".join(lines)


def fmt_playlists(data: dict) -> str:
    pls = data.get("playlists", [])
    if not pls:
        return "(no saved playlists)"
    return "\n".join(
        f"  {p['name']} ({p.get('track_count', 0)} tracks, {p.get('format', 'json')})"
        for p in pls
    )


def fmt_devices(data: dict) -> str:
    devs = data.get("devices", [])
    if not devs:
        return "(no audio devices found)"
    lines = []
    for d in devs:
        dev_id = d.get("id", "")
        if isinstance(dev_id, bytes):
            dev_id = dev_id.hex()
        default = " (default)" if d.get("is_default") else ""
        lines.append(f"  {d.get('name', 'Unknown')}{default}")
        lines.append(f"    ID: {dev_id}")
    return "\n".join(lines)


def fmt_event(evt: dict) -> str:
    etype = evt.get("event", "")
    data = evt.get("data", {})
    if etype == "track_changed":
        return f"[track_changed] {fmt_track(data.get('track'))}"
    if etype == "position_update":
        pos = fmt_time(data.get("position", 0))
        dur = fmt_time(data.get("duration", 0))
        return f"[position] {pos} / {dur}"
    if etype == "playback_paused":
        return f"[paused] at {fmt_time(data.get('position', 0))}"
    if etype == "error":
        return f"[error] {data.get('message', '')}"
    return f"[{etype}]"


def print_response(resp: dict, json_output: bool = False, formatter=None) -> None:
    if json_output:
        print(json.dumps(resp, indent=2))
        return
    if not resp.get("ok"):
        err = resp.get("error", {})
        print(f"Error: {err.get('message', 'Unknown error')}", file=sys.stderr)
        sys.exit(1)
    data = resp.get("data", {})
    if formatter:
        print(formatter(data))
    elif data:
        for k, v in data.items():
            print(f"{k}: {v}")
    else:
        print("OK")


# ---------------------------------------------------------------------------
# Seek parser
# ---------------------------------------------------------------------------


def parse_seek(position: str) -> float | str:
    """Parse seek position: 30, +5, -10, 1:30, 1:02:30."""
    if position.startswith("+") or position.startswith("-"):
        return position  # Relative — daemon handles it
    if ":" in position:
        parts = position.split(":")
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        raise click.BadParameter(f"Invalid time: {position}")
    return float(position)


# ---------------------------------------------------------------------------
# Click CLI
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--json", "json_output", is_flag=True, help="Output raw JSON")
@click.option("--tui", is_flag=True, help="Launch TUI")
@click.pass_context
def cli(ctx, json_output: bool, tui: bool):
    """ATK - Audio Toolkit. Non-blocking audio daemon with pipe IPC."""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    if tui:
        from .tui.app import main as tui_main

        tui_main()
        sys.exit(0)
    if ctx.invoked_subcommand is None:
        ctx.invoke(status)


# ── Playback ───────────────────────────────────────────────────────────────


@cli.command()
@click.argument("file", required=False, type=click.Path())
@click.pass_context
def play(ctx, file):
    """Play a file or resume playback."""
    args = {}
    if file:
        p = Path(file).expanduser()
        args["file"] = str(p.resolve()) if p.exists() else file
    print_response(send_command("play", args), ctx.obj["json"])


@cli.command()
@click.pass_context
def pause(ctx):
    """Pause playback."""
    print_response(send_command("pause"), ctx.obj["json"])


@cli.command()
@click.pass_context
def stop(ctx):
    """Stop playback."""
    print_response(send_command("stop"), ctx.obj["json"])


@cli.command("next")
@click.pass_context
def next_track(ctx):
    """Skip to next track."""
    print_response(send_command("next"), ctx.obj["json"])


@cli.command("prev")
@click.pass_context
def prev_track(ctx):
    """Go to previous track."""
    print_response(send_command("prev"), ctx.obj["json"])


@cli.command()
@click.argument("position")
@click.pass_context
def seek(ctx, position):
    """Seek to position (30, +5, -10, 1:30)."""
    print_response(send_command("seek", {"pos": parse_seek(position)}), ctx.obj["json"])


# ── Queue ──────────────────────────────────────────────────────────────────


@cli.command()
@click.argument("uri")
@click.pass_context
def add(ctx, uri):
    """Add file to queue."""
    p = Path(uri).expanduser()
    print_response(
        send_command("add", {"uri": str(p.resolve()) if p.exists() else uri}),
        ctx.obj["json"],
    )


@cli.command()
@click.argument("index", type=int)
@click.pass_context
def remove(ctx, index):
    """Remove track by index (0-based)."""
    print_response(send_command("remove", {"index": index}), ctx.obj["json"])


@cli.command()
@click.argument("from_idx", type=int)
@click.argument("to_idx", type=int)
@click.pass_context
def move(ctx, from_idx, to_idx):
    """Move track in queue."""
    print_response(
        send_command("move", {"from": from_idx, "to": to_idx}), ctx.obj["json"]
    )


@cli.command()
@click.pass_context
def clear(ctx):
    """Clear the queue."""
    print_response(send_command("clear"), ctx.obj["json"])


@cli.command()
@click.pass_context
def queue(ctx):
    """Show queue contents."""
    print_response(send_command("queue"), ctx.obj["json"], fmt_queue)


@cli.command()
@click.argument("index", type=int)
@click.pass_context
def jump(ctx, index):
    """Jump to track by index (0-based)."""
    print_response(send_command("jump", {"index": index}), ctx.obj["json"])


# ── State ──────────────────────────────────────────────────────────────────


@cli.command()
@click.pass_context
def status(ctx):
    """Show current playback status."""
    print_response(send_command("status"), ctx.obj["json"], fmt_status)


@cli.command()
@click.argument("index", type=int, required=False)
@click.pass_context
def info(ctx, index):
    """Show track metadata."""
    print_response(
        send_command("info", {"index": index} if index is not None else {}),
        ctx.obj["json"],
    )


@cli.command()
@click.argument("level", type=int)
@click.pass_context
def volume(ctx, level):
    """Set volume (0-100)."""
    print_response(send_command("volume", {"level": level}), ctx.obj["json"])


@cli.command()
@click.argument("state", type=click.Choice(["on", "off"]), required=False)
@click.pass_context
def shuffle(ctx, state):
    """Toggle or set shuffle mode."""
    if state is None:
        resp = send_command("status")
        enabled = (
            not resp.get("data", {}).get("shuffle", False) if resp.get("ok") else True
        )
    else:
        enabled = state == "on"
    print_response(send_command("shuffle", {"enabled": enabled}), ctx.obj["json"])


@cli.command()
@click.argument("mode", type=click.Choice(["none", "queue", "track"]), required=False)
@click.pass_context
def repeat(ctx, mode):
    """Set repeat mode."""
    if mode is None:
        resp = send_command("status")
        cur = resp.get("data", {}).get("repeat", "none") if resp.get("ok") else "none"
        mode = {"none": "queue", "queue": "track", "track": "none"}.get(cur, "none")
    print_response(send_command("repeat", {"mode": mode}), ctx.obj["json"])


@cli.command()
@click.argument("speed", type=float)
@click.option("--tape", is_flag=True, help="Tape-style (pitch changes with speed)")
@click.pass_context
def rate(ctx, speed, tape):
    """Set playback rate (0.25-4.0). Default preserves pitch."""
    args: dict = {"speed": speed}
    if tape:
        args["mode"] = "tape"
    print_response(send_command("rate", args), ctx.obj["json"])


# ── Playlists ──────────────────────────────────────────────────────────────


@cli.command()
@click.argument("name")
@click.option(
    "-f", "--format", "fmt", type=click.Choice(["json", "m3u", "txt"]), default="json"
)
@click.pass_context
def save(ctx, name, fmt):
    """Save queue as playlist."""
    print_response(send_command("save", {"name": name, "format": fmt}), ctx.obj["json"])


@cli.command()
@click.argument("name")
@click.pass_context
def load(ctx, name):
    """Load playlist."""
    print_response(send_command("load", {"name": name}), ctx.obj["json"])


@cli.command()
@click.pass_context
def playlists(ctx):
    """List saved playlists."""
    print_response(send_command("playlists"), ctx.obj["json"], fmt_playlists)


# ── Daemon ─────────────────────────────────────────────────────────────────


@cli.command()
@click.pass_context
def ping(ctx):
    """Ping the daemon."""
    print_response(send_command("ping"), ctx.obj["json"])


@cli.command()
@click.pass_context
def shutdown(ctx):
    """Gracefully shutdown the daemon."""
    print_response(send_command("shutdown"), ctx.obj["json"])


@cli.command()
@click.pass_context
def devices(ctx):
    """List audio playback devices."""
    print_response(send_command("devices"), ctx.obj["json"], fmt_devices)


@cli.command("set-device")
@click.argument("device_id", required=False)
@click.pass_context
def set_device(ctx, device_id):
    """Set audio device (omit to reset to default)."""
    print_response(
        send_command("set-device", {"device_id": device_id}), ctx.obj["json"]
    )


@cli.command("daemon-stop")
def daemon_stop():
    """Stop the daemon (SIGTERM)."""
    runtime = get_runtime_dir()
    pid_file = runtime / "daemon.pid"
    if not pid_file.exists():
        print("Daemon is not running", file=sys.stderr)
        sys.exit(1)
    os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)
    print("Daemon stopped")


@cli.command()
@click.pass_context
def subscribe(ctx):
    """Stream events from daemon."""
    json_output = ctx.obj["json"]
    try:
        for evt in subscribe_to_events():
            if json_output:
                print(json.dumps(evt))
            else:
                print(fmt_event(evt))
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    cli()
