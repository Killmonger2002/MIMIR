"""Unified audio calibration wizard: mic level -> noise baseline -> wake
word training -> voice profile enrollment, in one 4-step flow launched
from Settings ("Run Audio Calibration").

Steps 3-4 are new visual chrome over the existing, already-tested
pipelines (training/train_wake_word.py, core/voice_profile.py) - the
recording/training/enrollment logic itself is unchanged from the former
standalone ui/wake_word_training_window.py and ui/voice_training_window.py,
which this wizard replaces as the single entry point. Steps 1-2 (live
mic-level/noise-floor metering, ambient noise baseline -> VAD threshold)
are new functionality.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import tkinter as tk
from tkinter import messagebox

import numpy as np

from config import config
from core.config_writer import patch_config
from ui import theme
from ui.ui_root import get_root, run_on_ui_thread, show_window

logger = logging.getLogger("mimir.audio_calibration_window")

_window: tk.Toplevel | None = None
_wizard: "_CalibrationWizard | None" = None

_STEP_LABELS = ["Mic level", "Noise", "Wake word", "Voice profile"]

# Heuristic scale so normal speaking volume reads as roughly 50-85% on the
# level meters - not a calibrated dB reading, just a usable visual target.
_LEVEL_GAIN = 350.0
_METER_BLOCK_SEC = 0.1
_WAKE_RECORD_SECONDS = 2.0
_WAKE_LIVE_TEST_SECONDS = 8.0


def open_audio_calibration_window(start_step: int = 0) -> None:
    """Open (or focus) the calibration wizard, on a given step. Safe to
    call from any thread."""
    run_on_ui_thread(lambda: _open(start_step))


def _open(start_step: int) -> None:
    global _window, _wizard

    if _window is not None and _window.winfo_exists():
        _wizard._go_to(start_step)
        show_window(_window)
        return

    window = tk.Toplevel(get_root())
    window.title("MIMIR - Audio Calibration")
    window.geometry("560x660")
    window.resizable(False, False)

    wizard = _CalibrationWizard(window, start_step)
    window.protocol("WM_DELETE_WINDOW", wizard.close)

    _window = window
    _wizard = wizard
    show_window(window)


def _record_fixed_clip(duration_sec: float = _WAKE_RECORD_SECONDS) -> np.ndarray:
    """Record a fixed-duration clip (not VAD-endpointed) - the wake-word
    classifier needs a consistent clip size, unlike record_until_silence()."""
    import sounddevice as sd

    from core.audio_device import get_input_device
    from training.train_wake_word import SAMPLE_RATE

    audio = sd.rec(
        int(duration_sec * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=get_input_device()
    )
    sd.wait()
    return audio.flatten().astype(np.float32) / 32768.0


class _CalibrationWizard:
    def __init__(self, window: tk.Toplevel, start_step: int) -> None:
        self.window = window
        self.step = 0
        self._teardown_step = None

        self._meter_stop = threading.Event()
        self._meter_queue: queue.Queue = queue.Queue(maxsize=4)
        self._last_level = 0.0
        self._last_floor = 0.0

        self._noise_result: dict | None = None
        self._wake_state = {"samples": [], "sample_index": 0, "busy": False, "trained": False, "activated": False}
        self._voice_state = {"samples": [], "phrase_index": 0, "recording": False, "enrolled": False}

        theme.apply_window_theme(window)
        outer = theme.card(window)
        outer.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        header, self._right_label = theme.title_row(outer, "MIMIR — audio calibration", "")
        header.pack(fill=tk.X, pady=(2, 10))

        self.indicator = theme.StepIndicator(outer, _STEP_LABELS, current=0)
        self.indicator.pack(pady=(0, 14))

        self.content = tk.Frame(outer, bg=theme.BG_CARD)
        self.content.pack(fill=tk.BOTH, expand=True)

        nav = tk.Frame(outer, bg=theme.BG_CARD)
        nav.pack(fill=tk.X, pady=(14, 0))
        self.back_btn = theme.ghost_button(nav, "Back", command=self._go_back)
        self.back_btn.pack(side=tk.LEFT)
        self.next_btn = theme.primary_button(nav, "Continue", command=self._go_next)
        self.next_btn.pack(side=tk.RIGHT)

        self._go_to(start_step)

    # ---- navigation -----------------------------------------------------

    def _active(self, step: int) -> bool:
        """True if `step` is still the one on screen - guards async
        callbacks (recording/training finish) that can land after the
        user has already navigated away."""
        return self.window.winfo_exists() and self.step == step

    def _go_to(self, step: int) -> None:
        if self._teardown_step:
            self._teardown_step()
            self._teardown_step = None

        self.step = step
        self.indicator.set_current(step)
        self._right_label.configure(text=f"Step {step + 1} of {len(_STEP_LABELS)}")

        for child in self.content.winfo_children():
            child.destroy()

        self.back_btn.configure(state="disabled" if step == 0 else "normal")
        self.next_btn.configure(text="Save & finish" if step == len(_STEP_LABELS) - 1 else "Continue")

        [self._render_step1, self._render_step2, self._render_step3, self._render_step4][step]()
        self._update_nav_state()

    def _go_back(self) -> None:
        if self.step > 0:
            self._go_to(self.step - 1)

    def _go_next(self) -> None:
        if self.step < len(_STEP_LABELS) - 1:
            self._go_to(self.step + 1)
        else:
            self.close()

    def _update_nav_state(self) -> None:
        if self.step == 0:
            ready = True
        elif self.step == 1:
            ready = self._noise_result is not None
        elif self.step == 2:
            ready = self._wake_state["trained"]
        else:
            ready = self._voice_state["enrolled"]
        self.next_btn.configure(state="normal" if ready else "disabled")

    def close(self) -> None:
        global _window
        if self._teardown_step:
            self._teardown_step()
        _window = None
        self.window.destroy()

    # ---- Step 1: mic level -----------------------------------------------

    def _device_choice_label(self) -> str:
        name = config.audio.input_device_name.strip()
        return name if name else "System default (auto-detect)"

    def _device_choices(self) -> list[str]:
        try:
            from core.audio_device import list_input_devices

            names, seen = [], set()
            for d in list_input_devices():
                if d["name"] not in seen:
                    names.append(d["name"])
                    seen.add(d["name"])
        except Exception:
            logger.exception("Failed to enumerate input devices")
            names = []
        return ["System default (auto-detect)"] + names

    def _device_display_name(self) -> str:
        try:
            import sounddevice as sd

            from core.audio_device import get_input_device

            idx = get_input_device()
            if idx is None:
                return sd.query_devices(kind="input")["name"]
            return sd.query_devices(idx)["name"]
        except Exception:
            return "unknown device"

    def _render_step1(self) -> None:
        parent = self.content

        theme.section_heading(parent, "Microphone level check").pack(fill=tk.X)
        theme.body_text(
            parent,
            "Speak normally. The bar should reach the green zone. Adjust your mic "
            "position or Windows input gain if it stays too low or clips into red.",
        ).pack(fill=tk.X, pady=(2, 14))

        device_row = tk.Frame(parent, bg=theme.BG_CARD)
        device_row.pack(fill=tk.X, pady=(0, 14))
        tk.Label(device_row, text="Input device", font=theme.FONT_BODY, bg=theme.BG_CARD, fg=theme.FG_TEXT).pack(
            side=tk.LEFT
        )
        self._device_var = tk.StringVar(value=self._device_choice_label())
        device_menu = theme.styled_option_menu(
            device_row, self._device_var, self._device_choices(), command=self._on_device_selected
        )
        device_menu.pack(side=tk.RIGHT)

        level_frame, self._level_bar, self._level_val = theme.labeled_progress(parent, "Input level")
        level_frame.pack(fill=tk.X, pady=(0, 12))
        floor_frame, self._floor_bar, self._floor_val = theme.labeled_progress(parent, "Background noise floor")
        floor_frame.pack(fill=tk.X, pady=(0, 14))

        status_frame = tk.Frame(parent, bg=theme.BG_CARD)
        status_frame.pack(fill=tk.X)
        self._mic_row1, self._mic_update1 = theme.status_dot_dynamic(status_frame)
        self._mic_row1.pack(fill=tk.X, pady=2)
        self._mic_row2, self._mic_update2 = theme.status_dot_dynamic(status_frame)
        self._mic_row2.pack(fill=tk.X, pady=2)
        self._mic_row3, self._mic_update3 = theme.status_dot_dynamic(status_frame)
        self._mic_row3.pack(fill=tk.X, pady=2)

        self._mic_update1(f"Microphone detected — {self._device_display_name()}", theme.ACCENT_GREEN)
        self._mic_update2("Waiting for audio - speak normally to test the input level.", theme.FG_MUTED)
        self._mic_update3("Listening for background noise…", theme.FG_MUTED)

        self._start_meter()
        self._teardown_step = self._stop_meter

    def _on_device_selected(self, choice: str) -> None:
        from core.audio_device import reset_cache

        value = "" if choice.startswith("System default") else choice
        patch_config({"audio": {"input_device_name": value}})
        reset_cache()
        if self.step == 0:
            self._mic_update1(f"Microphone detected — {self._device_display_name()}", theme.ACCENT_GREEN)
            self._stop_meter()
            self.window.after(150, self._start_meter)

    def _start_meter(self) -> None:
        from core.audio_device import get_input_device

        self._meter_stop = threading.Event()
        self._meter_queue = queue.Queue(maxsize=4)
        device_index = get_input_device()
        stop_event = self._meter_stop
        out_queue = self._meter_queue
        threading.Thread(
            target=self._meter_worker, args=(device_index, stop_event, out_queue), name="calib-meter", daemon=True
        ).start()
        self.window.after(100, lambda: self._poll_meter(stop_event))

    def _stop_meter(self) -> None:
        self._meter_stop.set()

    def _meter_worker(self, device_index, stop_event: threading.Event, out_queue: queue.Queue) -> None:
        import sounddevice as sd

        sample_rate = config.stt.sample_rate
        block_size = int(sample_rate * _METER_BLOCK_SEC)
        floor_hist: collections.deque = collections.deque(maxlen=50)
        try:
            with sd.InputStream(
                samplerate=sample_rate, channels=1, dtype="int16", blocksize=block_size, device=device_index
            ) as stream:
                while not stop_event.is_set():
                    block, _overflowed = stream.read(block_size)
                    samples = block.flatten().astype(np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
                    level_pct = min(100.0, rms * _LEVEL_GAIN)
                    floor_hist.append(level_pct)
                    floor_pct = float(np.percentile(floor_hist, 20)) if len(floor_hist) >= 5 else level_pct
                    try:
                        out_queue.put_nowait((level_pct, floor_pct))
                    except queue.Full:
                        pass
        except Exception as exc:
            logger.warning("Mic level meter stream failed: %s", exc)
            try:
                out_queue.put_nowait(("error", str(exc)))
            except queue.Full:
                pass

    def _poll_meter(self, owning_stop_event: threading.Event) -> None:
        if owning_stop_event.is_set() or not self.window.winfo_exists() or self.step != 0:
            return
        try:
            while True:
                level_pct, floor_pct = self._meter_queue.get_nowait()
                if level_pct == "error":
                    self._mic_update2(f"Couldn't read from the microphone: {floor_pct}", theme.ACCENT_RED)
                    owning_stop_event.set()
                    return
                self._last_level, self._last_floor = level_pct, floor_pct
                self._level_bar["value"] = level_pct
                self._level_val.configure(text=f"{level_pct:.0f}%")
                self._floor_bar["value"] = floor_pct
                self._floor_val.configure(text=f"{floor_pct:.0f}%")
                self._render_mic_status(level_pct, floor_pct)
        except queue.Empty:
            pass
        self.window.after(100, lambda: self._poll_meter(owning_stop_event))

    def _render_mic_status(self, level_pct: float, floor_pct: float) -> None:
        if level_pct > 85:
            self._mic_update2("Input level is clipping - move back from the mic or lower input gain.", theme.ACCENT_RED)
        elif level_pct >= 50:
            self._mic_update2("Input level in recommended range (50-85%)", theme.ACCENT_GREEN)
        elif level_pct > 1:
            self._mic_update2("Input level is low - move closer to the mic or raise input gain.", theme.ACCENT_ORANGE)
        else:
            self._mic_update2("Waiting for audio - speak normally to test the input level.", theme.FG_MUTED)

        if floor_pct >= 15:
            self._mic_update3(f"Moderate background noise detected ({floor_pct:.0f}%).", theme.ACCENT_ORANGE)
        else:
            self._mic_update3(f"Background is quiet ({floor_pct:.0f}%).", theme.ACCENT_GREEN)

    # ---- Step 2: noise baseline ------------------------------------------

    def _render_step2(self) -> None:
        parent = self.content

        theme.section_heading(parent, "Noise baseline capture").pack(fill=tk.X)
        theme.body_text(
            parent,
            "Stay silent for 3 seconds. MIMIR will record your room's noise profile "
            "and use it to set how sensitive listening is to background sound.",
        ).pack(fill=tk.X, pady=(2, 14))

        progress_frame, self._noise_bar, self._noise_val = theme.labeled_progress(parent, "Ambient noise sample")
        progress_frame.pack(fill=tk.X, pady=(0, 14))

        self._noise_status_frame = tk.Frame(parent, bg=theme.BG_CARD)
        self._noise_status_frame.pack(fill=tk.X, pady=(0, 10))

        btn_row = tk.Frame(parent, bg=theme.BG_CARD)
        btn_row.pack(fill=tk.X, pady=(4, 12))
        self._noise_button = theme.secondary_button(
            btn_row, "Recapture" if self._noise_result else "Start Capture", command=self._on_capture_noise
        )
        self._noise_button.pack(side=tk.LEFT)

        theme.note_box(
            parent,
            "Re-run this step whenever your environment changes significantly - "
            "moving rooms, turning on AC, etc.",
        ).pack(fill=tk.X, pady=(10, 0))

        if self._noise_result is not None:
            self._noise_bar["value"] = 100
            self._noise_val.configure(text="captured")
            self._show_noise_result()

    def _on_capture_noise(self) -> None:
        self._noise_button.configure(state="disabled", text="Recording…")
        self._noise_val.configure(text="listening…")
        self._noise_bar["value"] = 0
        self.window.after(100, lambda: self._animate_noise_progress(0))
        threading.Thread(target=self._capture_noise_worker, name="calib-noise-capture", daemon=True).start()

    def _animate_noise_progress(self, tick: int) -> None:
        if not self._active(1) or self._noise_button["text"] != "Recording…":
            return
        self._noise_bar["value"] = min(100, tick * (100 / 30))
        self.window.after(100, lambda: self._animate_noise_progress(tick + 1))

    def _capture_noise_worker(self) -> None:
        import sounddevice as sd
        from openwakeword.vad import VAD

        from core.audio_device import get_input_device

        sample_rate = config.stt.sample_rate
        duration = 3.0
        try:
            audio = sd.rec(
                int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype="int16", device=get_input_device()
            )
            sd.wait()
        except Exception as exc:
            run_on_ui_thread(lambda: self._on_noise_capture_failed(exc))
            return

        flat = audio.flatten()
        samples = flat.astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        floor_pct = min(100.0, rms * _LEVEL_GAIN)

        vad = VAD()
        vad.reset_states()
        block = int(sample_rate * 0.1)
        max_prob = 0.0
        for start in range(0, len(flat) - block + 1, block):
            chunk = flat[start : start + block].astype(np.int16)
            prob = vad.predict(chunk, frame_size=400)
            max_prob = max(max_prob, float(prob))

        threshold = float(np.clip(max_prob + 0.15, 0.3, 0.9))
        result = {"floor_pct": floor_pct, "max_prob": max_prob, "threshold": round(threshold, 2)}
        run_on_ui_thread(lambda: self._on_noise_captured(result))

    def _on_noise_capture_failed(self, exc: Exception) -> None:
        if not self._active(1):
            return
        self._noise_button.configure(state="normal", text="Start Capture")
        messagebox.showerror("MIMIR Audio Calibration", f"Noise capture failed: {exc}")

    def _on_noise_captured(self, result: dict) -> None:
        if not self._active(1):
            self._noise_result = result
            return
        self._noise_result = result
        patch_config({"stt": {"vad_speech_threshold": result["threshold"]}})
        self._noise_button.configure(state="normal", text="Recapture")
        self._noise_bar["value"] = 100
        self._noise_val.configure(text="captured")
        self._show_noise_result()
        self._update_nav_state()

    def _show_noise_result(self) -> None:
        for w in self._noise_status_frame.winfo_children():
            w.destroy()
        r = self._noise_result
        theme.status_dot(
            self._noise_status_frame, "Baseline saved — noise floor profile ready", theme.ACCENT_GREEN
        ).pack(fill=tk.X, pady=2)
        theme.status_dot(
            self._noise_status_frame,
            f"Silero-VAD threshold set to {r['threshold']:.2f} based on your noise floor",
            theme.ACCENT_GREEN,
        ).pack(fill=tk.X, pady=2)

    # ---- Step 3: wake word training ---------------------------------------

    def _render_step3(self) -> None:
        parent = self.content
        from training.train_wake_word import RECOMMENDED_REAL_SAMPLES

        self._wake_state["n"] = RECOMMENDED_REAL_SAMPLES
        n = RECOMMENDED_REAL_SAMPLES

        theme.section_heading(parent, 'Wake word training — "hey mimir"').pack(fill=tk.X)
        theme.body_text(
            parent, f'Say "hey mimir" when the indicator turns blue. Record all {n} samples.'
        ).pack(fill=tk.X, pady=(2, 12))

        self._wake_progress_label = tk.Label(
            parent, text="", font=theme.FONT_BODY, bg=theme.BG_CARD, fg=theme.FG_TEXT, anchor="w"
        )
        self._wake_progress_label.pack(fill=tk.X)

        grid_frame = tk.Frame(parent, bg=theme.BG_CARD)
        grid_frame.pack(pady=(10, 12))
        self._wake_boxes = []
        cols = 5
        for i in range(n):
            box = tk.Label(
                grid_frame,
                text=str(i + 1),
                width=4,
                height=2,
                bg=theme.BG_INSET,
                fg=theme.FG_MUTED,
                highlightbackground=theme.BORDER,
                highlightthickness=1,
                font=theme.FONT_SMALL_BOLD,
            )
            box.grid(row=i // cols, column=i % cols, padx=4, pady=4)
            self._wake_boxes.append(box)

        status_frame = tk.Frame(parent, bg=theme.BG_CARD)
        status_frame.pack(fill=tk.X, pady=(2, 12))
        self._wake_status_row, self._wake_status_update = theme.status_dot_dynamic(status_frame)
        self._wake_status_row.pack(fill=tk.X)

        action_row = tk.Frame(parent, bg=theme.BG_CARD)
        action_row.pack(fill=tk.X)
        self._wake_action_btn = theme.secondary_button(action_row, "Record", command=self._on_wake_record)
        self._wake_action_btn.pack(side=tk.LEFT)
        self._wake_secondary_btn = theme.secondary_button(action_row, "Activate", command=self._on_wake_activate)

        if self._wake_state["trained"]:
            self._on_wake_training_done(self._wake_state["eval_results"], rerender=True)
        else:
            self._refresh_wake_boxes()
            self._update_wake_prompt()

    def _refresh_wake_boxes(self) -> None:
        idx = self._wake_state["sample_index"]
        recording = self._wake_state["busy"]
        for i, box in enumerate(self._wake_boxes):
            if i < idx:
                box.configure(bg=theme.ACCENT_GREEN, fg="#0c0c0d", text="✓", highlightbackground=theme.ACCENT_GREEN)
            elif i == idx and recording:
                box.configure(bg=theme.ACCENT_BLUE, fg="#ffffff", text=str(i + 1), highlightbackground=theme.ACCENT_BLUE)
            elif i == idx:
                box.configure(bg=theme.BG_INSET, fg=theme.FG_TEXT, text=str(i + 1), highlightbackground=theme.ACCENT_BLUE)
            else:
                box.configure(bg=theme.BG_INSET, fg=theme.FG_MUTED, text=str(i + 1), highlightbackground=theme.BORDER)

    def _update_wake_prompt(self) -> None:
        idx = self._wake_state["sample_index"]
        n = self._wake_state["n"]
        self._wake_progress_label.configure(text=f"Recorded {idx} of {n} samples")
        self._refresh_wake_boxes()
        if idx >= n:
            self._wake_status_update("All samples recorded.", theme.ACCENT_GREEN)
            self._wake_action_btn.configure(state="disabled", text="Recorded")
            self._start_wake_training()
            return
        self._wake_status_update('Click Record, then say "hey mimir" once recording starts.', theme.FG_MUTED)
        self._wake_action_btn.configure(state="normal", text="Record", command=self._on_wake_record)

    def _on_wake_record(self) -> None:
        if self._wake_state["busy"]:
            return
        self._wake_state["busy"] = True
        self._wake_action_btn.configure(state="disabled", text="Recording…")
        self._wake_status_update('Recording — say "hey mimir" now', theme.ACCENT_RED)
        self._refresh_wake_boxes()

        def _worker() -> None:
            audio = _record_fixed_clip()
            run_on_ui_thread(lambda: self._on_wake_sample_recorded(audio))

        threading.Thread(target=_worker, name="calib-wake-record", daemon=True).start()

    def _on_wake_sample_recorded(self, audio: np.ndarray) -> None:
        from core.stt import contains_speech

        self._wake_state["busy"] = False
        if not self._active(2):
            return
        if not contains_speech(audio):
            self._wake_status_update("Didn't catch anything - try again, a bit louder.", theme.ACCENT_ORANGE)
            self._wake_action_btn.configure(state="normal", text="Record")
            self._refresh_wake_boxes()
            return

        self._wake_state["samples"].append(audio)
        self._wake_state["sample_index"] += 1
        self.window.after(400, self._update_wake_prompt)

    def _start_wake_training(self) -> None:
        self._wake_status_update("Training - this can take a few minutes.", theme.FG_MUTED)

        def _report(msg: str) -> None:
            run_on_ui_thread(lambda: self._active(2) and self._wake_status_update(msg, theme.FG_MUTED))

        def _worker() -> None:
            from training.train_wake_word import OUTPUT_MODEL_PATH, run_full_pipeline

            try:
                results = run_full_pipeline(
                    self._wake_state["samples"],
                    progress_callback=_report,
                    n_synthetic_positive=150,
                    max_steps=3000,
                    output_path=OUTPUT_MODEL_PATH,
                )
                run_on_ui_thread(lambda: self._on_wake_training_done(results))
            except Exception as exc:
                logger.exception("Wake-word training failed")
                run_on_ui_thread(lambda: self._on_wake_training_failed(exc))

        threading.Thread(target=_worker, name="calib-wake-train", daemon=True).start()

    def _on_wake_training_failed(self, exc: Exception) -> None:
        if not self._active(2):
            return
        self._wake_status_update(f"Training failed: {exc}", theme.ACCENT_RED)
        messagebox.showerror("MIMIR Audio Calibration", f"Wake word training failed: {exc}")

    def _on_wake_training_done(self, results: dict, rerender: bool = False) -> None:
        self._wake_state["trained"] = True
        self._wake_state["eval_results"] = results
        if not self._active(2):
            return
        fp_rate = results["false_positive_rate"]
        self._wake_progress_label.configure(text="Training complete")
        color = theme.ACCENT_GREEN if fp_rate < 0.05 else theme.ACCENT_ORANGE
        self._wake_status_update(
            f"Held-out false-positive rate: {fp_rate:.0%} - test it live before activating.", color
        )
        self._wake_action_btn.configure(state="normal", text="Test Live", command=self._on_wake_test_live)
        self._wake_secondary_btn.configure(
            text="Activated" if self._wake_state["activated"] else "Activate",
            state="disabled" if self._wake_state["activated"] else "normal",
        )
        self._wake_secondary_btn.pack(side=tk.LEFT, padx=(8, 0))
        self._update_nav_state()

    def _on_wake_test_live(self) -> None:
        self._wake_action_btn.configure(state="disabled", text="Listening…")
        self._wake_status_update(
            f'Say "hey mimir" a few times over the next {_WAKE_LIVE_TEST_SECONDS:.0f}s…', theme.ACCENT_BLUE
        )

        def _worker() -> None:
            import sounddevice as sd
            from openwakeword.model import Model

            from core.audio_device import get_input_device
            from training.train_wake_word import OUTPUT_MODEL_PATH, SAMPLE_RATE

            chunk_size = 1280
            n_chunks = int(_WAKE_LIVE_TEST_SECONDS * SAMPLE_RATE / chunk_size)
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
                run_on_ui_thread(lambda: self._on_wake_test_failed(exc))
                return
            run_on_ui_thread(lambda: self._on_wake_test_done(max_score))

        threading.Thread(target=_worker, name="calib-wake-livetest", daemon=True).start()

    def _on_wake_test_failed(self, exc: Exception) -> None:
        if not self._active(2):
            return
        self._wake_action_btn.configure(state="normal", text="Test Live")
        messagebox.showerror("MIMIR Audio Calibration", f"Live test failed: {exc}")

    def _on_wake_test_done(self, max_score: float) -> None:
        if not self._active(2):
            return
        self._wake_action_btn.configure(state="normal", text="Test Again")
        fired = max_score > config.wake_word.sensitivity
        color = theme.ACCENT_GREEN if fired else theme.ACCENT_RED
        self._wake_status_update(
            f"Max score observed: {max_score:.2f} (threshold {config.wake_word.sensitivity:.2f}) - "
            f"{'fired' if fired else 'did NOT fire'}.",
            color,
        )

    def _on_wake_activate(self) -> None:
        from training.train_wake_word import OUTPUT_MODEL_PATH

        proceed = messagebox.askyesno(
            "MIMIR Audio Calibration",
            'This will set your wake word to "hey mimir" using the model you just '
            "trained, and requires restarting MIMIR to take effect. Continue?",
        )
        if not proceed:
            return
        try:
            patch_config(
                {"wake_word": {"phrase": "hey mimir", "model_path": str(OUTPUT_MODEL_PATH).replace("\\", "/")}}
            )
            self._wake_state["activated"] = True
            if self._active(2):
                self._wake_secondary_btn.configure(text="Activated", state="disabled")
            messagebox.showinfo("MIMIR Audio Calibration", "Activated. Restart MIMIR for it to take effect.")
        except Exception as exc:
            messagebox.showerror("MIMIR Audio Calibration", f"Failed to update config.yaml: {exc}")

    # ---- Step 4: voice profile ---------------------------------------------

    def _render_step4(self) -> None:
        parent = self.content

        theme.section_heading(parent, "Voice profile — speaker recognition").pack(fill=tk.X)
        theme.body_text(
            parent,
            "Read each phrase aloud naturally. This creates a voice embedding so "
            "MIMIR only responds to you, not background voices or TV.",
        ).pack(fill=tk.X, pady=(2, 12))

        self._voice_progress_label = tk.Label(
            parent, text="", font=theme.FONT_BODY, bg=theme.BG_CARD, fg=theme.FG_TEXT, anchor="w"
        )
        self._voice_progress_label.pack(fill=tk.X)

        phrase_frame = tk.Frame(parent, bg=theme.BG_INSET, highlightbackground=theme.BORDER, highlightthickness=1)
        phrase_frame.pack(fill=tk.X, pady=(8, 12))
        self._voice_phrase_label = tk.Label(
            phrase_frame,
            text="",
            font=("Segoe UI", 11, "italic"),
            bg=theme.BG_INSET,
            fg=theme.FG_TEXT,
            wraplength=440,
            justify="left",
        )
        self._voice_phrase_label.pack(padx=10, pady=10, fill=tk.X)

        rec_frame, self._voice_bar, self._voice_val = theme.labeled_progress(parent, "Recording")
        rec_frame.pack(fill=tk.X, pady=(0, 10))

        status_row = tk.Frame(parent, bg=theme.BG_CARD)
        status_row.pack(fill=tk.X, pady=(2, 12))
        self._voice_status_row, self._voice_status_update = theme.status_dot_dynamic(status_row)
        self._voice_status_row.pack(fill=tk.X)

        action_row = tk.Frame(parent, bg=theme.BG_CARD)
        action_row.pack(fill=tk.X)
        self._voice_action_btn = theme.secondary_button(action_row, "Record", command=self._on_voice_record)
        self._voice_action_btn.pack(side=tk.LEFT)

        if self._voice_state["enrolled"]:
            self._show_voice_enrolled()
        else:
            self._update_voice_prompt()

    def _update_voice_prompt(self) -> None:
        from core.voice_profile import ENROLLMENT_PHRASES

        idx = self._voice_state["phrase_index"]
        total = len(ENROLLMENT_PHRASES)
        if idx >= total:
            self._finish_voice_enrollment()
            return
        phrase, guidance = ENROLLMENT_PHRASES[idx]
        self._voice_progress_label.configure(text=f"Sample {idx + 1} of {total}")
        self._voice_phrase_label.configure(text=f'"{phrase}"')
        self._voice_status_update(guidance, theme.FG_MUTED)
        self._voice_bar["value"] = 0
        self._voice_val.configure(text="")
        self._voice_action_btn.configure(state="normal", text="Record", command=self._on_voice_record)

    def _on_voice_record(self) -> None:
        if self._voice_state["recording"]:
            return
        self._voice_state["recording"] = True
        self._voice_action_btn.configure(state="disabled", text="Recording…")
        self._voice_status_update("Speak now…", theme.ACCENT_RED)
        self._voice_elapsed_ticks = 0
        self._animate_voice_progress()

        def _worker() -> None:
            from core import stt

            audio = stt.record_until_silence()
            run_on_ui_thread(lambda: self._on_voice_sample_recorded(audio))

        threading.Thread(target=_worker, name="calib-voice-record", daemon=True).start()

    def _animate_voice_progress(self) -> None:
        if not self._active(3) or not self._voice_state["recording"]:
            return
        self._voice_elapsed_ticks += 1
        # Cosmetic only - actual stop is VAD-driven (record_until_silence),
        # not a fixed timer; ~15s is just a typical-length reference so the
        # bar reads as meaningful progress rather than an indeterminate spin.
        pct = min(100, self._voice_elapsed_ticks * 100 / 150)
        self._voice_bar["value"] = pct
        self._voice_val.configure(text=f"{self._voice_elapsed_ticks / 10:.0f}s")
        self.window.after(100, self._animate_voice_progress)

    def _on_voice_sample_recorded(self, audio: np.ndarray) -> None:
        self._voice_state["recording"] = False
        if not self._active(3):
            return
        duration = len(audio) / config.stt.sample_rate
        if duration < 0.5:
            self._voice_status_update(f"That was very short ({duration:.1f}s) - try again.", theme.ACCENT_RED)
            self._voice_action_btn.configure(state="normal", text="Record")
            return

        self._voice_state["samples"].append(audio)
        self._voice_bar["value"] = 100
        self._voice_val.configure(text=f"{duration:.1f}s")
        self._voice_status_update(f"Captured {duration:.1f}s.", theme.ACCENT_GREEN)
        self._voice_state["phrase_index"] += 1
        self.window.after(500, self._update_voice_prompt)

    def _finish_voice_enrollment(self) -> None:
        self._voice_progress_label.configure(text="Processing…")
        self._voice_phrase_label.configure(text="")
        self._voice_status_update("Computing your voice profile…", theme.FG_MUTED)
        self._voice_action_btn.configure(state="disabled", text="Record")

        def _worker() -> None:
            from core.voice_profile import enroll_from_samples

            try:
                enroll_from_samples(self._voice_state["samples"], config.stt.sample_rate)
                run_on_ui_thread(self._on_voice_enrolled)
            except Exception as exc:
                logger.exception("Voice enrollment failed")
                run_on_ui_thread(lambda: self._on_voice_enroll_failed(exc))

        threading.Thread(target=_worker, name="calib-voice-process", daemon=True).start()

    def _on_voice_enrolled(self) -> None:
        self._voice_state["enrolled"] = True
        if not self._active(3):
            self._update_nav_state()
            return
        self._show_voice_enrolled()
        self._update_nav_state()

    def _show_voice_enrolled(self) -> None:
        self._voice_progress_label.configure(text="Voice profile saved!")
        self._voice_phrase_label.configure(text="Voice embedding saved to models/voice_profile/reference.npy")
        self._voice_status_update(
            f"Similarity threshold: {config.speaker_verification.similarity_threshold:.2f} - lower it if "
            "MIMIR misses you, raise it if it responds to others.",
            theme.ACCENT_GREEN,
        )
        self._voice_action_btn.configure(state="normal", text="Test My Voice", command=self._on_voice_test)

    def _on_voice_enroll_failed(self, exc: Exception) -> None:
        if not self._active(3):
            return
        self._voice_status_update(f"Enrollment failed: {exc}", theme.ACCENT_RED)
        self._voice_action_btn.configure(state="normal", text="Record")
        messagebox.showerror("MIMIR Audio Calibration", f"Enrollment failed: {exc}")

    def _on_voice_test(self) -> None:
        self._voice_action_btn.configure(state="disabled", text="Recording…")
        self._voice_status_update("Speak now to test…", theme.ACCENT_RED)

        def _worker() -> None:
            from core import stt
            from core.voice_profile import compute_embedding, load_profile

            audio = stt.record_until_silence()
            reference = load_profile()
            test_embed = compute_embedding(audio, config.stt.sample_rate)
            run_on_ui_thread(lambda: self._on_voice_test_done(test_embed, reference))

        threading.Thread(target=_worker, name="calib-voice-test", daemon=True).start()

    def _on_voice_test_done(self, test_embed, reference) -> None:
        if not self._active(3):
            return
        self._voice_action_btn.configure(state="normal", text="Test Again")
        if test_embed is None or reference is None:
            self._voice_status_update("That was too quiet to check.", theme.ACCENT_RED)
            return
        similarity = float(np.dot(test_embed, reference) / (np.linalg.norm(test_embed) * np.linalg.norm(reference)))
        threshold = config.speaker_verification.similarity_threshold
        above = similarity >= threshold
        color = theme.ACCENT_GREEN if above else theme.ACCENT_RED
        self._voice_status_update(
            f"Similarity: {similarity:.2f} (threshold {threshold:.2f} - {'above' if above else 'BELOW'} it)", color
        )


if __name__ == "__main__":
    import time

    open_audio_calibration_window()
    time.sleep(300)
