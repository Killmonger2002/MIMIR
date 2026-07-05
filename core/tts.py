"""Text-to-speech via piper-tts, with lazy loading, sentence streaming,
and barge-in (the user can interrupt MIMIR mid-sentence by talking)."""

from __future__ import annotations

import gc
import logging
import re
import threading

import numpy as np
import sounddevice as sd

from config import config
from core.audio_device import get_input_device
from state import AppState

logger = logging.getLogger("mimir.tts")

_voice = None
_voice_lock = threading.Lock()

_barge_in_vad = None
_barge_in_vad_lock = threading.Lock()

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

# A dedicated VAD frame size (matching core/stt.py's reasoning: 400 divides
# the block size used below evenly; openwakeword's default 480 does not).
_BARGE_IN_FRAME_SIZE = 400
_BARGE_IN_BLOCK_DURATION = 0.1  # seconds per monitoring chunk


def get_voice():
    """Return the cached Piper voice, loading it on first call.

    Double-checked locking: the startup greeting and any background
    prewarming could otherwise race here and trigger two redundant loads.
    """
    global _voice
    if _voice is None:
        with _voice_lock:
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


def _synthesize_sentence(voice, sentence: str) -> tuple[np.ndarray, int] | None:
    """Synthesize one sentence to a raw audio array + sample rate, without
    playing it - kept separate from playback so the NEXT sentence can be
    synthesized while the CURRENT one is still playing (see speak())."""
    from piper.config import SynthesisConfig

    syn_config = SynthesisConfig(
        length_scale=1.0 / config.tts.speed,
        noise_scale=config.tts.noise_scale,
        noise_w_scale=config.tts.noise_w_scale,
    )

    audio_chunks = []
    sample_rate = config.stt.sample_rate
    for chunk in voice.synthesize(sentence, syn_config=syn_config):
        audio_chunks.append(chunk.audio_int16_array)
        sample_rate = chunk.sample_rate

    if not audio_chunks:
        return None
    return np.concatenate(audio_chunks), sample_rate


def _safe_synthesize(voice, sentence: str) -> tuple[np.ndarray, int] | None:
    try:
        return _synthesize_sentence(voice, sentence)
    except Exception:
        logger.exception("Failed to synthesize sentence: %r", sentence)
        return None


def _get_barge_in_vad():
    """Return a dedicated VAD instance for barge-in monitoring - kept
    separate from core/stt.py's own VAD instance. The two never actually
    run at the same time in the current main.py loop (recording only
    happens while nothing is being spoken), but keeping them independent
    avoids relying on that non-overlap remaining true forever."""
    global _barge_in_vad
    if _barge_in_vad is None:
        with _barge_in_vad_lock:
            if _barge_in_vad is None:
                from openwakeword.vad import VAD

                _barge_in_vad = VAD()
    return _barge_in_vad


def _monitor_for_barge_in(stop_event: threading.Event, interrupted_event: threading.Event) -> None:
    """Runs on its own thread while speak() is playing: listens for the
    user starting to talk and, if detected, stops playback immediately.

    Requires several consecutive speech-probability hits above threshold
    (not a single frame) before declaring an interruption - the same
    debounce reasoning as wake-word detection, and doubly important here
    since MIMIR's own voice could otherwise leak into the mic (especially
    without a headset) and self-trigger a "barge-in" on its own speech.

    Known limitation: detection needs a few consecutive chunks to confirm,
    so the very first moment of the user's interrupting speech - before
    playback actually stops - isn't captured. The next recording starts
    fresh right after, which can clip the first word or so of what the
    user says. Fixing that would mean this monitor capturing and handing
    off audio across the interrupt boundary instead of just detecting it,
    a meaningfully bigger change than what's here now.
    """
    sample_rate = config.stt.sample_rate
    block_size = int(sample_rate * _BARGE_IN_BLOCK_DURATION)
    threshold = config.tts.barge_in_vad_threshold
    chunks_needed = max(1, config.tts.barge_in_consecutive_chunks)

    vad = _get_barge_in_vad()
    vad.reset_states()

    consecutive = 0
    try:
        with sd.InputStream(
            samplerate=sample_rate, channels=1, dtype="int16", blocksize=block_size, device=get_input_device()
        ) as stream:
            while not stop_event.is_set():
                block, _overflowed = stream.read(block_size)
                speech_prob = vad.predict(block.flatten().astype(np.int16), frame_size=_BARGE_IN_FRAME_SIZE)
                if speech_prob > threshold:
                    consecutive += 1
                    if consecutive >= chunks_needed:
                        logger.info(
                            "Barge-in detected (%d consecutive chunks above %.2f)", consecutive, threshold
                        )
                        interrupted_event.set()
                        sd.stop()
                        return
                else:
                    consecutive = 0
    except Exception:
        logger.exception("Barge-in monitor failed; continuing without interrupt support for this utterance")


def speak(text: str, state: AppState, allow_interrupt: bool = True) -> bool:
    """Synthesize and play text, sentence by sentence, updating AppState.mode.

    Pipelines synthesis and playback: while one sentence plays, the next
    is already being synthesized in the background of this same call
    rather than the two happening strictly one after another - this cuts
    perceived latency on multi-sentence responses.

    If allow_interrupt is True (the default) and config.tts.barge_in_enabled
    is set, a background thread listens for the user starting to talk and
    stops playback mid-sentence if so - set allow_interrupt=False for
    announcements where there's nothing meaningful to do with an
    interruption (the startup greeting, the shutdown announcement).

    Returns True if the user interrupted mid-speech, False if playback
    ran to completion (or there was nothing to say).
    """
    text = text.strip()
    if not text:
        return False

    state.set_mode("speaking")
    interrupted_event = threading.Event()
    monitor_stop_event = threading.Event()
    monitor_thread = None

    if allow_interrupt and config.tts.barge_in_enabled:
        monitor_thread = threading.Thread(
            target=_monitor_for_barge_in, args=(monitor_stop_event, interrupted_event), daemon=True
        )
        monitor_thread.start()

    try:
        voice = get_voice()
        sentences = [s for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]
        if not sentences:
            return False

        next_audio = _safe_synthesize(voice, sentences[0])

        for i, sentence in enumerate(sentences):
            if interrupted_event.is_set():
                break

            current_audio = next_audio
            next_audio = None

            if current_audio is not None:
                audio, sample_rate = current_audio
                try:
                    sd.play(audio, samplerate=sample_rate)
                except Exception:
                    logger.exception("Failed to start playback for sentence: %r", sentence)
                    current_audio = None

            # Synthesize the next sentence while the current one plays
            # (sd.play() above is non-blocking) instead of waiting first.
            if i + 1 < len(sentences) and not interrupted_event.is_set():
                next_audio = _safe_synthesize(voice, sentences[i + 1])

            if current_audio is not None:
                sd.wait()
    except Exception:
        logger.exception("TTS failed for text: %r", text)
    finally:
        monitor_stop_event.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=1)
        state.set_mode("idle")

    return interrupted_event.is_set()


if __name__ == "__main__":
    _state = AppState()
    print("Speaking a long response - try talking over it to test barge-in...")
    interrupted = speak(
        "Hello. This is MIMIR speaking. Testing one, two, three. "
        "This sentence is here to give you enough time to interrupt.",
        _state,
    )
    print("Interrupted:" if interrupted else "Finished without interruption:", interrupted)
