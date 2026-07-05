"""Filters recorded audio down to segments matching the enrolled speaker's
voice, so MIMIR keeps listening to whoever said the wake word instead of
picking up other people talking nearby (the "crowded room" problem).

A no-op until a voice profile has been enrolled (see enroll_voice.py) -
config.speaker_verification.enabled can stay on by default since there's
nothing to gate against until then.
"""

from __future__ import annotations

import logging

import numpy as np

from config import config
from core.voice_profile import compute_embedding, load_profile

logger = logging.getLogger("mimir.speaker_verify")


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def filter_by_speaker(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Keep only the parts of `audio` that match the enrolled speaker.

    Splits the clip into ~1.6s overlapping windows (resemblyzer's own
    partial-utterance granularity), scores each window's similarity
    against the enrolled reference embedding, and concatenates only the
    windows that pass config.speaker_verification.similarity_threshold.

    Returns the audio unchanged if no profile is enrolled yet, or if the
    clip has nothing usable after resemblyzer's own preprocessing. Returns
    an empty array if the clip doesn't match the enrolled speaker at all -
    callers already treat an empty/silent result as "nothing was said".
    """
    reference = load_profile()
    if reference is None:
        return audio

    try:
        from resemblyzer import preprocess_wav

        from core.voice_profile import _get_encoder

        wav = preprocess_wav(audio, source_sr=sample_rate)
        if len(wav) == 0:
            return audio

        encoder = _get_encoder()
        _embed, partial_embeds, wav_slices = encoder.embed_utterance(
            wav, return_partials=True, rate=config.speaker_verification.rate
        )

        # Partial windows overlap by design (1.6s windows at ~1.3/sec), so
        # naively concatenating each passing window's raw samples would
        # duplicate the shared overlap and produce audio LONGER than the
        # original clip. Mark a per-sample keep-mask instead - each sample
        # is counted once no matter how many overlapping windows include
        # it - then extract via boolean indexing.
        threshold = config.speaker_verification.similarity_threshold
        keep_mask = np.zeros(len(wav), dtype=bool)
        n_passed = 0
        for embed, wav_slice in zip(partial_embeds, wav_slices):
            similarity = _cosine_similarity(embed, reference)
            if similarity >= threshold:
                n_passed += 1
                # embed_utterance() may have zero-padded its own internal
                # copy of wav to cover the last window - clip back to this
                # function's (unpadded) wav so the slice can't run past it.
                start = min(wav_slice.start, len(wav))
                stop = min(wav_slice.stop, len(wav))
                keep_mask[start:stop] = True

        if n_passed == 0:
            logger.info("Speaker verification: no segment matched the enrolled voice, dropping clip")
            return np.array([], dtype=np.float32)

        if n_passed < len(partial_embeds):
            logger.info(
                "Speaker verification: kept %d/%d segments matching the enrolled voice",
                n_passed,
                len(partial_embeds),
            )

        return wav[keep_mask]
    except Exception:
        logger.exception("Speaker verification failed; using unfiltered audio")
        return audio


if __name__ == "__main__":
    import sys

    from core.voice_profile import is_enrolled

    if not is_enrolled():
        print("No voice profile enrolled yet. Run enroll_voice.py first.")
        sys.exit(1)

    print("Recording... speak now.")
    from core import stt

    audio = stt.record_until_silence()
    print(f"Recorded {len(audio) / config.stt.sample_rate:.1f}s")
    filtered = filter_by_speaker(audio, config.stt.sample_rate)
    print(f"After speaker filtering: {len(filtered) / config.stt.sample_rate:.1f}s")
