"""Custom "hey mimir" wake-word training wizard, launched from Settings.

Collects real recordings of you saying the wake phrase, blends them
(heavily augmented - see training/train_wake_word.augment_real_clips)
with synthetic TTS positives and phonetically-similar hard negatives,
trains a small classifier, and exports an ONNX model - all on a
background thread, since training/embedding-extraction is real CPU work
that would otherwise freeze the Tk UI thread.

Nothing here touches config.yaml until you explicitly click "Activate",
matching the cutover-gate design in training/train_wake_word.py: offline
eval + a live listening test both have to look reasonable first.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import messagebox

import numpy as np

from config import config
from ui.ui_root import get_root, run_on_ui_thread, show_window

logger = logging.getLogger("mimir.wake_word_training_window")

_window: tk.Toplevel | None = None

_RECORD_SECONDS = 2.0
_LIVE_TEST_SECONDS = 8.0


def open_wake_word_training_window() -> None:
    """Open (or focus) the wake-word training wizard. Safe to call from any thread."""
    run_on_ui_thread(_open)


def _record_fixed_clip(duration_sec: float = _RECORD_SECONDS) -> np.ndarray:
    """Record a fixed-duration clip (not VAD-endpointed) - the wake-word
    classifier needs a consistent clip size, unlike the variable-length
    recording MIMIR's regular command path uses."""
    import sounddevice as sd

    from core.audio_device import get_input_device
    from training.train_wake_word import SAMPLE_RATE

    audio = sd.rec(
        int(duration_sec * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=get_input_device()
    )
    sd.wait()
    return audio.flatten().astype(np.float32) / 32768.0


def _open() -> None:
    global _window

    if _window is not None and _window.winfo_exists():
        show_window(_window)
        return

    from training.train_wake_word import RECOMMENDED_REAL_SAMPLES

    window = tk.Toplevel(get_root())
    window.title("MIMIR - Wake Word Training")
    window.geometry("460x300")
    window.resizable(False, False)
    window.columnconfigure(0, weight=1)

    def _close() -> None:
        global _window
        _window = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _close)

    wiz_state = {"samples": [], "sample_index": 0, "busy": False, "eval_results": None}
    n_samples = RECOMMENDED_REAL_SAMPLES

    tk.Label(window, text="Wake Word Training", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, pady=(12, 4))
    tk.Label(
        window,
        text='Record yourself saying "hey mimir" a number of times, then\ntrain a personalized wake-word model from your real voice.',
        justify="center",
    ).grid(row=1, column=0, pady=(0, 8))

    progress_label = tk.Label(window, text="", font=("Segoe UI", 10, "bold"))
    progress_label.grid(row=2, column=0, pady=(4, 0))

    phrase_label = tk.Label(window, text="", font=("Segoe UI", 11, "italic"))
    phrase_label.grid(row=3, column=0, pady=(4, 8))

    status_label = tk.Label(window, text="", fg="gray", wraplength=420, justify="center")
    status_label.grid(row=4, column=0, pady=(0, 8))

    action_button = tk.Button(window, text="Record", width=20)
    action_button.grid(row=5, column=0, pady=4)

    secondary_button = tk.Button(window, text="", width=20, state="disabled")
    secondary_button.grid(row=6, column=0, pady=4)

    tk.Button(window, text="Cancel", command=_close).grid(row=7, column=0, pady=(8, 12))

    def _update_record_prompt() -> None:
        idx = wiz_state["sample_index"]
        progress_label.configure(text=f"Sample {idx + 1} of {n_samples}")
        phrase_label.configure(text='Say: "hey mimir"')
        status_label.configure(text="Click Record, then say the phrase once recording starts.", fg="gray")
        action_button.configure(state="normal", text="Record", command=_on_record_clicked)

    def _on_record_clicked() -> None:
        if wiz_state["busy"]:
            return
        wiz_state["busy"] = True
        action_button.configure(state="disabled", text="Recording...")
        status_label.configure(text="Recording now - say it!", fg="green")

        def _worker() -> None:
            audio = _record_fixed_clip()
            run_on_ui_thread(lambda: _on_sample_recorded(audio))

        threading.Thread(target=_worker, name="wakeword-training-record", daemon=True).start()

    def _on_sample_recorded(audio: np.ndarray) -> None:
        from core.stt import contains_speech

        wiz_state["busy"] = False
        if not contains_speech(audio):
            status_label.configure(text="Didn't catch anything - try again, a bit louder.", fg="#b00000")
            action_button.configure(state="normal", text="Record")
            return

        wiz_state["samples"].append(audio)
        wiz_state["sample_index"] += 1
        status_label.configure(text="Captured.", fg="gray")

        if wiz_state["sample_index"] < n_samples:
            window.after(500, _update_record_prompt)
        else:
            window.after(500, _start_training)

    def _start_training() -> None:
        progress_label.configure(text="Training...")
        phrase_label.configure(text="")
        status_label.configure(text="Starting up - this can take a few minutes.", fg="gray")
        action_button.configure(state="disabled", text="Training...")

        def _report(msg: str) -> None:
            run_on_ui_thread(lambda: status_label.configure(text=msg, fg="gray"))

        def _worker() -> None:
            from training.train_wake_word import OUTPUT_MODEL_PATH, run_full_pipeline

            try:
                results = run_full_pipeline(
                    wiz_state["samples"],
                    progress_callback=_report,
                    n_synthetic_positive=150,
                    max_steps=3000,
                    output_path=OUTPUT_MODEL_PATH,
                )
                run_on_ui_thread(lambda: _on_training_done(results))
            except Exception as exc:
                logger.exception("Wake-word training failed")
                run_on_ui_thread(lambda: _on_training_failed(exc))

        threading.Thread(target=_worker, name="wakeword-training-run", daemon=True).start()

    def _on_training_failed(exc: Exception) -> None:
        messagebox.showerror("MIMIR Wake Word Training", f"Training failed: {exc}")
        _close()

    def _on_training_done(results: dict) -> None:
        wiz_state["eval_results"] = results
        fp_rate = results["false_positive_rate"]
        progress_label.configure(text="Training complete")
        status_label.configure(
            text=(
                f"Held-out false-positive rate: {fp_rate:.0%} ({results['false_positives']}/{results['n_clips']} "
                "clips wrongly fired).\nThis is a synthetic offline estimate - test it live before activating."
            ),
            fg="green" if fp_rate < 0.05 else "#b06000",
        )
        action_button.configure(state="normal", text="Test Live", command=_on_test_live_clicked)
        secondary_button.configure(state="normal", text="Activate", command=_on_activate_clicked)

    def _on_test_live_clicked() -> None:
        from training.train_wake_word import OUTPUT_MODEL_PATH

        action_button.configure(state="disabled", text="Listening...")
        status_label.configure(text=f'Say "hey mimir" a few times over the next {_LIVE_TEST_SECONDS:.0f}s...', fg="green")

        def _worker() -> None:
            import sounddevice as sd
            from openwakeword.model import Model

            from core.audio_device import get_input_device
            from training.train_wake_word import SAMPLE_RATE

            chunk_size = 1280
            n_chunks = int(_LIVE_TEST_SECONDS * SAMPLE_RATE / chunk_size)
            max_score = 0.0
            try:
                model = Model(wakeword_models=[str(OUTPUT_MODEL_PATH)], inference_framework="onnx")
                with sd.InputStream(
                    samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=chunk_size, device=get_input_device()
                ) as stream:
                    for _ in range(n_chunks):
                        chunk, _overflowed = stream.read(chunk_size)
                        predictions = model.predict(chunk.flatten().astype(np.int16))
                        max_score = max(max_score, max(predictions.values()))
            except Exception as exc:
                run_on_ui_thread(lambda: _on_test_live_failed(exc))
                return
            run_on_ui_thread(lambda: _on_test_live_done(max_score))

        threading.Thread(target=_worker, name="wakeword-training-livetest", daemon=True).start()

    def _on_test_live_failed(exc: Exception) -> None:
        action_button.configure(state="normal", text="Test Live")
        messagebox.showerror("MIMIR Wake Word Training", f"Live test failed: {exc}")

    def _on_test_live_done(max_score: float) -> None:
        action_button.configure(state="normal", text="Test Again")
        fired = max_score > config.wake_word.sensitivity
        status_label.configure(
            text=f"Max score observed: {max_score:.2f} (threshold {config.wake_word.sensitivity:.2f}) - "
            f"{'fired' if fired else 'did NOT fire'}.",
            fg="green" if fired else "#b00000",
        )

    def _on_activate_clicked() -> None:
        from training.train_wake_word import OUTPUT_MODEL_PATH

        proceed = messagebox.askyesno(
            "MIMIR Wake Word Training",
            "This will set your wake word to \"hey mimir\" using the model you just "
            "trained, and requires restarting MIMIR to take effect. Continue?",
        )
        if not proceed:
            return

        try:
            import yaml

            config_path = "config.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            data.setdefault("wake_word", {})["phrase"] = "hey mimir"
            data.setdefault("wake_word", {})["model_path"] = str(OUTPUT_MODEL_PATH).replace("\\", "/")
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, default_flow_style=False)
            config.reload(config_path)
            messagebox.showinfo("MIMIR Wake Word Training", "Activated. Restart MIMIR for it to take effect.")
            _close()
        except Exception as exc:
            messagebox.showerror("MIMIR Wake Word Training", f"Failed to update config.yaml: {exc}")

    _update_record_prompt()

    _window = window
    show_window(window)


if __name__ == "__main__":
    import time

    open_wake_word_training_window()
    time.sleep(120)
