# ATK - Claude Code Instructions

## Project Overview

ATK (Audio Toolkit) is a non-blocking audio playback daemon with named pipe IPC. Single daemon, KISS philosophy, Unix-style. Designed as a "dumb daemon + smart agent" — minimal core, intelligence lives in the LLM controller.

## Architecture

```
src/atk/
├── player.py      # Miniaudio wrapper, WSOLA time-stretch, tape rate (~250 lines)
├── daemon.py      # Pipe handler + command dispatch + queue state (~500 lines)
├── cli.py         # Click CLI, pipe client, output formatters (~370 lines)
├── config.py      # XDG path helpers (~30 lines)
├── __init__.py
└── tui/           # Optional Textual TUI
    ├── app.py     # Main app with file picker
    └── widgets.py # Progress bar, status widgets
```

## Key Design Decisions

1. **Single daemon** — one instance at `$XDG_RUNTIME_DIR/atk/`, no session multiplexing
2. **Named pipes** — FIFOs (`atk.cmd`, `atk.resp`), simple blocking IPC
3. **Plain dict protocol** — newline-delimited JSON, no typed dataclasses
4. **Two rate modes** — WSOLA time-stretch (default, preserves pitch) and tape-style (pitch changes with speed)
5. **Miniaudio backend** — full-file decode to float32 PCM, numpy for sample manipulation
6. **Flat structure** — 4 source files at package root, no sub-packages except TUI

## Protocol

Request: `{"id": "uuid", "cmd": "play", "args": {"file": "/path/to/audio.mp3"}}`
Response: `{"id": "uuid", "ok": true, "data": {"state": "playing"}}`
Event: `{"event": "track_changed", "data": {"track": {...}}}`

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
pytest --cov=atk --cov-report=term-missing
ruff check src/ tests/
ruff format src/ tests/
```

## Testing

- Tests in `tests/` — pytest with asyncio mode
- `test_daemon.py` — tests Daemon class directly via `daemon._cmd_*()` methods
- `test_cli.py` — tests formatters, parse_seek, and Click commands (mock `send_command`)
- `conftest.py` — mock miniaudio, sample audio files, temp dirs

## Adding a New Command

1. Add `_cmd_foo(self, args: dict) -> dict` method in `daemon.py` `Daemon` class
2. Register in `handlers` dict in `Daemon._dispatch()`
3. Add Click command in `cli.py` that calls `send_command("foo", {...})`
4. Add formatter in `cli.py` if needed (e.g. `fmt_foo()`)
5. Add tests in both `test_daemon.py` and `test_cli.py`

## File Locations

- Runtime: `$XDG_RUNTIME_DIR/atk/` — pipes, PID file
- Data: `$XDG_DATA_HOME/atk/` — saved playlists

## Code Style

- Type hints throughout, `from __future__ import annotations`
- ruff for linting and formatting
- Docstrings for public APIs only
- Prefer composition over inheritance
