"""ATK CLI main entry point."""

from __future__ import annotations

import sys

import click

from . import commands
from .output import (
    format_devices,
    format_playlists,
    format_queue,
    format_status,
    print_event,
    print_response,
)


@click.group(invoke_without_command=True)
@click.option("--json", "json_output", is_flag=True, help="Output raw JSON")
@click.option("--tui", is_flag=True, help="Launch TUI")
@click.pass_context
def cli(ctx, json_output: bool, tui: bool):
    """ATK - Audio Toolkit

    Non-blocking audio playback daemon with named pipe protocol.
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output

    if tui:
        # Launch TUI
        from ..tui.app import main as tui_main

        tui_main()
        sys.exit(0)

    if ctx.invoked_subcommand is None:
        # Default to status if no command
        ctx.invoke(status)


# Daemon commands


@click.command("daemon-stop")
@click.pass_context
def daemon_stop(ctx):
    """Stop the daemon."""
    commands.cmd_daemon_stop()
    print("Daemon stopped")


@click.command("ping")
@click.pass_context
def ping(ctx):
    """Ping the daemon."""
    response = commands.cmd_ping()
    print_response(response, ctx.obj["json"])


@click.command("shutdown")
@click.pass_context
def shutdown(ctx):
    """Gracefully shutdown the daemon."""
    response = commands.cmd_shutdown()
    print_response(response, ctx.obj["json"])


@click.command("devices")
@click.pass_context
def devices(ctx):
    """List available audio playback devices."""
    response = commands.cmd_devices()
    print_response(response, ctx.obj["json"], format_devices)


@click.command("set-device")
@click.argument("device_id", required=False)
@click.pass_context
def set_device(ctx, device_id: str | None):
    """Set the audio playback device by ID.

    Use 'atk devices' to list available devices and their IDs.
    Pass no argument to reset to the default device.
    """
    response = commands.cmd_set_device(device_id)
    print_response(response, ctx.obj["json"])


# Playback commands


@cli.command("play")
@click.argument("file", required=False, type=click.Path())
@click.pass_context
def play(ctx, file: str | None):
    """Play a file or resume playback."""
    response = commands.cmd_play(file)
    print_response(response, ctx.obj["json"])


@cli.command("pause")
@click.pass_context
def pause(ctx):
    """Pause playback."""
    response = commands.cmd_pause()
    print_response(response, ctx.obj["json"])


@cli.command("stop")
@click.pass_context
def stop(ctx):
    """Stop playback."""
    response = commands.cmd_stop()
    print_response(response, ctx.obj["json"])


@cli.command("next")
@click.pass_context
def next_track(ctx):
    """Skip to next track."""
    response = commands.cmd_next()
    print_response(response, ctx.obj["json"])


@cli.command("prev")
@click.pass_context
def prev_track(ctx):
    """Go to previous track."""
    response = commands.cmd_prev()
    print_response(response, ctx.obj["json"])


@cli.command("seek")
@click.argument("position")
@click.pass_context
def seek(ctx, position: str):
    """Seek to position (30, +5, -10, 1:30)."""
    response = commands.cmd_seek(position)
    print_response(response, ctx.obj["json"])


# Queue management commands


@cli.command("add")
@click.argument("uri")
@click.pass_context
def add(ctx, uri: str):
    """Add file/URL to queue."""
    response = commands.cmd_add(uri)
    print_response(response, ctx.obj["json"])


@cli.command("remove")
@click.argument("index", type=int)
@click.pass_context
def remove(ctx, index: int):
    """Remove track from queue by index (0-based)."""
    response = commands.cmd_remove(index)
    print_response(response, ctx.obj["json"])


@cli.command("move")
@click.argument("from_idx", type=int)
@click.argument("to_idx", type=int)
@click.pass_context
def move(ctx, from_idx: int, to_idx: int):
    """Move track in queue."""
    response = commands.cmd_move(from_idx, to_idx)
    print_response(response, ctx.obj["json"])


@cli.command("clear")
@click.pass_context
def clear(ctx):
    """Clear the queue."""
    response = commands.cmd_clear()
    print_response(response, ctx.obj["json"])


@cli.command("queue")
@click.pass_context
def queue(ctx):
    """Show queue contents."""
    response = commands.cmd_queue()
    print_response(response, ctx.obj["json"], format_queue)


@cli.command("jump")
@click.argument("index", type=int)
@click.pass_context
def jump(ctx, index: int):
    """Jump to track at index (0-based)."""
    response = commands.cmd_jump(index)
    print_response(response, ctx.obj["json"])


# State commands


@cli.command("status")
@click.pass_context
def status(ctx):
    """Show current playback status."""
    response = commands.cmd_status()
    print_response(response, ctx.obj["json"], format_status)


@cli.command("info")
@click.argument("index", type=int, required=False)
@click.pass_context
def info(ctx, index: int | None):
    """Show track metadata."""
    response = commands.cmd_info(index)
    print_response(response, ctx.obj["json"])


@cli.command("volume")
@click.argument("level", type=int)
@click.pass_context
def volume(ctx, level: int):
    """Set volume (0-100)."""
    response = commands.cmd_volume(level)
    print_response(response, ctx.obj["json"])


@cli.command("shuffle")
@click.argument("state", type=click.Choice(["on", "off"]), required=False)
@click.pass_context
def shuffle(ctx, state: str | None):
    """Toggle or set shuffle mode."""
    enabled = None
    if state == "on":
        enabled = True
    elif state == "off":
        enabled = False
    response = commands.cmd_shuffle(enabled)
    print_response(response, ctx.obj["json"])


@cli.command("repeat")
@click.argument("mode", type=click.Choice(["none", "queue", "track"]), required=False)
@click.pass_context
def repeat(ctx, mode: str | None):
    """Set repeat mode (none, queue, track)."""
    response = commands.cmd_repeat(mode)
    print_response(response, ctx.obj["json"])


@cli.command("rate")
@click.argument("speed", type=float)
@click.pass_context
def rate(ctx, speed: float):
    """Set playback rate (0.25 to 4.0, default 1.0)."""
    response = commands.cmd_rate(speed)
    print_response(response, ctx.obj["json"])


# Playlist commands


@cli.command("save")
@click.argument("name")
@click.option(
    "--format", "-f", "fmt", type=click.Choice(["json", "m3u", "txt"]), default="json"
)
@click.pass_context
def save(ctx, name: str, fmt: str):
    """Save queue as playlist."""
    response = commands.cmd_save(name, fmt)
    print_response(response, ctx.obj["json"])


@cli.command("load")
@click.argument("name")
@click.pass_context
def load(ctx, name: str):
    """Load playlist."""
    response = commands.cmd_load(name)
    print_response(response, ctx.obj["json"])


@cli.command("playlists")
@click.pass_context
def playlists(ctx):
    """List saved playlists."""
    response = commands.cmd_playlists()
    print_response(response, ctx.obj["json"], format_playlists)


# Event streaming


@cli.command("subscribe")
@click.pass_context
def subscribe(ctx):
    """Stream events from daemon."""
    json_output = ctx.obj["json"]

    try:
        for event in commands.subscribe_to_events():
            print_event(event, json_output)
    except KeyboardInterrupt:
        pass


# Register daemon commands
cli.add_command(daemon_stop)
cli.add_command(ping)
cli.add_command(shutdown)
cli.add_command(devices)
cli.add_command(set_device)


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
