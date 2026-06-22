"""MIMIR entry point.

Wires together wake-word detection, STT, intent routing, executors, TTS,
the system tray, global hotkeys, and the lifecycle manager.
"""

from __future__ import annotations

import importlib
import logging
import logging.handlers
import os
import sys
import threading
import time

import win32api
import win32event
import winerror

from config import config
from core import stt, tts, wake_word
from core.intent_router import classify
from state import AppState
from system.hotkey import HotkeyManager
from system.lifecycle import LifecycleManager
from system.tray_icon import TrayIcon
from ui.settings_window import open_settings_window
from ui.transcript_window import open_transcript_window

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "MIMIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "mimir.log")

_SINGLE_INSTANCE_MUTEX_NAME = "Global\\MIMIR_SingleInstanceMutex"


def _acquire_single_instance_lock():
    """Return a mutex handle if this is the only running instance, else None."""
    mutex = win32event.CreateMutex(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    if winerror.ERROR_ALREADY_EXISTS == win32api.GetLastError():
        return None
    return mutex


def _setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)

    level_name = config.logging.level.upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)


logger = logging.getLogger("mimir.main")


class Mimir:
    """Top-level orchestrator wiring all MIMIR subsystems together."""

    def __init__(self) -> None:
        self.state = AppState()
        self.lifecycle = LifecycleManager(self.state)
        self.hotkeys = HotkeyManager(self.state, on_quit=self.shutdown)
        self.wake_word_listener = wake_word.WakeWordListener(self.state, on_detected=self._on_wake_word)
        self.tray: TrayIcon | None = None

    def _on_wake_word(self) -> None:
        """Callback fired by the wake-word listener; runs the command loop."""
        try:
            self._handle_command_cycle()
        except Exception:
            logger.exception("Unexpected error in command handling")
            tts.speak("Sorry, something went wrong with that.", self.state)
            self.state.set_mode("idle")

    def _handle_command_cycle(self) -> None:
        """Listen, transcribe, route, execute, and respond - looping on followups."""
        first_loop = True
        while True:
            if first_loop:
                tts.speak("Listening", self.state)
                first_loop = False

            self.state.set_mode("listening")
            audio = stt.record_until_silence()

            self.state.set_mode("thinking")
            transcript = stt.transcribe(audio)

            if not transcript:
                self.state.set_mode("idle")
                return

            executor_name = classify(transcript)

            if executor_name == "unknown":
                result_speak = "I didn't understand that command."
                self.state.add_log_entry(transcript, result_speak, executor_name)
                tts.speak(result_speak, self.state)
                self.state.set_mode("idle")
                return

            try:
                executor_module = importlib.import_module(f"executors.{executor_name}")
                result = executor_module.execute(transcript, self.state)
            except Exception:
                logger.exception("Executor %s raised an exception", executor_name)
                result = type(
                    "Result",
                    (),
                    {
                        "success": False,
                        "speak": "Sorry, something went wrong with that.",
                        "needs_followup": False,
                        "shutdown": False,
                    },
                )()

            self.state.add_log_entry(transcript, result.speak, executor_name)

            tts.speak(result.speak, self.state)

            if getattr(result, "shutdown", False):
                self.shutdown()
                return

            if not result.needs_followup:
                self.state.set_mode("idle")
                return
            # Loop again immediately to capture the follow-up answer.

    def shutdown(self) -> None:
        """Run the full shutdown sequence: announce, stop threads, exit."""
        logger.info("Shutdown requested")
        self.state.set_mode("shutting_down")
        tts.speak("MIMIR shutting down", self.state)

        # Run the actual stop/join sequence on a dedicated thread. shutdown()
        # can be invoked from the wake-word listener's own thread (voice
        # "shut down" command), and WakeWordListener.stop()/thread.join()
        # raises "cannot join current thread" if called from that thread.
        threading.Thread(target=self._shutdown_worker, name="shutdown-worker", daemon=True).start()

    def _shutdown_worker(self) -> None:
        self.wake_word_listener.stop()
        self.hotkeys.stop()
        self.lifecycle.shutdown()

        if self.tray is not None:
            self.tray.stop()

        time.sleep(0.5)
        os._exit(0)

    def run(self) -> None:
        """Start all subsystems and run the tray icon on the main thread."""
        _setup_logging()
        logger.info("MIMIR starting up")

        self.state.set_mode("idle")
        tts.speak("MIMIR at your service", self.state)

        self.wake_word_listener.start()
        self.hotkeys.start()
        self.lifecycle.start()

        self.tray = TrayIcon(
            self.state,
            on_open_transcript=lambda: open_transcript_window(self.state),
            on_open_settings=lambda: open_settings_window(),
            on_quit=self.shutdown,
        )
        self.tray.run()


if __name__ == "__main__":
    _lock = _acquire_single_instance_lock()
    if _lock is None:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, "MIMIR is already running.", "MIMIR", 0x40)
        sys.exit(1)
    Mimir().run()
