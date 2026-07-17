"""Centralized, thread-safe application state for MIMIR.

All other modules must interact with state exclusively through the
methods on AppState - never via direct attribute access.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

VALID_MODES = {"idle", "listening", "thinking", "speaking", "paused", "shutting_down", "dictating"}

MAX_LOG_ENTRIES = 200
MAX_CAPTION_ENTRIES = 50


class AppState:
    """Holds shared mutable state, guarded by a single lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode: str = "idle"
        self._is_paused: bool = False
        self._conversation_log: list[dict[str, Any]] = []
        self._captions: list[dict[str, Any]] = []
        self._last_folder: str | None = None
        self._dictating: bool = False
        # A plain Event, not lock-guarded state: it's set from the hotkey
        # thread and polled from the dictation loop thread, and Event is
        # already threadsafe. Lets the pause hotkey (or a tray item) break
        # a running dictation session out of its record/transcribe/type
        # loop between utterances even if the spoken "stop dictation"
        # phrase is never recognized.
        self._dictation_stop = threading.Event()

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

    def add_caption(self, speaker: str, text: str) -> None:
        """Append one live-caption line ("you" or "mimir"), independent of
        add_log_entry()'s curated command history.

        The command log only gets an entry once a full command cycle
        resolves (transcribe -> classify -> execute), so it silently
        drops everything else that's actually said out loud - the "Yes?"
        listening cue, confirmation questions ("Did you mean X?"), a
        user's spoken yes/no reply, and any transcript that never became
        a recognized command. The live transcript bar needs ALL of that
        to actually read as a line-by-line conversation instead of one
        result appearing per finished command - observed live, 2026-07-17.
        """
        if not text or not text.strip():
            return
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "speaker": speaker,
            "text": text.strip(),
        }
        with self._lock:
            self._captions.append(entry)
            if len(self._captions) > MAX_CAPTION_ENTRIES:
                self._captions = self._captions[-MAX_CAPTION_ENTRIES:]

    def get_captions(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return up to the last `limit` live-caption lines."""
        with self._lock:
            if limit <= 0:
                return list(self._captions)
            return list(self._captions[-limit:])

    def caption_count(self) -> int:
        with self._lock:
            return len(self._captions)

    def set_dictating(self, active: bool) -> None:
        """Mark dictation as active/inactive. Clears any pending stop
        request when starting, so a stale request from a previous session
        can't immediately end the new one."""
        with self._lock:
            self._dictating = active
        if active:
            self._dictation_stop.clear()

    def is_dictating(self) -> bool:
        with self._lock:
            return self._dictating

    def request_stop_dictation(self) -> None:
        """Ask a running dictation loop to stop at the next utterance
        boundary. Safe to call from any thread (e.g. the hotkey thread)."""
        self._dictation_stop.set()

    def dictation_stop_requested(self) -> bool:
        return self._dictation_stop.is_set()

    def set_last_folder(self, path: str) -> None:
        """Remember the most recently opened folder, for context-aware navigation."""
        with self._lock:
            self._last_folder = path

    def get_last_folder(self) -> str | None:
        """Return the most recently opened folder path, if any."""
        with self._lock:
            return self._last_folder
