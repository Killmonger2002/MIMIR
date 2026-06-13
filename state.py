"""Centralized, thread-safe application state for MIMIR.

All other modules must interact with state exclusively through the
methods on AppState - never via direct attribute access.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

VALID_MODES = {"idle", "listening", "thinking", "speaking", "paused", "shutting_down"}

MAX_LOG_ENTRIES = 200


class AppState:
    """Holds shared mutable state, guarded by a single lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode: str = "idle"
        self._is_paused: bool = False
        self._conversation_log: list[dict[str, Any]] = []

    def set_mode(self, mode: str) -> None:
        """Set the current operating mode."""
        if mode not in VALID_MODES:
            raise ValueError(f"Invalid mode: {mode!r}")
        with self._lock:
            self._mode = mode

    def get_mode(self) -> str:
        """Return the current operating mode."""
        with self._lock:
            return self._mode

    def toggle_pause(self) -> bool:
        """Toggle the paused flag and return the new value."""
        with self._lock:
            self._is_paused = not self._is_paused
            return self._is_paused

    def is_paused(self) -> bool:
        """Return True if MIMIR is currently paused."""
        with self._lock:
            return self._is_paused

    def add_log_entry(self, transcript: str, response: str, executor: str) -> None:
        """Append a conversation entry, capping the log at MAX_LOG_ENTRIES."""
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "transcript": transcript,
            "response": response,
            "executor": executor,
        }
        with self._lock:
            self._conversation_log.append(entry)
            if len(self._conversation_log) > MAX_LOG_ENTRIES:
                self._conversation_log = self._conversation_log[-MAX_LOG_ENTRIES:]

    def get_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return up to the last `limit` conversation log entries."""
        with self._lock:
            if limit <= 0:
                return list(self._conversation_log)
            return list(self._conversation_log[-limit:])
