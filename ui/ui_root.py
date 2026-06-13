"""Shared Tkinter root running on a single dedicated UI thread.

Tkinter is not thread-safe: all widget creation and updates must happen on
the thread running `mainloop()`. MIMIR's tray menu callbacks fire on the
tray icon's own thread, so windows must be scheduled onto this UI thread
rather than created directly.
"""

from __future__ import annotations

import threading
import tkinter as tk
from typing import Callable

_root: tk.Tk | None = None
_ready = threading.Event()


def _run_root() -> None:
    global _root
    _root = tk.Tk()
    _root.withdraw()
    _ready.set()
    _root.mainloop()


def get_root() -> tk.Tk:
    """Return the shared hidden Tk root, starting its thread on first use."""
    if _root is None and not _ready.is_set():
        thread = threading.Thread(target=_run_root, name="ui-thread", daemon=True)
        thread.start()
        _ready.wait()
    return _root


def run_on_ui_thread(func: Callable[[], None]) -> None:
    """Schedule `func` to run on the UI thread as soon as possible."""
    root = get_root()
    root.after(0, func)


def show_window(window: tk.Toplevel) -> None:
    """Bring a window to the front and give it focus."""
    window.deiconify()
    window.lift()
    window.attributes("-topmost", True)
    window.after(200, lambda: window.attributes("-topmost", False))
    window.focus_force()
