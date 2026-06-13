"""Read-only transcript viewer showing recent conversation history."""

from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext

from state import AppState
from ui.ui_root import get_root, run_on_ui_thread, show_window

_window: tk.Toplevel | None = None
_text_widget: scrolledtext.ScrolledText | None = None


def open_transcript_window(state: AppState) -> None:
    """Open (or focus) the transcript window. Safe to call from any thread."""
    run_on_ui_thread(lambda: _open(state))


def _open(state: AppState) -> None:
    global _window, _text_widget

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

    button_frame = tk.Frame(window)
    button_frame.pack(fill=tk.X, padx=8, pady=(0, 8))

    refresh_button = tk.Button(button_frame, text="Refresh", command=lambda: _populate(state))
    refresh_button.pack(side=tk.RIGHT)

    _window = window
    _text_widget = text_widget

    _populate(state)
    show_window(window)


def _populate(state: AppState) -> None:
    if _text_widget is None:
        return

    entries = state.get_log(limit=50)
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


if __name__ == "__main__":
    import time

    _state = AppState()
    _state.add_log_entry("open chrome", "Opening chrome", "app_executor")
    _state.add_log_entry("volume 40", "Volume set to 40 percent", "volume_executor")
    open_transcript_window(_state)
    time.sleep(5)
