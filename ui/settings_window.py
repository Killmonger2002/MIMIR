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
from ui.voice_training_window import open_voice_training_window
from ui.wake_word_training_window import open_wake_word_training_window

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
    window.geometry("400x460")
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

    from core.voice_profile import is_enrolled

    voice_status_text = "Voice profile: enrolled" if is_enrolled() else "Voice profile: not enrolled"
    tk.Label(window, text=voice_status_text, fg="gray").grid(
        row=7, column=0, sticky="w", padx=8, pady=(12, 4)
    )
    voice_button_text = "Re-train Voice" if is_enrolled() else "Enroll Voice"
    tk.Button(window, text=voice_button_text, command=open_voice_training_window).grid(
        row=7, column=1, sticky="e", padx=8, pady=(12, 4)
    )

    import os

    from training.train_wake_word import OUTPUT_MODEL_PATH

    from core.llm_runtime import TIER_LABELS, get_active_tier, set_active_tier

    tier_choices = {f"{n} - {label.capitalize()}": n for n, label in TIER_LABELS.items()}
    current_label = next(lbl for lbl, n in tier_choices.items() if n == get_active_tier())
    tier_var = tk.StringVar(value=current_label)

    def _on_tier_selected(choice: str) -> None:
        # Applies immediately and is deliberately NOT saved to config -
        # MIMIR always restarts on the basic (least-resource) model; the
        # user escalates per session, by voice or here.
        set_active_tier(tier_choices[choice])

    tk.Label(window, text="Model (this session)").grid(row=8, column=0, sticky="w", padx=8, pady=4)
    tier_menu = tk.OptionMenu(window, tier_var, *tier_choices.keys(), command=_on_tier_selected)
    tier_menu.grid(row=8, column=1, sticky="ew", padx=8, pady=4)

    wake_word_trained = os.path.exists(OUTPUT_MODEL_PATH)
    wake_word_active = wake_word_trained and str(OUTPUT_MODEL_PATH).replace("\\", "/") == str(
        config.wake_word.model_path
    ).replace("\\", "/")
    if wake_word_active:
        wake_status_text = "Custom wake word: active"
    elif wake_word_trained:
        wake_status_text = "Custom wake word: trained, not active"
    else:
        wake_status_text = "Custom wake word: not trained"
    tk.Label(window, text=wake_status_text, fg="gray").grid(row=9, column=0, sticky="w", padx=8, pady=(4, 4))
    tk.Button(
        window,
        text="Re-train Wake Word" if wake_word_trained else "Train Custom Wake Word",
        command=open_wake_word_training_window,
    ).grid(row=9, column=1, sticky="e", padx=8, pady=(4, 4))

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
    save_button.grid(row=10, column=0, columnspan=2, pady=12)

    _window = window
    show_window(window)


if __name__ == "__main__":
    import time

    open_settings_window()
    time.sleep(5)
