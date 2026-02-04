# ATK - Audio Toolkit

A lightweight, non-blocking audio playback daemon with a named pipe protocol. ATK follows the Unix philosophy: do one thing well.

## Features

- **Daemon Architecture**: Background daemon with CLI and TUI clients
- **Named Pipe IPC**: Simple JSON protocol over Unix FIFOs
- **Queue Management**: Add, remove, move, shuffle, repeat
- **Playback Control**: Play, pause, stop, seek, volume, rate (tape-style)
- **Playlist Support**: Save/load playlists (JSON, M3U, TXT formats)
- **Device Selection**: Choose audio output device
- **Event Streaming**: Subscribe to real-time playback events

## Installation

```bash
pip install .

# Or for development
pip install -e ".[dev]"
```

### Dependencies

- Python 3.10+
- miniaudio (audio playback)
- numpy (audio processing)
- textual (TUI)
- click (CLI)

## Quick Start

```bash
# Play a file (daemon starts automatically)
atk play music.mp3

# Add files to queue
atk add track1.mp3
atk add track2.ogg

# Control playback
atk pause
atk play          # resume
atk next
atk prev
atk seek 1:30     # seek to 1:30
atk seek +10      # forward 10s
atk seek -5       # back 5s

# Check status
atk status
# ▶ Artist - Track Name
#   ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░ 1:30 / 4:00
#   Volume: 80%
#   Queue: 2/5

# Queue management
atk queue         # show queue
atk shuffle on
atk repeat queue  # none, queue, track
atk jump 3        # jump to track index

# Playback rate (tape-style, affects pitch)
atk rate 1.5      # 1.5x speed
atk rate 0.75     # 0.75x speed

# Volume
atk volume 80

# Playlists
atk save favorites
atk load favorites
atk playlists

# Device selection
atk devices
atk set-device <device-id>

# TUI mode
atk --tui

# JSON output for scripting
atk --json status
```

## Commands

| Command | Description |
|---------|-------------|
| `play [FILE]` | Play file or resume |
| `pause` | Pause playback |
| `stop` | Stop playback |
| `next` | Next track |
| `prev` | Previous track |
| `seek POS` | Seek (30, +5, -10, 1:30) |
| `volume LEVEL` | Set volume (0-100) |
| `rate SPEED` | Set rate (0.25-4.0) |
| `add URI` | Add to queue |
| `remove INDEX` | Remove from queue |
| `move FROM TO` | Move in queue |
| `clear` | Clear queue |
| `queue` | Show queue |
| `jump INDEX` | Jump to track |
| `shuffle [on\|off]` | Toggle shuffle |
| `repeat [none\|queue\|track]` | Set repeat mode |
| `status` | Show status |
| `info [INDEX]` | Show track info |
| `save NAME` | Save playlist |
| `load NAME` | Load playlist |
| `playlists` | List playlists |
| `devices` | List audio devices |
| `set-device [ID]` | Set audio device |
| `subscribe` | Stream events |
| `ping` | Ping daemon |
| `shutdown` | Stop daemon |

## Architecture

```
┌─────────┐     ┌─────────┐     ┌─────────┐
│   CLI   │     │   TUI   │     │ Scripts │
└────┬────┘     └────┬────┘     └────┬────┘
     │               │               │
     └───────────────┴───────────────┘
                     │
              Named Pipes (FIFO)
              atk.cmd / atk.resp
                     │
              ┌──────┴──────┐
              │   Daemon    │
              │  (Session)  │
              └──────┬──────┘
                     │
              ┌──────┴──────┐
              │   Player    │
              │ (miniaudio) │
              └─────────────┘
```

## Protocol

JSON messages over named pipes at `$XDG_RUNTIME_DIR/atk/`:

```json
// Request
{"v": 1, "id": "uuid", "cmd": "play", "args": {"file": "music.mp3"}}

// Response
{"v": 1, "id": "uuid", "ok": true, "data": {...}}

// Event
{"v": 1, "event": "track_changed", "data": {...}}
```

## Supported Formats

MP3, OGG, FLAC, WAV, OPUS, M4A, AAC

## License

MIT
