"""Speech-to-text via faster-whisper, with lazy loading and Silero VAD.

The model is loaded on first use and cached as a module-level singleton.
The lifecycle manager calls unload() to free memory after idle timeout.
"""

from __future__ import annotations

import gc
import logging
import threading

import numpy as np
import sounddevice as sd

from config import config
from core.audio_device import get_input_device

logger = logging.getLogger("mimir.stt")

_model = None
_model_lock = threading.Lock()

_vad = None
_vad_lock = threading.Lock()

# openwakeword.vad.VAD's own suggested default (480) doesn't evenly divide
# the 1600-sample (0.1s @ 16kHz) blocks read below (1600/480=3.33) - 400
# does (1600/400=4 exactly), avoiding an uneven leftover chunk in VAD's
# internal per-frame slicing loop.
_VAD_FRAME_SIZE = 400


def _get_vad():
    """Return the cached Silero VAD wrapper (onnxruntime-only, bundled with
    openwakeword - no torch dependency), loading it on first call."""
    global _vad
    if _vad is None:
        with _vad_lock:
            if _vad is None:
                from openwakeword.vad import VAD

                logger.info("Loading Silero VAD")
                _vad = VAD()
    return _vad


def get_model():
    """Return the cached faster-whisper model, loading it on first call.

    Double-checked locking: main.py prewarms this on a background thread
    at startup, so a command arriving before that finishes could otherwise
    race with it here and trigger two redundant loads.
    """
    global _model
    if _model is None:
        with _model_lock:
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


def record_until_silence(max_wait_sec: float | None = None) -> np.ndarray:
    """Record audio from the default input device until ~1s of silence.

    Uses Silero VAD (via openwakeword's bundled onnxruntime wrapper, no
    torch needed) to detect speech vs. silence per block, instead of a
    raw amplitude threshold - amplitude alone can't distinguish speech
    from equally-loud background noise, so a noisy room would never
    register as "gone silent". Recording stops once the trailing window
    has scored below vad_speech_threshold for silence_duration_sec.

    If max_wait_sec is set and the user hasn't started speaking within
    that window, give up and return what was recorded (used for yes/no
    confirmation replies, where waiting forever would hang the command
    loop if the user walks away).
    """
    sample_rate = config.stt.sample_rate
    block_duration = 0.1  # seconds per chunk
    block_size = int(sample_rate * block_duration)
    silence_blocks_needed = max(1, int(config.stt.silence_duration_sec / block_duration))
    max_wait_blocks = None if max_wait_sec is None else max(1, int(max_wait_sec / block_duration))
    vad_threshold = config.stt.vad_speech_threshold

    vad = _get_vad()
    vad.reset_states()  # per-utterance: LSTM hidden state must not leak across calls

    chunks: list[np.ndarray] = []
    silent_count = 0
    has_spoken = False
    blocks_read = 0

    with sd.InputStream(
        samplerate=sample_rate, channels=1, dtype="int16", blocksize=block_size, device=get_input_device()
    ) as stream:
        while True:
            block, _overflowed = stream.read(block_size)
            chunks.append(block.copy())
            blocks_read += 1

            speech_prob = vad.predict(block.flatten().astype(np.int16), frame_size=_VAD_FRAME_SIZE)
            if speech_prob > vad_threshold:
                has_spoken = True
                silent_count = 0
            elif has_spoken:
                silent_count += 1
                if silent_count >= silence_blocks_needed:
                    break
            elif max_wait_blocks is not None and blocks_read >= max_wait_blocks:
                logger.debug("No speech within %.1fs, giving up on this recording", max_wait_sec)
                break

    audio = np.concatenate(chunks).flatten()
    audio = audio.astype(np.float32) / 32768.0

    if config.stt.denoise_enabled:
        try:
            import noisereduce as nr

            audio = nr.reduce_noise(y=audio, sr=sample_rate, stationary=False)
        except Exception:
            logger.exception("Noise reduction failed; using raw audio")

    if config.speaker_verification.enabled:
        from core.speaker_verify import filter_by_speaker

        audio = filter_by_speaker(audio, sample_rate)

    return audio


def transcribe_with_confidence(audio_array: np.ndarray) -> tuple[str, float]:
    """Transcribe audio and return (text, confidence).

    Confidence is the mean avg_logprob across Whisper segments: ~-0.2 to
    -0.4 for clean speech, below roughly -0.8 for garbled/noisy audio.
    Returns 0.0 (fully confident) for empty audio so silence never trips
    the low-confidence confirmation path.
    """
    model = get_model()

    initial_prompt = None
    if config.stt.vocabulary_hint_enabled:
        from core.vocabulary import build_vocabulary_prompt

        initial_prompt = build_vocabulary_prompt() or None

    segments, _info = model.transcribe(audio_array, language="en", initial_prompt=initial_prompt)
    segment_list = list(segments)
    text = " ".join(segment.text.strip() for segment in segment_list).strip()
    if not segment_list:
        return "", 0.0
    confidence = sum(s.avg_logprob for s in segment_list) / len(segment_list)
    return text, confidence


def transcribe(audio_array: np.ndarray) -> str:
    """Transcribe a float32 mono audio array at the configured sample rate."""
    return transcribe_with_confidence(audio_array)[0]


def contains_speech(audio: np.ndarray) -> bool:
    """Return True if any part of a recorded clip crossed the silence
    threshold - i.e. the user actually said something, as opposed to the
    clip being pure silence. Used to tell a genuine (if brief) utterance
    apart from a follow-up listening window that simply timed out."""
    if audio.size == 0:
        return False
    peak = float(np.abs(audio).max()) * 32768.0
    return peak > config.stt.silence_threshold


if __name__ == "__main__":
    print("Recording until silence... speak now.")
    audio = record_until_silence()
    print("Transcribing...")
    print("Transcript:", transcribe(audio))
