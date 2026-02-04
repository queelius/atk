# ATK - Claude Code Instructions

## Project Overview

ATK (Audio Toolkit) is a non-blocking audio playback daemon with named pipe IPC. Single daemon, KISS philosophy, Unix-style.

## Architecture

```
src/atk/
├── cli/               # Click-based CLI client
│   ├── main.py        # Click command definitions (27 commands)
│   ├── commands.py    # Command implementations (send_command → pipe)
│   └── output.py     # Output formatters (status, queue, devices, playlists)
├── daemon/            # Background daemon (single instance)
│   ├── main.py        # Entry point, signal handling, PID file
│   ├── daemon.py      # Command dispatcher (_handle_request)
│   ├── session.py     # Playback session state (queue, shuffle, repeat)
│   ├── player.py      # Miniaudio wrapper (decode, stream, rate control)
│   └── pipe_handler.py # Named pipe IPC (asyncio + thread executor)
├── protocol/          # JSON message protocol
│   ├── messages.py    # Request/Response/Event dataclasses
│   └── client.py      # Async client helpers
├── tui/               # Textual TUI
│   ├── app.py         # Main app with file picker
│   └── widgets.py     # Progress bar, status widgets
└── config.py          # XDG path configuration
```

## Key Design Decisions

1. **Single daemon** — one instance at `$XDG_RUNTIME_DIR/atk/`, no session multiplexing
2. **Named pipes** — FIFOs (`atk.cmd`, `atk.resp`), simple blocking IPC
3. **JSON protocol** — newline-delimited JSON, versioned (`v: 1`)
4. **Rate-only audio** — tape-style rate control (affects pitch), no DSP/EQ
5. **Miniaudio backend** — decode to float32 PCM, numpy for sample manipulation
6. **Player state separation** — `_active` (generator running) vs `_playing` (producing audio)

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
pytest --cov=atk --cov-report=term-missing
ruff check src/ tests/
mypy src/atk/ --ignore-missing-imports
```

## Testing

- Tests in `tests/` — pytest with asyncio mode
- Mock the Player class for session tests
- Test formatters independently in `test_cli.py`
- Mock `send_command` for CLI command tests

## Adding a New Command

1. Add handler in `daemon/daemon.py` → `_handle_request()`
2. Add `cmd_*()` function in `cli/commands.py`
3. Add Click command in `cli/main.py`
4. Add formatter in `cli/output.py` if needed
5. Add tests in `tests/test_cli.py`

## File Locations

- Runtime: `$XDG_RUNTIME_DIR/atk/` — pipes, PID file
- State: `$XDG_STATE_HOME/atk/` — session state
- Data: `$XDG_DATA_HOME/atk/` — saved playlists

## Code Style

- Type hints throughout, `from __future__ import annotations`
- ruff for linting and formatting
- mypy for type checking
- Docstrings for public APIs only
- Prefer composition over inheritance
