"""Speech-to-text via faster-whisper, with lazy loading and simple VAD.

The model is loaded on first use and cached as a module-level singleton.
The lifecycle manager calls unload() to free memory after idle timeout.
"""

from __future__ import annotations

import gc
import logging

import numpy as np
import sounddevice as sd

from config import config
from core.audio_device import get_input_device

logger = logging.getLogger("mimir.stt")

_model = None


def get_model():
    """Return the cached faster-whisper model, loading it on first call."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        logger.info("Loading Whisper model (%s)", config.stt.model_size)
        _model = WhisperModel(
            config.stt.model_size,
            device=config.stt.device,
            compute_type=config.stt.compute_type,
        )
    return _model


def unload() -> None:
    """Release the cached Whisper model and force garbage collection."""
    global _model
    if _model is not None:
        logger.info("Unloading Whisper model")
        _model = None
        gc.collect()


def record_until_silence() -> np.ndarray:
    """Record audio from the default input device until ~1s of silence.

    Uses a simple amplitude-threshold VAD: recording stops once the
    trailing window of audio has been below silence_threshold for
    silence_duration_sec seconds.
    """
    sample_rate = config.stt.sample_rate
    block_duration = 0.1  # seconds per chunk
    block_size = int(sample_rate * block_duration)
    silence_blocks_needed = max(1, int(config.stt.silence_duration_sec / block_duration))

    chunks: list[np.ndarray] = []
    silent_count = 0
    has_spoken = False

    with sd.InputStream(
        samplerate=sample_rate, channels=1, dtype="int16", blocksize=block_size, device=get_input_device()
    ) as stream:
        while True:
            block, _overflowed = stream.read(block_size)
            chunks.append(block.copy())

            amplitude = np.abs(block).mean()
            if amplitude > config.stt.silence_threshold:
                has_spoken = True
                silent_count = 0
            elif has_spoken:
                silent_count += 1
                if silent_count >= silence_blocks_needed:
                    break

    audio = np.concatenate(chunks).flatten()
    return audio.astype(np.float32) / 32768.0


def transcribe(audio_array: np.ndarray) -> str:
    """Transcribe a float32 mono audio array at the configured sample rate."""
    model = get_model()
    segments, _info = model.transcribe(audio_array, language="en")
    text = " ".join(segment.text.strip() for segment in segments)
    return text.strip()


if __name__ == "__main__":
    print("Recording until silence... speak now.")
    audio = record_until_silence()
    print("Transcribing...")
    print("Transcript:", transcribe(audio))
