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

        # Per-model consecutive-chunk counters, not a single shared scalar:
        # the loaded Model covers multiple pretrained wakewords at once
        # (alexa, hey_jarvis, hey_mycroft, etc. when model_path is unset),
        # and a shared counter would let chunk N's high score on model A
        # and chunk N+1's high score on model B incorrectly accumulate as
        # one model's 2 consecutive hits.
        activation_counts: dict[str, int] = {}
        min_chunks = max(1, config.wake_word.min_activation_chunks)

        try:
            with sd.InputStream(
                samplerate=_SAMPLE_RATE, channels=1, dtype="int16", blocksize=_CHUNK_SIZE, device=get_input_device()
            ) as stream:
                while not self._stop_event.is_set():
                    audio_chunk, overflowed = stream.read(_CHUNK_SIZE)

                    if overflowed:
                        # This thread just spent a whole command cycle (STT,
                        # LLM, TTS) blocked inside _on_detected() without
                        # reading from this stream, so the OS buffer backed
                        # up. That backlog is stale by the time we get to it
                        # - it may replay the tail of the user's last command,
                        # or (without a headset) actual acoustic bleed of
                        # MIMIR's own TTS output into the mic. Drop it and
                        # reset the model's streaming buffers rather than
                        # feed a time-discontinuous chunk into them.
                        logger.debug("Input overflow detected; discarding stale backlog")
                        while stream.read_available >= _CHUNK_SIZE:
                            stream.read(_CHUNK_SIZE)
                        oww_model.reset()
                        activation_counts.clear()
                        continue

                    audio_data = audio_chunk.flatten().astype(np.int16)
                    predictions = oww_model.predict(audio_data)

                    # is_paused(): user explicitly paused MIMIR via hotkey/tray.
                    # mode == "speaking": defensive - the command cycle
                    # currently runs synchronously on this same thread so
                    # this can't fire mid-cycle today, but skip anyway in
                    # case that threading model ever changes.
                    if self._state.is_paused() or self._state.get_mode() == "speaking":
                        activation_counts.clear()
                        continue

                    for model_name, score in predictions.items():
                        if score > config.wake_word.sensitivity:
                            activation_counts[model_name] = activation_counts.get(model_name, 0) + 1
                            if activation_counts[model_name] >= min_chunks:
                                logger.debug(
                                    "Wake word detected (model=%s, score=%.2f, consecutive_chunks=%d)",
                                    model_name,
                                    score,
                                    activation_counts[model_name],
                                )
                                activation_counts.clear()
                                self._on_detected()
                                break
                        else:
                            activation_counts[model_name] = 0
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
