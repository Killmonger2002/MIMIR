"""System tray icon reflecting MIMIR's current mode.

Generates simple solid-color circle icons programmatically (no external
image files required) for idle, listening, thinking, speaking, and
paused states.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

import pystray
from PIL import Image, ImageDraw

from state import AppState

logger = logging.getLogger("mimir.tray_icon")

_COLORS = {
    "idle": (66, 135, 245),       # blue
    "listening": (76, 175, 80),   # green
    "thinking": (255, 193, 7),    # amber
    "speaking": (156, 39, 176),   # purple
    "paused": (128, 128, 128),    # gray
    "shutting_down": (128, 128, 128),
    "dictating": (217, 154, 63),  # orange
}

_POLL_INTERVAL_SEC = 0.3


def _make_icon_image(color: tuple[int, int, int]) -> Image.Image:
    """Create a simple solid-color circle icon."""
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color)
    return image


class TrayIcon:
    """Manages the system tray icon and its menu."""

    def __init__(
        self,
        state: AppState,
        on_open_transcript: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_listen_now: Callable[[], None],
        on_quit: Callable[[], None],
        on_toggle_transcript_bar: Callable[[], None] | None = None,
    ) -> None:
        self._state = state
        self._on_open_transcript = on_open_transcript
        self._on_open_settings = on_open_settings
        self._on_listen_now = on_listen_now
        self._on_quit = on_quit
        self._on_toggle_transcript_bar = on_toggle_transcript_bar
        self._icon = pystray.Icon(
            "MIMIR",
            icon=_make_icon_image(_COLORS["idle"]),
            title="MIMIR",
            menu=self._build_menu(),
        )
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _build_menu(self) -> pystray.Menu:
        items = [
            pystray.MenuItem(
                "Open Transcript", lambda: self._on_open_transcript(), default=True
            ),
            # Manual fallback for when the wake word doesn't fire - starts
            # the same listen/transcribe/route/execute cycle a spoken
            # "hey mimir" would, without needing the wake word at all.
            pystray.MenuItem("Listen Now", lambda: self._on_listen_now()),
            pystray.MenuItem("Settings", lambda: self._on_open_settings()),
        ]
        if self._on_toggle_transcript_bar is not None:
            items.append(pystray.MenuItem(self._transcript_bar_label, lambda: self._on_toggle_transcript_bar()))
        items.extend(
            [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(self._pause_label, self._toggle_pause),
                pystray.MenuItem("Quit MIMIR", lambda: self._on_quit()),
            ]
        )
        return pystray.Menu(*items)

    def _transcript_bar_label(self, _item: pystray.MenuItem) -> str:
        from ui.transcript_bar import is_showing

        return "Hide Live Transcript" if is_showing() else "Show Live Transcript"

    def _pause_label(self, _item: pystray.MenuItem) -> str:
        if self._state.is_dictating():
            return "Stop Dictation"
        return "Resume" if self._state.is_paused() else "Pause"

    def _toggle_pause(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
        # While dictating, this item is the hard-stop for the dictation
        # loop (matching the pause hotkey's behavior), not a pause toggle.
        if self._state.is_dictating():
            self._state.request_stop_dictation()
            logger.info("Dictation stop requested via tray menu")
            return
        paused = self._state.toggle_pause()
        self._state.set_mode("paused" if paused else "idle")
        logger.info("MIMIR %s via tray menu", "paused" if paused else "resumed")

    def _poll_state(self) -> None:
        last_mode = None
        while not self._stop_event.is_set():
            mode = "paused" if self._state.is_paused() else self._state.get_mode()
            if mode != last_mode:
                color = _COLORS.get(mode, _COLORS["idle"])
                self._icon.icon = _make_icon_image(color)
                self._icon.menu = self._build_menu()
                last_mode = mode
            self._stop_event.wait(_POLL_INTERVAL_SEC)

    def run(self) -> None:
        """Run the tray icon on the calling (main) thread. Blocks until stopped."""
        self._poll_thread = threading.Thread(target=self._poll_state, name="tray-poll", daemon=True)
        self._poll_thread.start()

        self._icon.run()

    def stop(self) -> None:
        """Stop the tray icon and polling thread."""
        self._stop_event.set()
        self._icon.stop()


if __name__ == "__main__":
    _state = AppState()
    tray = TrayIcon(
        _state,
        on_open_transcript=lambda: print("open transcript"),
        on_open_settings=lambda: print("open settings"),
        on_listen_now=lambda: print("listen now"),
        on_quit=lambda: tray.stop(),
    )
    tray.run()
