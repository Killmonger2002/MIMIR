"""Text-to-speech via piper-tts, with lazy loading and sentence streaming."""

from __future__ import annotations

import gc
import logging
import re

import numpy as np
import sounddevice as sd

from config import config
from state import AppState

logger = logging.getLogger("mimir.tts")

_voice = None

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def get_voice():
    """Return the cached Piper voice, loading it on first call."""
    global _voice
    if _voice is None:
        from piper import PiperVoice

        logger.info("Loading Piper voice (%s)", config.tts.voice_model_path)
        _voice = PiperVoice.load(config.tts.voice_model_path, config_path=config.tts.voice_config_path)
    return _voice


def unload() -> None:
    """Release the cached Piper voice and force garbage collection."""
    global _voice
    if _voice is not None:
        logger.info("Unloading Piper voice")
        _voice = None
        gc.collect()


def _play_sentence(voice, sentence: str) -> None:
    """Synthesize one sentence and play it via sounddevice."""
    from piper.config import SynthesisConfig

    syn_config = SynthesisConfig(length_scale=1.0 / config.tts.speed)

    audio_chunks = []
    sample_rate = config.stt.sample_rate
    for chunk in voice.synthesize(sentence, syn_config=syn_config):
        audio_chunks.append(chunk.audio_int16_array)
        sample_rate = chunk.sample_rate

    if not audio_chunks:
        return

    audio = np.concatenate(audio_chunks)
    sd.play(audio, samplerate=sample_rate)
    sd.wait()


def speak(text: str, state: AppState) -> None:
    """Synthesize and play text, sentence by sentence, updating AppState.mode."""
    text = text.strip()
    if not text:
        return

    state.set_mode("speaking")
    try:
        voice = get_voice()
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
        for sentence in sentences:
            try:
                _play_sentence(voice, sentence)
            except Exception:
                logger.exception("Failed to play sentence: %r", sentence)
    except Exception:
        logger.exception("TTS failed for text: %r", text)
    finally:
        state.set_mode("idle")


if __name__ == "__main__":
    _state = AppState()
    speak("Hello. This is MIMIR speaking. Testing one, two, three.", _state)
