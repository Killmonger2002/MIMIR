"""Small always-on-top live transcript bar, docked near the top of the
screen. Off by default (config.ui.transcript_bar_enabled) - toggled from
the tray menu or Settings, and the choice persists across restarts.

Unlike ui/transcript_window.py (a full scrollable history log you open
on demand), this shows only the latest turn, replacing it as the
conversation progresses, plus a live status word (Listening/Thinking/
Speaking) - a HUD, not a log.
"""

from __future__ import annotations

import tkinter as tk

from core.config_writer import patch_config
from state import AppState
from ui import theme
from ui.ui_root import get_root, run_on_ui_thread, show_window

_window: tk.Toplevel | None = None
_state: AppState | None = None
_last_count = 0
_refresh_running = False

_REFRESH_INTERVAL_MS = 400
_BAR_WIDTH = 560

_MODE_LABELS = {
    "idle": ("Idle", theme.FG_MUTED),
    "listening": ("Listening…", theme.ACCENT_GREEN),
    "thinking": ("Thinking…", theme.ACCENT_BLUE),
    "speaking": ("Speaking…", "#b57bd6"),
    "paused": ("Paused", theme.FG_MUTED),
    "shutting_down": ("Shutting down…", theme.ACCENT_RED),
}


def is_showing() -> bool:
    return _window is not None


def show_transcript_bar(state: AppState) -> None:
    """Show (or focus) the transcript bar. Safe to call from any thread."""
    run_on_ui_thread(lambda: _open(state))


def hide_transcript_bar() -> None:
    """Hide the transcript bar. Safe to call from any thread."""
    run_on_ui_thread(_close)


def set_enabled(state: AppState, enabled: bool, persist: bool = True) -> None:
    """Show or hide the bar and (by default) persist the choice to
    config.yaml so it's remembered across restarts."""
    if persist:
        patch_config({"ui": {"transcript_bar_enabled": enabled}})
    if enabled:
        show_transcript_bar(state)
    else:
        hide_transcript_bar()


def _open(state: AppState) -> None:
    global _window, _state, _refresh_running

    _state = state

    if _window is not None and _window.winfo_exists():
        show_window(_window)
        return

    root = get_root()
    window = tk.Toplevel(root)
    window.title("MIMIR")
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    theme.apply_window_theme(window)

    outer = theme.card(window)
    outer.pack(fill=tk.BOTH, expand=True)

    header = tk.Frame(outer, bg=theme.BG_CARD)
    header.pack(fill=tk.X, padx=10, pady=(6, 0))
    tk.Label(header, text="⚙  MIMIR", font=theme.FONT_SMALL_BOLD, bg=theme.BG_CARD, fg=theme.FG_MUTED).pack(
        side=tk.LEFT
    )
    mode_label = tk.Label(header, text="Idle", font=theme.FONT_SMALL_BOLD, bg=theme.BG_CARD, fg=theme.FG_MUTED)
    mode_label.pack(side=tk.RIGHT)
    close_btn = tk.Label(header, text="✕", font=theme.FONT_SMALL, bg=theme.BG_CARD, fg=theme.FG_MUTED, cursor="hand2")
    close_btn.pack(side=tk.RIGHT, padx=(0, 10))
    close_btn.bind("<Button-1>", lambda _e: set_enabled(state, False))

    body = tk.Label(
        outer,
        text="Say the wake word to begin.",
        font=theme.FONT_BODY,
        bg=theme.BG_CARD,
        fg=theme.FG_TEXT,
        anchor="w",
        justify="left",
        wraplength=_BAR_WIDTH - 24,
    )
    body.pack(fill=tk.X, padx=10, pady=(2, 8))

    window.update_idletasks()
    screen_width = window.winfo_screenwidth()
    x = (screen_width - _BAR_WIDTH) // 2
    window.geometry(f"{_BAR_WIDTH}x64+{x}+12")

    window._mode_label = mode_label  # type: ignore[attr-defined]
    window._body_label = body  # type: ignore[attr-defined]

    _window = window
    show_window(window)

    if not _refresh_running:
        _refresh_running = True
        window.after(_REFRESH_INTERVAL_MS, _schedule_refresh)


def _close() -> None:
    global _window
    if _window is not None and _window.winfo_exists():
        _window.destroy()
    _window = None


def _schedule_refresh() -> None:
    global _refresh_running, _last_count

    if _window is None or not _window.winfo_exists() or _state is None:
        _refresh_running = False
        return

    mode = "paused" if _state.is_paused() else _state.get_mode()
    label_text, color = _MODE_LABELS.get(mode, ("Idle", theme.FG_MUTED))
    _window._mode_label.configure(text=label_text, fg=color)  # type: ignore[attr-defined]

    entries = _state.get_log(limit=1)
    current_count = len(_state.get_log(limit=0))
    if current_count != _last_count and entries:
        _last_count = current_count
        latest = entries[-1]
        _window._body_label.configure(  # type: ignore[attr-defined]
            text=f"You: {latest['transcript']}\nMIMIR: {latest['response']}"
        )

    _window.after(_REFRESH_INTERVAL_MS, _schedule_refresh)


if __name__ == "__main__":
    import time

    _demo_state = AppState()
    show_transcript_bar(_demo_state)
    time.sleep(2)
    _demo_state.add_log_entry("open chrome", "Opening chrome", "app_executor")
    time.sleep(3)
    _demo_state.set_mode("listening")
    time.sleep(3)
    _demo_state.add_log_entry("volume 40", "Volume set to 40 percent", "volume_executor")
    time.sleep(5)
