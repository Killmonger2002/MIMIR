"""Live transcript bar: a docked strip at the top of the screen showing
the conversation line by line as it happens, plus quick controls
(Settings, Listen Now, mic pause, input level, input device). Off by
default (config.ui.transcript_bar_enabled) - toggled from the tray menu
or Settings, and the choice persists across restarts.

Two things distinguish this from ui/transcript_window.py (the full,
on-demand scrollable history):

- It shows EVERY spoken line as it happens (state.get_captions()), not
  just one entry per finished command - see state.AppState.add_caption()
  for why add_log_entry() alone can't drive a live-captions view (it only
  fires once a whole command cycle resolves, silently dropping the
  listening cue, confirmation questions, and the user's yes/no replies).
- It's a real Windows app-bar (the same SHAppBarMessage mechanism the
  taskbar uses), not just an always-on-top floating window: it reserves
  its own strip of screen height so maximized windows and other apps
  leave room for it, instead of a window being able to render underneath
  it. Falls back to a plain always-on-top window if app-bar registration
  fails for any reason (e.g. a Windows version quirk) rather than not
  showing at all.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import queue
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

import numpy as np

from core.config_writer import patch_config
from state import AppState
from ui import theme
from ui.ui_root import get_root, run_on_ui_thread

logger = logging.getLogger("mimir.transcript_bar")

_window: tk.Toplevel | None = None
_state: AppState | None = None
_on_listen_now: Callable[[], None] | None = None

_last_caption_count = 0
_refresh_running = False
_REFRESH_INTERVAL_MS = 350

# ~half the standard 48px Windows taskbar height - a single compact row,
# not the earlier 3-row (header/captions/toolbar) layout. Requested live,
# 2026-07-17: "the top bar must be very thin, like half the size of the
# task bar." Everything below is sized to fit this in one row: a single
# most-recent caption line (not a scrollback feed - state.get_captions()
# still holds full history for the Transcript window), and icon-only
# buttons instead of labeled ones.
_BAR_HEIGHT = 26
_COMPACT_FONT = ("Segoe UI", 8)
_COMPACT_FONT_BOLD = ("Segoe UI", 8, "bold")
_CAPTION_MAX_CHARS = 160

_METER_BLOCK_SEC = 0.15
_LEVEL_GAIN = 350.0  # same heuristic as ui/audio_calibration_window.py

_MODE_LABELS = {
    "idle": ("Idle", theme.FG_MUTED),
    "listening": ("Listening…", theme.ACCENT_GREEN),
    "thinking": ("Thinking…", theme.ACCENT_BLUE),
    "speaking": ("Speaking…", "#b57bd6"),
    "paused": ("Paused", theme.FG_MUTED),
    "shutting_down": ("Shutting down…", theme.ACCENT_RED),
    "dictating": ("Dictating…", theme.ACCENT_ORANGE),
}

# ---- Windows app-bar (SHAppBarMessage) ------------------------------------

_ABM_NEW = 0x00000000
_ABM_REMOVE = 0x00000001
_ABM_QUERYPOS = 0x00000002
_ABM_SETPOS = 0x00000003
_ABE_TOP = 1
_SM_CXSCREEN = 0


class _APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


_appbar_data: _APPBARDATA | None = None


def _get_hwnd(window: tk.Toplevel) -> int:
    # Same pattern as ui_overlay.py's click-through setup: an
    # overrideredirect Tk window's winfo_id() is an internal child, the
    # real top-level frame hwnd is its parent.
    return ctypes.windll.user32.GetParent(window.winfo_id())


def _dock_as_appbar(hwnd: int, height_px: int) -> tuple[int, int, int, int] | None:
    """Register hwnd as a top-docked Windows app-bar reserving
    `height_px` of screen height, the same mechanism the taskbar itself
    uses - maximized windows/other apps' work area shrinks to leave room
    for it. Returns the granted (left, top, right, bottom) rect, or None
    on failure (caller falls back to a plain always-on-top window)."""
    global _appbar_data
    try:
        shell32 = ctypes.windll.shell32
        shell32.SHAppBarMessage.restype = ctypes.c_size_t
        shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(_APPBARDATA)]
        user32 = ctypes.windll.user32

        abd = _APPBARDATA()
        abd.cbSize = ctypes.sizeof(_APPBARDATA)
        abd.hWnd = hwnd
        abd.uCallbackMessage = 0

        if not shell32.SHAppBarMessage(_ABM_NEW, ctypes.byref(abd)):
            logger.warning("SHAppBarMessage(ABM_NEW) failed - falling back to a floating window")
            return None

        screen_w = user32.GetSystemMetrics(_SM_CXSCREEN)
        abd.uEdge = _ABE_TOP
        abd.rc.left = 0
        abd.rc.top = 0
        abd.rc.right = screen_w
        abd.rc.bottom = height_px

        shell32.SHAppBarMessage(_ABM_QUERYPOS, ctypes.byref(abd))
        # Per MSDN: for a TOP/BOTTOM edge, only top/bottom may have been
        # adjusted (to avoid another top-docked app-bar) - recompute
        # bottom from the (possibly adjusted) top rather than trusting
        # the query's own bottom value.
        abd.rc.bottom = abd.rc.top + height_px

        shell32.SHAppBarMessage(_ABM_SETPOS, ctypes.byref(abd))

        _appbar_data = abd
        return abd.rc.left, abd.rc.top, abd.rc.right, abd.rc.bottom
    except Exception:
        logger.exception("App-bar docking failed")
        return None


def _undock_appbar() -> None:
    global _appbar_data
    if _appbar_data is None:
        return
    try:
        shell32 = ctypes.windll.shell32
        shell32.SHAppBarMessage.restype = ctypes.c_size_t
        shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(_APPBARDATA)]
        shell32.SHAppBarMessage(_ABM_REMOVE, ctypes.byref(_appbar_data))
    except Exception:
        logger.debug("Failed to remove app-bar registration", exc_info=True)
    _appbar_data = None


def _keep_topmost() -> None:
    """Re-assert -topmost on a timer, belt-and-suspenders alongside the
    app-bar reservation - docking keeps other windows from *maximizing*
    into this strip, but doesn't stop some other explicitly-topmost
    window from being dragged over it. Replaces ui_root.show_window()'s
    one-shot-then-clear-topmost behavior (right for dialogs, wrong for a
    HUD that must never be covered - observed live, 2026-07-17)."""
    if _window is None or not _window.winfo_exists():
        return
    try:
        _window.attributes("-topmost", True)
        _window.lift()
    except Exception:
        pass
    _window.after(2000, _keep_topmost)


# ---- public API -------------------------------------------------------------


def configure(on_listen_now: Callable[[], None] | None = None) -> None:
    """Set the callback the bar's "Listen Now" button invokes. Call once
    at startup (main.py), before the bar is ever shown."""
    global _on_listen_now
    _on_listen_now = on_listen_now


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


# ---- window construction -----------------------------------------------------


def _open(state: AppState) -> None:
    global _window, _state, _refresh_running, _last_caption_count

    _state = state

    if _window is not None and _window.winfo_exists():
        return

    root = get_root()
    window = tk.Toplevel(root)
    window.title("MIMIR")
    window.overrideredirect(True)
    window.attributes("-topmost", True)
    theme.apply_window_theme(window)

    outer = tk.Frame(window, bg=theme.BG_CARD, highlightbackground=theme.BORDER, highlightthickness=1)
    outer.pack(fill=tk.BOTH, expand=True)

    # Everything in ONE row - there's no vertical room for the earlier
    # 3-row (header/captions/toolbar) layout at this height.
    row = tk.Frame(outer, bg=theme.BG_CARD)
    row.pack(fill=tk.BOTH, expand=True, padx=6, pady=1)

    close_btn = tk.Label(
        row, text="✕", font=_COMPACT_FONT, bg=theme.BG_CARD, fg=theme.FG_MUTED, cursor="hand2"
    )
    close_btn.pack(side=tk.RIGHT)
    close_btn.bind("<Button-1>", lambda _e: set_enabled(state, False))

    from core.audio_device import current_device_choice_label, list_device_choices

    device_var = tk.StringVar(value=current_device_choice_label())
    device_btn = tk.Menubutton(
        row, text="🎧", bg=theme.BG_INSET, fg=theme.FG_TEXT, activebackground=theme.BORDER,
        activeforeground=theme.FG_TEXT, relief="flat", bd=0, padx=4, pady=0, font=_COMPACT_FONT,
        cursor="hand2", highlightthickness=0,
    )
    device_menu_widget = tk.Menu(
        device_btn, tearoff=False, bg=theme.BG_INSET, fg=theme.FG_TEXT,
        activebackground=theme.ACCENT_BLUE, activeforeground="#ffffff", font=theme.FONT_BODY,
    )
    for choice in list_device_choices():
        device_menu_widget.add_radiobutton(
            label=choice, variable=device_var, value=choice,
            command=lambda c=choice: _on_device_selected(c),
        )
    device_btn.configure(menu=device_menu_widget)
    device_btn.pack(side=tk.RIGHT, padx=(4, 4))

    theme.ensure_thin_progressbar_style()
    level_bar = ttk.Progressbar(
        row, style="MimirThin.Horizontal.TProgressbar", orient="horizontal", mode="determinate",
        maximum=100, length=50,
    )
    level_bar.pack(side=tk.RIGHT, padx=(4, 4))

    mic_btn = _icon_button(row, "🎤", _on_mic_toggle)
    mic_btn.pack(side=tk.RIGHT, padx=(2, 0))

    def _open_settings() -> None:
        from ui.settings_window import open_settings_window

        open_settings_window(state)

    _icon_button(row, "⚙", _open_settings).pack(side=tk.RIGHT, padx=(2, 0))

    def _listen_now() -> None:
        if _on_listen_now is not None:
            _on_listen_now()

    _icon_button(row, "▶", _listen_now).pack(side=tk.RIGHT, padx=(2, 0))

    mode_label = tk.Label(row, text="Idle", font=_COMPACT_FONT_BOLD, bg=theme.BG_CARD, fg=theme.FG_MUTED)
    mode_label.pack(side=tk.LEFT, padx=(2, 6))

    caption_label = tk.Label(
        row, text="Say the wake word to begin.", font=_COMPACT_FONT, bg=theme.BG_CARD, fg=theme.FG_MUTED,
        anchor="w", justify="left",
    )
    caption_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

    window._mode_label = mode_label  # type: ignore[attr-defined]
    window._caption_label = caption_label  # type: ignore[attr-defined]
    window._mic_btn = mic_btn  # type: ignore[attr-defined]
    window._device_var = device_var  # type: ignore[attr-defined]
    window._level_bar = level_bar  # type: ignore[attr-defined]

    window.update_idletasks()
    hwnd = _get_hwnd(window)
    rect = _dock_as_appbar(hwnd, _BAR_HEIGHT)
    if rect is not None:
        left, top, right, bottom = rect
        window.geometry(f"{right - left}x{bottom - top}+{left}+{top}")
    else:
        screen_w = window.winfo_screenwidth()
        window.geometry(f"{screen_w}x{_BAR_HEIGHT}+0+0")

    window.deiconify()
    window.lift()
    window.attributes("-topmost", True)

    _window = window
    _last_caption_count = 0
    _update_mic_button()

    _keep_topmost()

    if not _refresh_running:
        _refresh_running = True
        window.after(_REFRESH_INTERVAL_MS, _schedule_refresh)

    _start_meter()


def _close() -> None:
    global _window
    _stop_meter()
    _undock_appbar()
    if _window is not None and _window.winfo_exists():
        _window.destroy()
    _window = None


# ---- live caption + mode polling --------------------------------------------


def _schedule_refresh() -> None:
    global _refresh_running, _last_caption_count

    if _window is None or not _window.winfo_exists() or _state is None:
        _refresh_running = False
        return

    mode = "paused" if _state.is_paused() else _state.get_mode()
    label_text, color = _MODE_LABELS.get(mode, ("Idle", theme.FG_MUTED))
    _window._mode_label.configure(text=label_text, fg=color)  # type: ignore[attr-defined]
    _update_mic_button()

    current_count = _state.caption_count()
    if current_count != _last_caption_count:
        _last_caption_count = current_count
        latest = _state.get_captions(limit=1)
        if latest:
            _show_caption(latest[0])

    _window.after(_REFRESH_INTERVAL_MS, _schedule_refresh)


def _show_caption(entry: dict) -> None:
    """Show the single most recent caption line - there's no vertical
    room in a ~half-taskbar-height bar for a scrolling multi-line feed
    (full history still lives in state.get_captions() and the Transcript
    window). Each new turn replaces the previous one as it happens, so
    the conversation still updates live, one line at a time."""
    label: tk.Label = _window._caption_label  # type: ignore[attr-defined]
    speaker_label = "You" if entry["speaker"] == "you" else "MIMIR"
    color = theme.ACCENT_BLUE if entry["speaker"] == "you" else theme.ACCENT_GREEN
    text = f"{speaker_label}: {entry['text']}"
    if len(text) > _CAPTION_MAX_CHARS:
        text = text[: _CAPTION_MAX_CHARS - 1] + "…"
    label.configure(text=text, fg=color)


# ---- toolbar actions ----------------------------------------------------------


def _icon_button(parent: tk.Misc, text: str, command) -> tk.Button:
    return tk.Button(
        parent, text=text, command=command, bg=theme.BG_INSET, fg=theme.FG_TEXT,
        activebackground=theme.BORDER, activeforeground=theme.FG_TEXT, relief="flat", bd=0,
        padx=5, pady=0, width=2, font=_COMPACT_FONT, cursor="hand2", highlightthickness=0,
    )


def _on_mic_toggle() -> None:
    if _state is None:
        return
    paused = _state.toggle_pause()
    _state.set_mode("paused" if paused else "idle")
    _update_mic_button()


def _update_mic_button() -> None:
    if _window is None or _state is None:
        return
    btn = getattr(_window, "_mic_btn", None)
    if btn is None:
        return
    # No room for a "Pause"/"Resume" label at this height - color-code
    # the same mic glyph instead (red tint = paused/not listening).
    if _state.is_paused():
        btn.configure(fg=theme.ACCENT_RED)
    else:
        btn.configure(fg=theme.FG_TEXT)


def _on_device_selected(choice: str) -> None:
    from core.audio_device import reset_cache

    value = "" if choice.startswith("System default") else choice
    patch_config({"audio": {"input_device_name": value}})
    reset_cache()


# ---- input level meter (idle-only, to never contend with real STT capture) --


def _start_meter() -> None:
    stop_event = threading.Event()
    _window._meter_stop = stop_event  # type: ignore[attr-defined]
    q: queue.Queue = queue.Queue(maxsize=4)
    _window._meter_queue = q  # type: ignore[attr-defined]
    threading.Thread(target=_meter_worker, args=(stop_event, q), name="transcript-bar-meter", daemon=True).start()
    _poll_meter(stop_event, q)


def _stop_meter() -> None:
    if _window is not None:
        stop_event = getattr(_window, "_meter_stop", None)
        if stop_event is not None:
            stop_event.set()


def _meter_worker(stop_event: threading.Event, out_queue: queue.Queue) -> None:
    """Samples input level only while MIMIR is idle - closes its stream
    the instant a real command cycle needs the microphone, so this never
    competes with core/stt.py's own capture (observed live earlier this
    project: some devices - e.g. a Bluetooth headset resolving to a
    WDM-KS-only endpoint - don't tolerate concurrent blocking streams at
    all)."""
    import sounddevice as sd

    from config import config
    from core.audio_device import get_input_device

    sample_rate = config.stt.sample_rate
    block_size = int(sample_rate * _METER_BLOCK_SEC)

    while not stop_event.is_set():
        if _state is None or _state.get_mode() != "idle":
            stop_event.wait(0.3)
            continue
        try:
            device_index = get_input_device()
            with sd.InputStream(
                samplerate=sample_rate, channels=1, dtype="int16", blocksize=block_size, device=device_index
            ) as stream:
                while not stop_event.is_set() and _state.get_mode() == "idle":
                    block, _overflowed = stream.read(block_size)
                    samples = block.flatten().astype(np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
                    level_pct = min(100.0, rms * _LEVEL_GAIN)
                    try:
                        out_queue.put_nowait(level_pct)
                    except queue.Full:
                        pass
        except Exception:
            logger.debug("Transcript bar level meter stream failed", exc_info=True)
            stop_event.wait(1.0)


def _poll_meter(owning_stop_event: threading.Event, q: queue.Queue) -> None:
    if owning_stop_event.is_set() or _window is None or not _window.winfo_exists():
        return
    try:
        while True:
            level_pct = q.get_nowait()
            _window._level_bar["value"] = level_pct  # type: ignore[attr-defined]
    except queue.Empty:
        pass
    except Exception:
        pass
    if _state is not None and _state.get_mode() != "idle":
        _window._level_bar["value"] = 0  # type: ignore[attr-defined]
    _window.after(150, lambda: _poll_meter(owning_stop_event, q))


if __name__ == "__main__":
    import time

    _demo_state = AppState()
    configure(on_listen_now=lambda: print("[demo] Listen Now clicked"))
    show_transcript_bar(_demo_state)
    time.sleep(2)
    _demo_state.add_caption("you", "show numbers")
    time.sleep(1)
    _demo_state.add_caption("mimir", "Showing 80 numbers. Say a number to click it.")
    time.sleep(2)
    _demo_state.set_mode("listening")
    time.sleep(2)
    _demo_state.add_caption("you", "click on 9")
    _demo_state.set_mode("thinking")
    time.sleep(1)
    _demo_state.add_caption("mimir", "Clicked Edit")
    _demo_state.set_mode("idle")
    time.sleep(10)
