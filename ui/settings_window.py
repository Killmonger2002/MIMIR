"""Settings editor for config.yaml.

NOTE: Hotkey changes require a restart to take effect - re-registering
global hotkeys live is out of scope for Phase 1.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

import yaml

from config import config
from ui.ui_root import get_root, run_on_ui_thread, show_window

_CONFIG_PATH = "config.yaml"

_window: tk.Toplevel | None = None


def open_settings_window() -> None:
    """Open (or focus) the settings window. Safe to call from any thread."""
    run_on_ui_thread(_open)


def _open() -> None:
    global _window

    if _window is not None and _window.winfo_exists():
        show_window(_window)
        return

    window = tk.Toplevel(get_root())
    window.title("MIMIR - Settings")
    window.geometry("400x320")
    window.protocol("WM_DELETE_WINDOW", window.withdraw)

    fields: dict[str, tk.Variable] = {}

    def _add_row(row: int, label: str, var: tk.Variable, widget_factory) -> None:
        tk.Label(window, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=4)
        widget = widget_factory(window, var)
        widget.grid(row=row, column=1, sticky="ew", padx=8, pady=4)

    window.columnconfigure(1, weight=1)

    wake_word_var = tk.StringVar(value=config.wake_word.phrase)
    fields["wake_word_phrase"] = wake_word_var
    _add_row(0, "Wake word phrase", wake_word_var, lambda w, v: tk.Entry(w, textvariable=v))

    tts_speed_var = tk.DoubleVar(value=config.tts.speed)
    fields["tts_speed"] = tts_speed_var
    _add_row(
        1,
        "TTS voice speed",
        tts_speed_var,
        lambda w, v: tk.Scale(w, from_=0.5, to=2.0, resolution=0.1, orient=tk.HORIZONTAL, variable=v),
    )

    idle_unload_var = tk.IntVar(value=config.lifecycle.idle_unload_minutes)
    fields["idle_unload_minutes"] = idle_unload_var
    _add_row(2, "Idle unload (minutes)", idle_unload_var, lambda w, v: tk.Spinbox(w, from_=1, to=60, textvariable=v))

    volume_step_var = tk.IntVar(value=config.volume.volume_step_percent)
    fields["volume_step_percent"] = volume_step_var
    _add_row(3, "Volume step (%)", volume_step_var, lambda w, v: tk.Spinbox(w, from_=1, to=50, textvariable=v))

    pause_hotkey_var = tk.StringVar(value=config.hotkeys.pause_resume)
    fields["pause_resume_hotkey"] = pause_hotkey_var
    _add_row(4, "Pause/Resume hotkey", pause_hotkey_var, lambda w, v: tk.Entry(w, textvariable=v))

    quit_hotkey_var = tk.StringVar(value=config.hotkeys.quit)
    fields["quit_hotkey"] = quit_hotkey_var
    _add_row(5, "Quit hotkey", quit_hotkey_var, lambda w, v: tk.Entry(w, textvariable=v))

    note = tk.Label(
        window,
        text="Note: hotkey changes require restarting MIMIR to take effect.",
        wraplength=380,
        fg="gray",
        justify="left",
    )
    note.grid(row=6, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 0))

    def _save() -> None:
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            data.setdefault("wake_word", {})["phrase"] = wake_word_var.get()
            data.setdefault("tts", {})["speed"] = tts_speed_var.get()
            data.setdefault("lifecycle", {})["idle_unload_minutes"] = idle_unload_var.get()
            data.setdefault("volume", {})["volume_step_percent"] = volume_step_var.get()
            data.setdefault("hotkeys", {})["pause_resume"] = pause_hotkey_var.get()
            data.setdefault("hotkeys", {})["quit"] = quit_hotkey_var.get()

            with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False)

            config.reload(_CONFIG_PATH)
            messagebox.showinfo("MIMIR Settings", "Settings saved. Restart MIMIR for hotkey changes to apply.")
        except Exception as exc:
            messagebox.showerror("MIMIR Settings", f"Failed to save settings: {exc}")

    save_button = tk.Button(window, text="Save", command=_save)
    save_button.grid(row=7, column=0, columnspan=2, pady=12)

    _window = window
    show_window(window)


if __name__ == "__main__":
    import time

    open_settings_window()
    time.sleep(5)
