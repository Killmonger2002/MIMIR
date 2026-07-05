"""Enrolled speaker voice-print: computes and persists a reference voice
embedding (via resemblyzer) so core/speaker_verify.py can tell the
enrolled user's voice apart from other people talking nearby.

The embedding is a 256-dim float32 vector, not raw audio - it can't be
played back or turned back into speech, but it is still biometric data
tied to one person's voice, so it's stored under models/ (gitignored,
machine-local) and never transmitted anywhere.
"""

from __future__ import annotations

import logging
import os
import threading

import numpy as np

logger = logging.getLogger("mimir.voice_profile")

_PROFILE_PATH = "models/voice_profile/reference.npy"

_encoder = None
_encoder_lock = threading.Lock()

_cached_profile: np.ndarray | None = None
_profile_loaded = False  # separate flag, not None-checking: a numpy array
# compared against a sentinel value raises "truth value of an array is
# ambiguous" the moment a profile is actually loaded, so the cache state
# can't be encoded in _cached_profile's value alone.
_profile_lock = threading.Lock()


def _get_encoder():
    """Return the cached resemblyzer VoiceEncoder, loading it on first call."""
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                from resemblyzer import VoiceEncoder

                logger.info("Loading speaker-verification voice encoder")
                _encoder = VoiceEncoder()
    return _encoder


def compute_embedding(audio: np.ndarray, sample_rate: int) -> np.ndarray | None:
    """Compute a single speaker embedding for one clip of audio.

    Returns None if the clip has nothing usable left after resemblyzer's
    own silence-trimming (e.g. the clip was essentially silent).
    """
    from resemblyzer import preprocess_wav

    wav = preprocess_wav(audio, source_sr=sample_rate)
    if len(wav) == 0:
        return None
    return _get_encoder().embed_utterance(wav)


def save_profile(embedding: np.ndarray) -> None:
    """Persist the enrolled reference embedding to disk."""
    global _cached_profile, _profile_loaded
    os.makedirs(os.path.dirname(_PROFILE_PATH), exist_ok=True)
    np.save(_PROFILE_PATH, embedding)
    with _profile_lock:
        _cached_profile = embedding
        _profile_loaded = True
    logger.info("Saved voice profile to %s", _PROFILE_PATH)


def load_profile() -> np.ndarray | None:
    """Return the enrolled reference embedding, or None if never enrolled.

    Cached after first successful load for the process lifetime - re-run
    enroll_voice.py and restart MIMIR to pick up a re-enrollment.
    """
    global _cached_profile, _profile_loaded
    if not _profile_loaded:
        with _profile_lock:
            if not _profile_loaded:
                if os.path.exists(_PROFILE_PATH):
                    _cached_profile = np.load(_PROFILE_PATH)
                    logger.info("Loaded voice profile from %s", _PROFILE_PATH)
                _profile_loaded = True
    return _cached_profile


def is_enrolled() -> bool:
    return load_profile() is not None


if __name__ == "__main__":
    profile = load_profile()
    if profile is None:
        print("No voice profile enrolled yet. Run enroll_voice.py first.")
    else:
        print(f"Voice profile loaded: shape={profile.shape}, norm={np.linalg.norm(profile):.3f}")
