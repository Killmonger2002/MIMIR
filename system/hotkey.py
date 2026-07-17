"""Global hotkey registration for pause/resume and quit."""

from __future__ import annotations

import logging
import threading
from typing import Callable

import keyboard

from config import config
from state import AppState

logger = logging.getLogger("mimir.hotkey")


class HotkeyManager:
    """Registers global hotkeys for pause/resume and quit."""

    def __init__(self, state: AppState, on_quit: Callable[[], None]) -> None:
        self._state = state
        self._on_quit = on_quit

    def _toggle_pause(self) -> None:
        # While dictating, the pause hotkey is the hard-stop escape hatch
        # (in case the spoken "stop dictation" phrase never gets
        # recognized) rather than a normal pause toggle - stopping the
        # dictation loop is what the user means by hitting the key here.
        if self._state.is_dictating():
            self._state.request_stop_dictation()
            logger.info("Dictation stop requested via hotkey")
            return
        paused = self._state.toggle_pause()
        self._state.set_mode("paused" if paused else "idle")
        logger.info("MIMIR %s via hotkey", "paused" if paused else "resumed")

    def start(self) -> None:
        """Register the configured global hotkeys."""
        pause_combo = config.hotkeys.pause_resume
        quit_combo = config.hotkeys.quit

        keyboard.add_hotkey(pause_combo, self._toggle_pause)
        keyboard.add_hotkey(quit_combo, self._on_quit)

        logger.info("Registered hotkeys: pause/resume=%s, quit=%s", pause_combo, quit_combo)

    def stop(self) -> None:
        """Unregister all hotkeys."""
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            logger.exception("Failed to unhook hotkeys")


if __name__ == "__main__":
    import time

    _state = AppState()

    def _quit() -> None:
        print("Quit hotkey pressed")

    manager = HotkeyManager(_state, _quit)
    manager.start()
    print("Hotkeys registered. Press Ctrl+Shift+M to pause, Ctrl+Shift+Q to quit (Ctrl+C to exit).")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        manager.stop()
