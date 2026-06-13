"""Idle-unload monitoring and clean shutdown for MIMIR."""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timedelta

from config import config
from state import AppState

logger = logging.getLogger("mimir.lifecycle")


class LifecycleManager:
    """Background daemon thread that unloads idle models and handles shutdown."""

    def __init__(self, state: AppState) -> None:
        self._state = state
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._idle_since: datetime | None = None
        self._unloaded = False
        self._extra_threads: list[threading.Thread] = []

    def register_thread(self, thread: threading.Thread) -> None:
        """Register a daemon thread that should be joined on shutdown."""
        self._extra_threads.append(thread)

    def start(self) -> None:
        """Start the idle-monitoring background thread."""
        self._thread = threading.Thread(target=self._run, name="lifecycle-manager", daemon=True)
        self._thread.start()
        logger.info("Lifecycle manager started")

    def _run(self) -> None:
        check_interval = config.lifecycle.check_interval_seconds
        idle_timeout = timedelta(minutes=config.lifecycle.idle_unload_minutes)

        while not self._stop_event.wait(check_interval):
            mode = self._state.get_mode()

            if mode == "idle":
                if self._idle_since is None:
                    self._idle_since = datetime.now()
                elif not self._unloaded and datetime.now() - self._idle_since >= idle_timeout:
                    self._unload_models()
                    self._unloaded = True
            else:
                self._idle_since = None
                self._unloaded = False

    def _unload_models(self) -> None:
        """Unload Whisper and log a no-op for Ollama (which manages its own memory)."""
        try:
            from core import stt

            stt.unload()
            logger.info("Unloaded Whisper model after %d minutes idle", config.lifecycle.idle_unload_minutes)
        except Exception:
            logger.exception("Failed to unload Whisper model")

        try:
            from core import tts

            tts.unload()
            logger.info("Unloaded Piper voice after %d minutes idle", config.lifecycle.idle_unload_minutes)
        except Exception:
            logger.exception("Failed to unload Piper voice")

        logger.info("Ollama model unload is a no-op; Ollama manages its own memory")

    def shutdown(self) -> None:
        """Stop the lifecycle thread and join all registered daemon threads."""
        logger.info("Shutting down MIMIR")
        self._stop_event.set()

        if self._thread is not None:
            self._thread.join(timeout=2)

        for thread in self._extra_threads:
            thread.join(timeout=2)

        logger.info("Shutdown complete")
