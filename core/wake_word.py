"""Continuous wake-word detection using openWakeWord.

Runs on its own daemon thread, started from main.py. NEVER unloads -
this listener runs for the entire lifetime of the process.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

from config import config
from core.audio_device import get_input_device
from state import AppState

logger = logging.getLogger("mimir.wake_word")

_CHUNK_SIZE = 1280  # openWakeWord expects 80ms chunks at 16kHz
_SAMPLE_RATE = 16000


class WakeWordListener:
    """Continuously listens for the configured wake phrase on a daemon thread."""

    def __init__(self, state: AppState, on_detected: Callable[[], None]) -> None:
        self._state = state
        self._on_detected = on_detected
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background listening thread."""
        self._thread = threading.Thread(target=self._run, name="wake-word-listener", daemon=True)
        self._thread.start()
        logger.info("Wake word listener started (phrase=%r)", config.wake_word.phrase)

    def stop(self) -> None:
        """Signal the listening thread to stop."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        try:
            from openwakeword.model import Model

            if config.wake_word.model_path:
                oww_model = Model(
                    wakeword_models=[config.wake_word.model_path],
                    inference_framework="onnx",
                )
            else:
                oww_model = Model(inference_framework="onnx")
        except Exception:
            logger.exception("Failed to initialize openWakeWord model")
            return

        try:
            with sd.InputStream(
                samplerate=_SAMPLE_RATE, channels=1, dtype="int16", blocksize=_CHUNK_SIZE, device=get_input_device()
            ) as stream:
                while not self._stop_event.is_set():
                    audio_chunk, _overflowed = stream.read(_CHUNK_SIZE)
                    audio_data = audio_chunk.flatten().astype(np.int16)

                    predictions = oww_model.predict(audio_data)

                    if self._state.is_paused():
                        continue

                    for model_name, score in predictions.items():
                        if score > config.wake_word.sensitivity:
                            logger.debug("Wake word detected (model=%s, score=%.2f)", model_name, score)
                            self._on_detected()
                            break
        except Exception:
            logger.exception("Wake word listener crashed")


if __name__ == "__main__":
    import time

    _state = AppState()

    def _on_wake() -> None:
        print("Wake word detected!")

    listener = WakeWordListener(_state, _on_wake)
    listener.start()
    print("Listening for wake word... (Ctrl+C to stop)")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        listener.stop()
