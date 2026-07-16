"""Settings editor for config.yaml.

NOTE: Hotkey changes require a restart to take effect - re-registering
global hotkeys live is out of scope for Phase 1.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

from config import config
from core.config_writer import patch_config
from state import AppState
from ui import theme
from ui.audio_calibration_window import open_audio_calibration_window
from ui.ui_root import get_root, run_on_ui_thread, show_window

_CONFIG_PATH = "config.yaml"

_STT_MODEL_CHOICES = ["tiny.en", "base.en", "small.en"]

_window: tk.Toplevel | None = None


def open_settings_window(state: AppState | None = None) -> None:
    """Open (or focus) the settings window. Safe to call from any thread."""
    run_on_ui_thread(lambda: _open(state))


def _dark_entry(parent: tk.Misc, var: tk.Variable) -> tk.Entry:
    return tk.Entry(
        parent,
        textvariable=var,
        bg=theme.BG_INSET,
        fg=theme.FG_TEXT,
        insertbackground=theme.FG_TEXT,
        relief="flat",
        highlightthickness=1,
        highlightbackground=theme.BORDER,
        font=theme.FONT_BODY,
        width=18,
    )


def _dark_scale(parent: tk.Misc, var: tk.Variable, from_: float, to: float, resolution: float) -> tk.Scale:
    return tk.Scale(
        parent,
        from_=from_,
        to=to,
        resolution=resolution,
        orient=tk.HORIZONTAL,
        variable=var,
        bg=theme.BG_CARD,
        fg=theme.FG_TEXT,
        troughcolor=theme.BG_INSET,
        highlightthickness=0,
        activebackground=theme.ACCENT_BLUE,
        font=theme.FONT_SMALL,
        length=160,
    )


def _dark_spinbox(parent: tk.Misc, var: tk.Variable, from_: int, to: int) -> tk.Spinbox:
    return tk.Spinbox(
        parent,
        from_=from_,
        to=to,
        textvariable=var,
        bg=theme.BG_INSET,
        fg=theme.FG_TEXT,
        insertbackground=theme.FG_TEXT,
        buttonbackground=theme.BG_INSET,
        relief="flat",
        highlightthickness=1,
        highlightbackground=theme.BORDER,
        font=theme.FONT_BODY,
        width=8,
    )


def _field_row(parent: tk.Misc, label_text: str, widget_factory) -> tk.Widget:
    row = tk.Frame(parent, bg=theme.BG_CARD)
    row.pack(fill=tk.X, pady=5, padx=16)
    tk.Label(row, text=label_text, font=theme.FONT_BODY, bg=theme.BG_CARD, fg=theme.FG_TEXT).pack(side=tk.LEFT)
    widget = widget_factory(row)
    widget.pack(side=tk.RIGHT)
    return widget


def _divider(parent: tk.Misc) -> tk.Frame:
    line = tk.Frame(parent, bg=theme.BORDER, height=1)
    line.pack(fill=tk.X, pady=(14, 12))
    return line


def _open(state: AppState | None) -> None:
    global _window

    if _window is not None and _window.winfo_exists():
        show_window(_window)
        return

    window = tk.Toplevel(get_root())
    window.title("MIMIR - Settings")
    window.geometry("440x640")
    theme.apply_window_theme(window)
    window.protocol("WM_DELETE_WINDOW", window.withdraw)

    container = tk.Frame(window, bg=theme.BG_WINDOW)
    container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

    canvas = tk.Canvas(container, bg=theme.BG_WINDOW, highlightthickness=0)
    scrollbar = tk.Scrollbar(container, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    body = theme.card(canvas)
    body_window = canvas.create_window((0, 0), window=body, anchor="nw")

    def _on_configure(event: tk.Event) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig(body_window, width=event.width)

    canvas.bind("<Configure>", _on_configure)

    def _on_mousewheel(event: tk.Event) -> None:
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
    canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

    pad = dict(padx=16)

    header, _right = theme.title_row(body, "MIMIR — Settings", "")
    header.pack(fill=tk.X, pady=(14, 4), **pad)

    # ---- Audio ------------------------------------------------------------
    theme.section_heading(body, "Audio").pack(fill=tk.X, pady=(4, 2), **pad)

    device_choices = _device_choices()
    device_var = tk.StringVar(value=_device_choice_label())

    def _on_device_change(choice: str) -> None:
        value = "" if choice.startswith("System default") else choice
        patch_config({"audio": {"input_device_name": value}})
        from core.audio_device import reset_cache

        reset_cache()

    device_row = tk.Frame(body, bg=theme.BG_CARD)
    device_row.pack(fill=tk.X, pady=5, **pad)
    tk.Label(device_row, text="Input device", font=theme.FONT_BODY, bg=theme.BG_CARD, fg=theme.FG_TEXT).pack(
        side=tk.LEFT
    )
    theme.styled_option_menu(device_row, device_var, device_choices, command=_on_device_change).pack(side=tk.RIGHT)

    _divider(body).pack(fill=tk.X, **pad)

    # ---- Model selection ----------------------------------------------------
    theme.section_heading(body, "Model selection").pack(fill=tk.X, pady=(0, 2), **pad)
    theme.body_text(body, "Applies immediately for this session.").pack(fill=tk.X, pady=(0, 6), **pad)

    from core.llm_runtime import TIER_LABELS, get_active_tier, set_active_tier

    tier_choices = {f"{n} - {label.capitalize()}": n for n, label in TIER_LABELS.items()}
    current_tier_label = next(lbl for lbl, n in tier_choices.items() if n == get_active_tier())
    tier_var = tk.StringVar(value=current_tier_label)

    def _on_tier_selected(choice: str) -> None:
        # Deliberately NOT saved to config - MIMIR always restarts on the
        # basic (least-resource) model; the user escalates per session,
        # by voice or here.
        set_active_tier(tier_choices[choice])

    tier_row = tk.Frame(body, bg=theme.BG_CARD)
    tier_row.pack(fill=tk.X, pady=5, **pad)
    tk.Label(tier_row, text="LLM tier (this session)", font=theme.FONT_BODY, bg=theme.BG_CARD, fg=theme.FG_TEXT).pack(
        side=tk.LEFT
    )
    theme.styled_option_menu(tier_row, tier_var, list(tier_choices.keys()), command=_on_tier_selected).pack(
        side=tk.RIGHT
    )

    stt_var = tk.StringVar(value=config.stt.model_size)

    def _on_stt_model_selected(choice: str) -> None:
        patch_config({"stt": {"model_size": choice}})
        from core import stt as stt_module

        stt_module.unload()  # next transcribe() call reloads at the new size

    stt_row = tk.Frame(body, bg=theme.BG_CARD)
    stt_row.pack(fill=tk.X, pady=5, **pad)
    tk.Label(stt_row, text="Speech recognition model", font=theme.FONT_BODY, bg=theme.BG_CARD, fg=theme.FG_TEXT).pack(
        side=tk.LEFT
    )
    theme.styled_option_menu(stt_row, stt_var, _STT_MODEL_CHOICES, command=_on_stt_model_selected).pack(side=tk.RIGHT)

    _divider(body).pack(fill=tk.X, **pad)

    # ---- Voice & audio calibration -----------------------------------------
    theme.section_heading(body, "Voice & audio calibration").pack(fill=tk.X, pady=(0, 6), **pad)

    from core.voice_profile import is_enrolled

    voice_text = "Voice profile: enrolled" if is_enrolled() else "Voice profile: not enrolled"
    theme.status_dot(body, voice_text, theme.ACCENT_GREEN if is_enrolled() else theme.FG_MUTED).pack(
        fill=tk.X, pady=2, **pad
    )

    import os

    from training.train_wake_word import OUTPUT_MODEL_PATH

    wake_word_trained = os.path.exists(OUTPUT_MODEL_PATH)
    wake_word_active = wake_word_trained and str(OUTPUT_MODEL_PATH).replace("\\", "/") == str(
        config.wake_word.model_path
    ).replace("\\", "/")
    if wake_word_active:
        wake_text, wake_color = "Custom wake word: active", theme.ACCENT_GREEN
    elif wake_word_trained:
        wake_text, wake_color = "Custom wake word: trained, not active", theme.ACCENT_ORANGE
    else:
        wake_text, wake_color = "Custom wake word: not trained", theme.FG_MUTED
    theme.status_dot(body, wake_text, wake_color).pack(fill=tk.X, pady=2, **pad)

    calib_btn_row = tk.Frame(body, bg=theme.BG_CARD)
    calib_btn_row.pack(fill=tk.X, pady=(8, 0), **pad)
    theme.primary_button(
        calib_btn_row, "Run Audio Calibration →", command=lambda: open_audio_calibration_window(0), width=22
    ).pack(side=tk.LEFT)

    _divider(body).pack(fill=tk.X, **pad)

    # ---- Live transcript bar ------------------------------------------------
    theme.section_heading(body, "Live transcript bar").pack(fill=tk.X, pady=(0, 2), **pad)
    theme.body_text(body, "A small always-on-top bar showing the current exchange.").pack(
        fill=tk.X, pady=(0, 6), **pad
    )

    from ui.transcript_bar import set_enabled as _set_transcript_bar_enabled

    transcript_var = tk.BooleanVar(value=config.ui.transcript_bar_enabled)

    def _on_transcript_toggle() -> None:
        if state is not None:
            _set_transcript_bar_enabled(state, transcript_var.get())
        else:
            patch_config({"ui": {"transcript_bar_enabled": transcript_var.get()}})

    tk.Checkbutton(
        body,
        text="Show live transcript bar",
        variable=transcript_var,
        command=_on_transcript_toggle,
        bg=theme.BG_CARD,
        fg=theme.FG_TEXT,
        selectcolor=theme.BG_INSET,
        activebackground=theme.BG_CARD,
        activeforeground=theme.FG_TEXT,
        font=theme.FONT_BODY,
        highlightthickness=0,
        bd=0,
    ).pack(anchor="w", **pad)

    _divider(body).pack(fill=tk.X, **pad)

    # ---- General --------------------------------------------------------
    theme.section_heading(body, "General").pack(fill=tk.X, pady=(0, 2), **pad)

    fields: dict[str, tk.Variable] = {}

    wake_word_var = tk.StringVar(value=config.wake_word.phrase)
    fields["wake_word_phrase"] = wake_word_var
    _field_row(body, "Wake word phrase", lambda p: _dark_entry(p, wake_word_var))

    tts_speed_var = tk.DoubleVar(value=config.tts.speed)
    fields["tts_speed"] = tts_speed_var
    _field_row(body, "TTS voice speed", lambda p: _dark_scale(p, tts_speed_var, 0.5, 2.0, 0.1))

    idle_unload_var = tk.IntVar(value=config.lifecycle.idle_unload_minutes)
    fields["idle_unload_minutes"] = idle_unload_var
    _field_row(body, "Idle unload (minutes)", lambda p: _dark_spinbox(p, idle_unload_var, 1, 60))

    volume_step_var = tk.IntVar(value=config.volume.volume_step_percent)
    fields["volume_step_percent"] = volume_step_var
    _field_row(body, "Volume step (%)", lambda p: _dark_spinbox(p, volume_step_var, 1, 50))

    pause_hotkey_var = tk.StringVar(value=config.hotkeys.pause_resume)
    fields["pause_resume_hotkey"] = pause_hotkey_var
    _field_row(body, "Pause/Resume hotkey", lambda p: _dark_entry(p, pause_hotkey_var))

    quit_hotkey_var = tk.StringVar(value=config.hotkeys.quit)
    fields["quit_hotkey"] = quit_hotkey_var
    _field_row(body, "Quit hotkey", lambda p: _dark_entry(p, quit_hotkey_var))

    theme.body_text(body, "Hotkey changes require restarting MIMIR to take effect.").pack(
        fill=tk.X, pady=(6, 0), **pad
    )

    def _save() -> None:
        try:
            patch_config(
                {
                    "wake_word": {"phrase": wake_word_var.get()},
                    "tts": {"speed": tts_speed_var.get()},
                    "lifecycle": {"idle_unload_minutes": idle_unload_var.get()},
                    "volume": {"volume_step_percent": volume_step_var.get()},
                    "hotkeys": {"pause_resume": pause_hotkey_var.get(), "quit": quit_hotkey_var.get()},
                }
            )
            messagebox.showinfo("MIMIR Settings", "Settings saved. Restart MIMIR for hotkey changes to apply.")
        except Exception as exc:
            messagebox.showerror("MIMIR Settings", f"Failed to save settings: {exc}")

    save_row = tk.Frame(body, bg=theme.BG_CARD)
    save_row.pack(fill=tk.X, pady=(18, 16), **pad)
    theme.primary_button(save_row, "Save", command=_save, width=12).pack()

    _window = window
    show_window(window)


def _device_choice_label() -> str:
    name = config.audio.input_device_name.strip()
    return name if name else "System default (auto-detect)"


def _device_choices() -> list[str]:
    try:
        from core.audio_device import list_input_devices

        names, seen = [], set()
        for d in list_input_devices():
            if d["name"] not in seen:
                names.append(d["name"])
                seen.add(d["name"])
    except Exception:
        names = []
    return ["System default (auto-detect)"] + names


if __name__ == "__main__":
    import time

    open_settings_window(AppState())
    time.sleep(5)
