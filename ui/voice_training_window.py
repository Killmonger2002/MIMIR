"""Voice-enrollment wizard for speaker verification, launched from the
Settings window. Records a few short samples, averages them into a
reference voice-print (via core.voice_profile), and saves it - the UI
equivalent of running enroll_voice.py from a terminal.

Recording and embedding computation are blocking I/O/CPU work, so they
always run on a background thread; only widget creation/updates happen
on the Tk UI thread (via run_on_ui_thread / window.after), matching the
threading rules in ui/ui_root.py.
"""

from __future__ import annotations

import datetime
import logging
import threading
import tkinter as tk
from tkinter import messagebox

import numpy as np

from config import config
from core.voice_profile import ENROLLMENT_PHRASES, enroll_from_samples
from ui.ui_root import get_root, run_on_ui_thread, show_window

logger = logging.getLogger("mimir.voice_training_window")

_window: tk.Toplevel | None = None


def open_voice_training_window() -> None:
    """Open (or focus) the voice-training wizard. Safe to call from any thread."""
    run_on_ui_thread(_open)


def _open() -> None:
    global _window

    if _window is not None and _window.winfo_exists():
        show_window(_window)
        return

    from core.voice_profile import is_enrolled, profile_enrolled_at

    if is_enrolled():
        enrolled_at = profile_enrolled_at()
        when = (
            datetime.datetime.fromtimestamp(enrolled_at).strftime("%Y-%m-%d %H:%M")
            if enrolled_at
            else "unknown time"
        )
        proceed = messagebox.askyesno(
            "MIMIR Voice Training",
            f"You already have a voice profile enrolled ({when}).\nRe-training will replace it. Continue?",
        )
        if not proceed:
            return

    window = tk.Toplevel(get_root())
    window.title("MIMIR - Voice Training")
    window.geometry("420x260")
    window.resizable(False, False)
    window.columnconfigure(0, weight=1)

    def _close() -> None:
        global _window
        _window = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _close)

    # Mutable wizard state, closed over by the nested callbacks below -
    # simpler than a class for a short-lived, single-purpose wizard.
    wiz_state = {"samples": [], "phrase_index": 0, "recording": False}

    tk.Label(window, text="Voice Training", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, pady=(12, 4))
    tk.Label(
        window,
        text="Record a few short samples so MIMIR can tell your voice\napart from other people talking nearby.",
        justify="center",
    ).grid(row=1, column=0, pady=(0, 8))

    progress_label = tk.Label(window, text="", font=("Segoe UI", 10, "bold"))
    progress_label.grid(row=2, column=0, pady=(4, 0))

    phrase_label = tk.Label(window, text="", wraplength=380, font=("Segoe UI", 10, "italic"))
    phrase_label.grid(row=3, column=0, pady=(4, 8))

    status_label = tk.Label(window, text="", fg="gray")
    status_label.grid(row=4, column=0, pady=(0, 8))

    record_button = tk.Button(window, text="Record", width=18)
    record_button.grid(row=5, column=0, pady=4)

    tk.Button(window, text="Cancel", command=_close).grid(row=6, column=0, pady=(4, 12))

    def _update_prompt() -> None:
        idx = wiz_state["phrase_index"]
        total = len(ENROLLMENT_PHRASES)
        phrase, guidance = ENROLLMENT_PHRASES[idx]
        progress_label.configure(text=f"Sample {idx + 1} of {total}")
        phrase_label.configure(text=f'Say: "{phrase}"')
        status_label.configure(text=guidance, fg="gray")
        record_button.configure(state="normal", text="Record", command=_on_record_clicked)

    def _on_record_clicked() -> None:
        if wiz_state["recording"]:
            return
        wiz_state["recording"] = True
        record_button.configure(state="disabled", text="Recording...")
        status_label.configure(text="Speak now...", fg="green")

        def _record_worker() -> None:
            from core import stt

            audio = stt.record_until_silence()
            run_on_ui_thread(lambda: _on_sample_recorded(audio))

        threading.Thread(target=_record_worker, name="voice-training-record", daemon=True).start()

    def _on_sample_recorded(audio: np.ndarray) -> None:
        wiz_state["recording"] = False
        duration = len(audio) / config.stt.sample_rate

        if duration < 0.5:
            status_label.configure(text=f"That was very short ({duration:.1f}s) - try again.", fg="#b00000")
            record_button.configure(state="normal", text="Record")
            return

        wiz_state["samples"].append(audio)
        status_label.configure(text=f"Captured {duration:.1f}s.", fg="gray")
        wiz_state["phrase_index"] += 1

        if wiz_state["phrase_index"] < len(ENROLLMENT_PHRASES):
            window.after(600, _update_prompt)
        else:
            window.after(600, _finish_recording)

    def _finish_recording() -> None:
        progress_label.configure(text="Processing...")
        phrase_label.configure(text="")
        status_label.configure(text="Computing your voice profile...", fg="gray")
        record_button.configure(state="disabled", text="Record")

        def _process_worker() -> None:
            try:
                enroll_from_samples(wiz_state["samples"], config.stt.sample_rate)
                run_on_ui_thread(_on_enrolled)
            except Exception as exc:
                logger.exception("Voice enrollment failed")
                run_on_ui_thread(lambda: _on_enroll_failed(exc))

        threading.Thread(target=_process_worker, name="voice-training-process", daemon=True).start()

    def _on_enrolled() -> None:
        progress_label.configure(text="Voice profile saved!")
        status_label.configure(text="Restart MIMIR for this to take effect.", fg="green")
        record_button.configure(text="Test My Voice", state="normal", command=_on_test_clicked)

    def _on_enroll_failed(exc: Exception) -> None:
        messagebox.showerror("MIMIR Voice Training", f"Enrollment failed: {exc}")
        _close()

    def _on_test_clicked() -> None:
        record_button.configure(state="disabled", text="Recording...")
        status_label.configure(text="Speak now to test...", fg="green")

        def _test_worker() -> None:
            from core import stt
            from core.voice_profile import compute_embedding, load_profile

            audio = stt.record_until_silence()
            reference = load_profile()
            test_embed = compute_embedding(audio, config.stt.sample_rate)
            run_on_ui_thread(lambda: _on_test_recorded(test_embed, reference))

        threading.Thread(target=_test_worker, name="voice-training-test", daemon=True).start()

    def _on_test_recorded(test_embed: np.ndarray | None, reference: np.ndarray | None) -> None:
        record_button.configure(state="normal", text="Test Again")
        if test_embed is None or reference is None:
            status_label.configure(text="That was too quiet to check.", fg="#b00000")
            return
        similarity = float(np.dot(test_embed, reference) / (np.linalg.norm(test_embed) * np.linalg.norm(reference)))
        threshold = config.speaker_verification.similarity_threshold
        above = similarity >= threshold
        status_label.configure(
            text=f"Similarity: {similarity:.2f} (threshold {threshold:.2f} - {'above' if above else 'BELOW'} it)",
            fg="green" if above else "#b00000",
        )

    _update_prompt()

    _window = window
    show_window(window)


if __name__ == "__main__":
    import time

    open_voice_training_window()
    time.sleep(60)
