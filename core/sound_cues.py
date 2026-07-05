"""Short procedural sound cues, used instead of spoken phrases for signals
where synthesizing and speaking a word would add real, perceptible delay
to every single interaction (e.g. announcing "Listening" before every
command, via Piper TTS, easily costs 500ms-1s that the user just has to
wait through before they can even start talking).
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd

from config import config

_SAMPLE_RATE = 22050  # matches Piper's typical output rate; independent of mic sample rate


def _tone(freq: float, duration: float, sample_rate: int = _SAMPLE_RATE) -> np.ndarray:
    """A short sine tone with a quick fade in/out to avoid audible clicks."""
    n = int(duration * sample_rate)
    t = np.linspace(0, duration, n, endpoint=False)
    wave = np.sin(2 * np.pi * freq * t)
    fade_len = max(1, int(0.01 * sample_rate))
    fade = np.ones(n)
    fade[:fade_len] = np.linspace(0, 1, fade_len)
    fade[-fade_len:] = np.linspace(1, 0, fade_len)
    return (wave * fade * 0.2).astype(np.float32)  # 0.2 headroom, avoid clipping


def play_listening_cue() -> None:
    """A quick rising two-tone chime, played instead of speaking the word
    "Listening" - signals the user can start talking without the ~500ms+
    delay of synthesizing and playing a spoken word first."""
    if not config.audio.listening_cue_enabled:
        return
    tone = np.concatenate([_tone(880, 0.06), _tone(1175, 0.07)])
    sd.play(tone, samplerate=_SAMPLE_RATE)
    sd.wait()


if __name__ == "__main__":
    print("Playing listening cue...")
    play_listening_cue()
