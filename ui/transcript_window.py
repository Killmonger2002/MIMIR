"""Read-only transcript viewer showing recent conversation history."""

from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext

from state import AppState
from ui.ui_root import get_root, run_on_ui_thread, show_window

_window: tk.Toplevel | None = None
_text_widget: scrolledtext.ScrolledText | None = None
_last_count = 0
_refresh_running = False

_REFRESH_INTERVAL_MS = 750


def open_transcript_window(state: AppState) -> None:
    """Open (or focus) the transcript window. Safe to call from any thread."""
    run_on_ui_thread(lambda: _open(state))


def _open(state: AppState) -> None:
    global _window, _text_widget, _refresh_running

    if _window is not None and _window.winfo_exists():
        _populate(state)
        show_window(_window)
        return

    window = tk.Toplevel(get_root())
    window.title("MIMIR - Transcript")
    window.geometry("600x400")
    window.protocol("WM_DELETE_WINDOW", window.withdraw)

    text_widget = scrolledtext.ScrolledText(window, wrap=tk.WORD, state="disabled")
    text_widget.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    _window = window
    _text_widget = text_widget

    _populate(state)
    show_window(window)

    if not _refresh_running:
        _refresh_running = True
        window.after(_REFRESH_INTERVAL_MS, lambda: _schedule_refresh(state))


def _schedule_refresh(state: AppState) -> None:
    global _refresh_running

    if _window is None or not _window.winfo_exists():
        _refresh_running = False
        return

    current_count = len(state.get_log(limit=0))
    if current_count != _last_count:
        _populate(state)

    _window.after(_REFRESH_INTERVAL_MS, lambda: _schedule_refresh(state))


def _populate(state: AppState) -> None:
    global _last_count

    if _text_widget is None:
        return

    entries = state.get_log(limit=50)
    _last_count = len(state.get_log(limit=0))

    _text_widget.configure(state="normal")
    _text_widget.delete("1.0", tk.END)
    for entry in entries:
        _text_widget.insert(
            tk.END,
            f"[{entry['timestamp']}] ({entry['executor']})\n"
            f"  You: {entry['transcript']}\n"
            f"  MIMIR: {entry['response']}\n\n",
        )
    _text_widget.configure(state="disabled")
    _text_widget.see(tk.END)


if __name__ == "__main__":
    import time

    _state = AppState()
    _state.add_log_entry("open chrome", "Opening chrome", "app_executor")
    _state.add_log_entry("volume 40", "Volume set to 40 percent", "volume_executor")
    open_transcript_window(_state)
    time.sleep(5)
