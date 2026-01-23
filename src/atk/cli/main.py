"""ATK CLI main entry point."""

from __future__ import annotations

import sys

import click

from . import commands
from .output import (
    format_status,
    format_queue,
    format_sessions,
    print_response,
    print_event,
)


@click.group(invoke_without_command=True)
@click.option("--json", "json_output", is_flag=True, help="Output raw JSON")
@click.option("--session", "-s", default=None, help="Session name")
@click.option("--tui", is_flag=True, help="Launch TUI")
@click.pass_context
def cli(ctx, json_output: bool, session: str | None, tui: bool):
    """ATK - Audio Toolkit

    Non-blocking audio playback daemon with named pipe protocol.
    """
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    ctx.obj["session"] = session

    if tui:
        # Launch TUI
        from ..tui.app import main as tui_main

        tui_main()
        sys.exit(0)

    if ctx.invoked_subcommand is None:
        # Default to status if no command
        ctx.invoke(status)


# Session management commands


@cli.command("list")
@click.pass_context
def list_sessions(ctx):
    """List active sessions."""
    response = commands.cmd_list()
    print_response(response, ctx.obj["json"], format_sessions)


@cli.command("new")
@click.argument("name", required=False)
@click.pass_context
def new_session(ctx, name: str | None):
    """Create a new session."""
    response = commands.cmd_new(name)
    print_response(response, ctx.obj["json"])


@cli.command("kill")
@click.argument("name")
@click.pass_context
def kill_session(ctx, name: str):
    """Kill a session."""
    response = commands.cmd_kill(name)
    print_response(response, ctx.obj["json"])


@cli.command("daemon-stop")
@click.pass_context
def daemon_stop(ctx):
    """Stop the daemon."""
    commands.cmd_daemon_stop()
    print("Daemon stopped")


# Playback commands


