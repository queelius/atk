# ATK: Motivation and Direction

## What Is ATK?

A minimal audio playback daemon with named pipe IPC. ~4,000 lines of Python, 4 dependencies (miniaudio, numpy, textual, click).

## Core Thesis

Traditional audio players pack intelligence into the application: library management, metadata databases, smart playlists, chapter parsing. ATK inverts this. The daemon is deliberately minimal — play, pause, seek, queue — and delegates intelligence to whatever controls it.

When that controller is an LLM agent, the "dumb" daemon becomes surprisingly capable.

## The Agent-Native Design Pattern

ATK is an example of **agent-native Unix service design**: services built to be controlled by LLMs as easily as by humans.

Properties:
1. **Text protocol** — JSON over named pipes. Agents generate JSON naturally.
2. **Filesystem IPC** — pipes at `$XDG_RUNTIME_DIR/atk/`. No libraries, no connection management. `echo '{"v":1,"cmd":"pause","args":{},"id":"x"}' > atk.cmd` is a complete client.
3. **Stateless commands** — each request is self-contained. No session setup, no handshake.
4. **Shell-composable** — `echo` + `cat` work. So does a Claude Code skill.
5. **Deliberately dumb** — no library management, no metadata DB, no chapter detection. The agent can `find` files, parse metadata, and figure out where to seek. Every feature not built into ATK is a feature that benefits from the agent's general intelligence.

## What ATK Isn't

- A music library manager (use `find`, `ls`, or ask Claude)
- A metadata database (parse it at the agent layer)
- A chapter/bookmark system (the agent knows where to seek)
- A streaming service client (download first, play local files)
- A DSP/effects engine (tape-style rate is the only manipulation)

## Comparison to Existing Tools

- **MPD** — TCP sockets, custom text protocol, built-in library/database. Powerful but complex. Requires a client library to use well.
- **PulseAudio/PipeWire** — system-level audio routing, not application-level playback control.
- **VLC CLI** — can be controlled via CLI but wasn't designed for it. Complex interface.
- **ATK** — named pipes + JSON. The simplest possible IPC that works for humans, scripts, and agents.

## Target Workflow

1. Sit down, start working. Tell Claude: "find me something to listen to."
2. Claude fetches audio, creates a playlist, loads it into ATK.
3. "Skip to the next chapter" — Claude parses timestamps, sends `atk seek 45:30`.
4. "Turn it down" — Claude sends `atk volume 40`.
5. ATK doesn't know what a "chapter" is. Claude does. The intelligence lives in the agent.

## Next Steps

1. Write a Claude Code skill for natural audio control
2. Update README with this framing
3. Keep it small. Don't over-build.
