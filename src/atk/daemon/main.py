"""ATK Daemon main entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

from ..config import get_effective_runtime_dir, get_effective_state_dir, load_config
from .daemon import Daemon
from .player import Player


class DaemonRunner:
    """Main ATK daemon process."""

    def __init__(self):
        self.config = load_config()
        self.runtime_dir = get_effective_runtime_dir(self.config)
        self.state_dir = get_effective_state_dir(self.config)
        self.daemon: Daemon | None = None
        self._shutdown_event = asyncio.Event()
        self._logger = logging.getLogger("atk.daemon")

    def _setup_logging(self) -> None:
        """Set up logging to file and stderr."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.state_dir / "daemon.log"

        level = getattr(logging, self.config.daemon.log_level.upper(), logging.INFO)

        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

        # File handler
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)

        # Root logger
        root_logger = logging.getLogger("atk")
        root_logger.setLevel(level)
        root_logger.addHandler(file_handler)
        root_logger.addHandler(console_handler)

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        loop = asyncio.get_event_loop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_handler)

    def _signal_handler(self) -> None:
        """Handle shutdown signal."""
        self._logger.info("Received shutdown signal")
        self._shutdown_event.set()

    async def start(self) -> None:
        """Start the daemon."""
        self._setup_logging()
        self._logger.info("Starting ATK daemon")
        self._logger.info(f"Runtime directory: {self.runtime_dir}")
        self._logger.info(f"State directory: {self.state_dir}")

        # Check for existing daemon
        if self._is_already_running():
            self._logger.error("Another daemon instance is already running")
            sys.exit(1)

        # Create directories
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        # Write PID file
        pid_file = self.runtime_dir / "daemon.pid"
        pid_file.write_text(str(os.getpid()))

        # Start daemon
        self.daemon = Daemon(self.runtime_dir)
        await self.daemon.start()

        self._logger.info("ATK daemon started successfully")

    async def stop(self) -> None:
        """Stop the daemon."""
        self._logger.info("Stopping ATK daemon")

        if self.daemon:
            await self.daemon.stop()

        # Clean up PID file
        pid_file = self.runtime_dir / "daemon.pid"
        if pid_file.exists():
            pid_file.unlink()

        # Shutdown player
        Player.shutdown()

        self._logger.info("ATK daemon stopped")

    def _is_already_running(self) -> bool:
        """Check if another daemon is running."""
        pid_file = self.runtime_dir / "daemon.pid"
        cmd_pipe = self.runtime_dir / "atk.cmd"

        if not pid_file.exists():
            return False

        # Check if process is still running
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process exists
            return True
        except (ValueError, ProcessLookupError, PermissionError):
            # Stale PID file, clean up
            if pid_file.exists():
                pid_file.unlink()
            if cmd_pipe.exists():
                cmd_pipe.unlink()
            resp_pipe = self.runtime_dir / "atk.resp"
            if resp_pipe.exists():
                resp_pipe.unlink()
            return False

    async def run(self) -> None:
        """Run the daemon until shutdown."""
        self._setup_signal_handlers()
        await self.start()

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        await self.stop()


async def async_main() -> None:
    """Async entry point."""
    runner = DaemonRunner()
    await runner.run()


def main() -> None:
    """Main entry point for atk-daemon."""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