@cli.command("play")
@click.argument("file", required=False, type=click.Path())
@click.pass_context
def play(ctx, file: str | None):
    """Play a file or resume playback."""
    response = commands.cmd_play(file, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("pause")
@click.pass_context
def pause(ctx):
    """Pause playback."""
    response = commands.cmd_pause(ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("stop")
@click.pass_context
def stop(ctx):
    """Stop playback."""
    response = commands.cmd_stop(ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("next")
@click.pass_context
def next_track(ctx):
    """Skip to next track."""
    response = commands.cmd_next(ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("prev")
@click.pass_context
def prev_track(ctx):
    """Go to previous track."""
    response = commands.cmd_prev(ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("seek")
@click.argument("position")
@click.pass_context
def seek(ctx, position: str):
    """Seek to position (30, +5, -10, 1:30)."""
    response = commands.cmd_seek(position, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


# Queue management commands


@cli.command("add")
@click.argument("uri")
@click.pass_context
def add(ctx, uri: str):
    """Add file/URL to queue."""
    response = commands.cmd_add(uri, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("remove")
@click.argument("index", type=int)
@click.pass_context
def remove(ctx, index: int):
    """Remove track from queue by index (0-based)."""
    response = commands.cmd_remove(index, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("move")
@click.argument("from_idx", type=int)
@click.argument("to_idx", type=int)
@click.pass_context
def move(ctx, from_idx: int, to_idx: int):
    """Move track in queue."""
    response = commands.cmd_move(from_idx, to_idx, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("clear")
@click.pass_context
def clear(ctx):
    """Clear the queue."""
    response = commands.cmd_clear(ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("queue")
@click.pass_context
def queue(ctx):
    """Show queue contents."""
    response = commands.cmd_queue(ctx.obj["session"])
    print_response(response, ctx.obj["json"], format_queue)


# State commands


@cli.command("status")
@click.pass_context
def status(ctx):
    """Show current playback status."""
    response = commands.cmd_status(ctx.obj["session"])
    print_response(response, ctx.obj["json"], format_status)


@cli.command("info")
@click.argument("index", type=int, required=False)
@click.pass_context
def info(ctx, index: int | None):
    """Show track metadata."""
    response = commands.cmd_info(index, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("volume")
@click.argument("level", type=int)
@click.pass_context
def volume(ctx, level: int):
    """Set volume (0-100)."""
    response = commands.cmd_volume(level, ctx.obj["session"])
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
    response = commands.cmd_shuffle(enabled, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("repeat")
@click.argument("mode", type=click.Choice(["none", "queue", "track"]), required=False)
@click.pass_context
def repeat(ctx, mode: str | None):
    """Set repeat mode (none, queue, track)."""
    response = commands.cmd_repeat(mode, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


# DSP commands


@cli.command("rate")
@click.argument("speed", type=float)
@click.pass_context
def rate(ctx, speed: float):
    """Set playback rate (0.25 to 4.0, default 1.0)."""
    response = commands.cmd_rate(speed, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("pitch")
@click.argument("semitones", type=float)
@click.pass_context
def pitch(ctx, semitones: float):
    """Set pitch shift in semitones (-12 to +12, default 0)."""
    response = commands.cmd_pitch(semitones, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("bass")
@click.argument("db", type=float)
@click.pass_context
def bass(ctx, db: float):
    """Set bass EQ adjustment in dB (-12 to +12, default 0)."""
    response = commands.cmd_bass(db, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("treble")
@click.argument("db", type=float)
@click.pass_context
def treble(ctx, db: float):
    """Set treble EQ adjustment in dB (-12 to +12, default 0)."""
    response = commands.cmd_treble(db, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("fade")
@click.argument("to", type=int)
@click.argument("duration", type=float)
@click.pass_context
def fade(ctx, to: int, duration: float):
    """Fade volume to target (0-100) over duration in seconds."""
    response = commands.cmd_fade(to, duration, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("loop")
@click.argument("args", nargs=-1)
@click.pass_context
def loop(ctx, args: tuple):
    """Set A/B loop. Usage: loop A B | loop on | loop off | loop clear"""
    a = None
    b = None
    enabled = None

    if len(args) == 0:
        # Show current loop status
        response = commands.cmd_status(ctx.obj["session"])
        if response.ok and response.data:
            loop_a = response.data.get("loop_a")
            loop_b = response.data.get("loop_b")
            loop_on = response.data.get("loop_enabled", False)
            if loop_a is not None and loop_b is not None:
                status = "enabled" if loop_on else "disabled"
                print(f"Loop: {loop_a:.1f}s - {loop_b:.1f}s ({status})")
            else:
                print("Loop: not set")
        return
    elif len(args) == 1:
        arg = args[0].lower()
        if arg == "on":
            enabled = True
        elif arg == "off":
            enabled = False
        elif arg == "clear":
            a = 0.0
            b = 0.0
            enabled = False
        else:
            try:
                # Single time value - set as point A
                a = float(arg)
            except ValueError:
                print(f"Invalid argument: {args[0]}")
                return
    elif len(args) == 2:
        try:
            a = float(args[0])
            b = float(args[1])
        except ValueError:
            print(f"Invalid arguments: {args}")
            return

    response = commands.cmd_loop(a, b, enabled, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


# Persistence commands


@cli.command("save")
@click.argument("name")
@click.pass_context
def save(ctx, name: str):
    """Save session state."""
    response = commands.cmd_save(name, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


@cli.command("load")
@click.argument("name")
@click.pass_context
def load(ctx, name: str):
    """Load session state."""
    response = commands.cmd_load(name, ctx.obj["session"])
    print_response(response, ctx.obj["json"])


# Event streaming


@cli.command("subscribe")
@click.pass_context
def subscribe(ctx):
    """Stream events from session."""
    session = ctx.obj["session"] or "default"
    json_output = ctx.obj["json"]

    try:
        for event in commands.subscribe_to_session(session):
            print_event(event, json_output)
    except KeyboardInterrupt:
        pass


def main():
    """Main entry point."""
    cli()


if __name__ == "__main__":
    main()
