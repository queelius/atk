# ATK Radical Simplification Plan

## Goal

Reduce the core (excluding TUI) from ~3300 lines to ~1200 lines. 4 files. No ceremony.

## Target Structure

```
src/atk/
├── player.py      # Miniaudio wrapper (~200 lines)
├── daemon.py      # Pipe handler + command dispatch + queue state (~500 lines)
├── cli.py         # Table-driven CLI, thin pipe client (~300 lines)
├── config.py      # XDG paths, that's it (~30 lines)
├── __init__.py
└── tui/           # Untouched, optional viewer
    ├── app.py
    └── widgets.py
```

## What Gets Killed

### Protocol layer (protocol/) — DELETE ENTIRELY
- No `Request`, `Response`, `Event` dataclasses
- No `ErrorCode`, `EventType`, `RepeatMode` enums
- No `TrackInfo`, `QueueInfo`, `StatusInfo`, `PlaylistInfo`
- No `client.py`, no `parse_message()`
- Protocol is: send `{"cmd": "play", "args": {"file": "x.mp3"}}`, get `{"ok": true, "data": {...}}` or `{"ok": false, "error": "message"}`
- That's it. Dicts in, dicts out. JSON lines over pipes.

### Config system — REPLACE WITH 30 LINES
- No `DaemonConfig`, `DefaultsConfig`, `PathsConfig`, `Config` dataclasses
- No TOML loading
- Just: `get_runtime_dir()`, `get_state_dir()`, `get_data_dir()`

### CLI layer (cli/) — MERGE INTO ONE FILE
- No separate `commands.py`, `main.py`, `output.py`
- Table-driven: commands defined as `{"play": {"args": ["file?"], "help": "Play or resume"}}`
- One `send()` function that writes to pipe, reads response
- Formatters for `status` and `queue` only (everything else prints the dict)

### Daemon layer (daemon/) — MERGE INTO ONE FILE
- No separate `session.py`, `daemon.py`, `main.py`, `pipe_handler.py`
- Inline the pipe handling (it's ~80 lines)
- Queue state is just lists + an index, not a class
- Command dispatch is a dict of handler functions, not if/elif

### Unused code — DELETE
- `PipeHandler` class (only `MultiClientPipeHandler` is used)
- `protocol/client.py` (async helpers never used by daemon)
- `get_track_duration()` mutagen fallback (optional dep nobody installs)

## What Stays

- `player.py` — decode, stream, rate, volume, seek, device selection
- Queue logic — add, remove, move, shuffle, repeat, jump
- Pipe I/O — read JSON from cmd pipe, write JSON to resp pipe
- CLI — `atk play`, `atk status`, etc.
- Playlist save/load — JSON, M3U, TXT
- TUI — untouched in its own directory
- XDG paths

## Protocol (simplified)

Request: `{"cmd": "play", "args": {"file": "/path/to/song.mp3"}}`
Response: `{"ok": true, "data": {"state": "playing", "track": "song.mp3"}}`
Error: `{"ok": false, "error": "File not found: /path/to/song.mp3"}`
Event: `{"event": "track_changed", "data": {"track": "song.mp3"}}`

No version field. No correlation IDs. No typed enums. Just JSON.

## Line Budget

| File | Target |
|------|--------|
| player.py | ~200 |
| daemon.py | ~500 |
| cli.py | ~300 |
| config.py | ~30 |
| __init__.py | ~3 |
| **Total (core)** | **~1033** |
| tui/ (unchanged) | ~691 |

## Migration

1. Write new files alongside old ones
2. Update TUI to use simplified protocol (just dict access)
3. Update tests
4. Delete old files
5. Verify: `ruff check`, `mypy`, `pytest`
