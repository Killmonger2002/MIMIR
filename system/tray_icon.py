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
        on_quit: Callable[[], None],
    ) -> None:
        self._state = state
        self._on_open_transcript = on_open_transcript
        self._on_open_settings = on_open_settings
        self._on_quit = on_quit
        self._icon = pystray.Icon(
            "MIMIR",
            icon=_make_icon_image(_COLORS["idle"]),
            title="MIMIR",
            menu=self._build_menu(),
        )
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Open Transcript", lambda: self._on_open_transcript(), default=True
            ),
            pystray.MenuItem("Settings", lambda: self._on_open_settings()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(self._pause_label, self._toggle_pause),
            pystray.MenuItem("Quit MIMIR", lambda: self._on_quit()),
        )

    def _pause_label(self, _item: pystray.MenuItem) -> str:
        return "Resume" if self._state.is_paused() else "Pause"

    def _toggle_pause(self, _icon: pystray.Icon, _item: pystray.MenuItem) -> None:
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
        on_quit=lambda: tray.stop(),
    )
    tray.run()
