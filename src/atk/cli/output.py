"""Output formatting for CLI."""

from __future__ import annotations

import json
import sys
from typing import Any

from ..protocol.messages import Response, Event


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


def format_track(track: dict | None, include_duration: bool = True) -> str:
    """Format track info for display."""
    if not track:
        return "(no track)"

    parts = []

    if track.get("artist"):
        parts.append(track["artist"])

    if track.get("title"):
        parts.append(track["title"])
    elif track.get("uri"):
        # Use filename from URI
        uri = track["uri"]
        parts.append(uri.split("/")[-1])

    text = " - ".join(parts) if parts else track.get("uri", "(unknown)")

    if include_duration and track.get("duration"):
        text += f" [{format_time(track['duration'])}]"

    return text


def format_status(data: dict) -> str:
    """Format status response for display."""
    lines = []

    # State and track
    state = data.get("state", "stopped")
    state_icon = {"playing": "▶", "paused": "⏸", "stopped": "⏹"}.get(state, "?")

    track = data.get("track")
    track_str = format_track(track, include_duration=False)
    lines.append(f"{state_icon} {track_str}")

    # Progress bar
    if track:
        position = data.get("position", 0)
        duration = data.get("duration", 0)

        if duration > 0:
            progress = position / duration
            bar_width = 40
            filled = int(bar_width * progress)
            bar = "▓" * filled + "░" * (bar_width - filled)
            lines.append(f"  {bar} {format_time(position)} / {format_time(duration)}")

    # Settings
    volume = data.get("volume", 0)
    shuffle = "🔀" if data.get("shuffle") else "  "
    repeat_mode = data.get("repeat", "none")
    repeat_icon = {"none": "  ", "queue": "🔁", "track": "🔂"}.get(repeat_mode, "  ")

    lines.append(f"  Volume: {volume}%  {shuffle} {repeat_icon}")

    # DSP settings (only show if non-default)
    dsp_parts = []
    rate = data.get("rate", 1.0)
    if rate != 1.0:
        dsp_parts.append(f"Rate: {rate:.2f}x")
    pitch = data.get("pitch", 0.0)
    if pitch != 0.0:
        dsp_parts.append(f"Pitch: {pitch:+.1f}st")
    bass = data.get("bass", 0.0)
    treble = data.get("treble", 0.0)
    if bass != 0.0 or treble != 0.0:
        dsp_parts.append(f"EQ: Bass {bass:+.0f}dB / Treble {treble:+.0f}dB")
    if dsp_parts:
        lines.append(f"  {' | '.join(dsp_parts)}")

    # Loop info
    loop_a = data.get("loop_a")
    loop_b = data.get("loop_b")
    loop_enabled = data.get("loop_enabled", False)
    if loop_a is not None and loop_b is not None:
        loop_status = "🔁" if loop_enabled else "off"
        lines.append(f"  Loop: {format_time(loop_a)} - {format_time(loop_b)} [{loop_status}]")

    # Queue info
    queue_len = data.get("queue_length", 0)
    queue_pos = data.get("queue_position", 0)
    if queue_len > 0:
        lines.append(f"  Queue: {queue_pos + 1}/{queue_len}")

    return "\n".join(lines)


def format_queue(data: dict) -> str:
    """Format queue response for display."""
    tracks = data.get("tracks", [])
    current = data.get("current_index", 0)

    if not tracks:
        return "(empty queue)"

    lines = []
    for i, track in enumerate(tracks):
        prefix = "▶ " if i == current else "  "
        track_str = format_track(track)
        lines.append(f"{prefix}{i + 1}. {track_str}")

    return "\n".join(lines)


def format_sessions(data: dict) -> str:
    """Format session list for display."""
    sessions = data.get("sessions", [])

    if not sessions:
        return "(no active sessions)"

    lines = []
    for session in sessions:
        name = session.get("name", "?")
        state = session.get("state", "stopped")
        state_icon = {"playing": "▶", "paused": "⏸", "stopped": "⏹"}.get(state, "?")

        track = session.get("track")
        track_str = format_track(track) if track else "(no track)"

        lines.append(f"{state_icon} {name}: {track_str}")

    return "\n".join(lines)


def format_event(event: Event) -> str:
    """Format event for display."""
    etype = event.event.value
    data = event.data

    if etype == "track_changed":
        track = data.get("track", {})
        return f"[track_changed] {format_track(track)}"
    elif etype == "playback_started":
        return "[playback_started]"
    elif etype == "playback_paused":
        pos = data.get("position", 0)
        return f"[playback_paused] at {format_time(pos)}"
    elif etype == "playback_stopped":
        return "[playback_stopped]"
    elif etype == "position_update":
        pos = data.get("position", 0)
        dur = data.get("duration", 0)
        return f"[position] {format_time(pos)} / {format_time(dur)}"
    elif etype == "queue_updated":
        queue = data.get("queue", {})
        count = len(queue.get("tracks", []))
        return f"[queue_updated] {count} tracks"
    elif etype == "queue_finished":
        return "[queue_finished]"
    elif etype == "error":
        code = data.get("code", "?")
        msg = data.get("message", "")
        return f"[error] {code}: {msg}"
    else:
        return f"[{etype}] {json.dumps(data)}"


def print_response(
    response: Response,
    json_output: bool = False,
    formatter: callable = None,
) -> None:
    """Print response to stdout."""
    if json_output:
        print(json.dumps(response.to_dict(), indent=2))
        return

    if not response.ok:
        error = response.error
        if error:
            print(f"Error [{error.code}]: {error.message}", file=sys.stderr)
        else:
            print("Unknown error", file=sys.stderr)
        sys.exit(1)

    data = response.data or {}

    if formatter:
        print(formatter(data))
    elif data:
        # Default: print key=value pairs
        for key, value in data.items():
            if isinstance(value, dict):
                print(f"{key}:")
                for k, v in value.items():
                    print(f"  {k}: {v}")
            elif isinstance(value, list):
                print(f"{key}:")
                for item in value:
                    print(f"  - {item}")
            else:
                print(f"{key}: {value}")
    else:
        print("OK")


def print_event(event: Event, json_output: bool = False) -> None:
    """Print event to stdout."""
    if json_output:
        print(json.dumps(event.to_dict()))
    else:
        print(format_event(event))
    sys.stdout.flush()
