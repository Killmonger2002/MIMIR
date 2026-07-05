"""One-time voice enrollment for speaker verification.

Run this directly in a normal terminal (not through an automation/sandbox
session) with a working microphone, in a reasonably quiet spot:

    venv\\Scripts\\activate
    python enroll_voice.py

Records a few short samples of your voice, averages them into a single
reference embedding (via resemblyzer), and saves it to
models/voice_profile/reference.npy. Once enrolled,
config.speaker_verification.enabled (on by default) makes MIMIR filter
out audio that doesn't match your voice during recording - the fix for
"stay on the same person who said hey jarvis" in a noisy/crowded room.

The embedding is a 256-number vector, not audio - it can't be played back
or turned into speech, but it's still tied to your voice, so it's saved
under models/ (already gitignored, never committed, never sent anywhere).

Re-run this script any time to re-enroll (e.g. if verification feels too
strict or too loose - see also speaker_verification.similarity_threshold
in config.yaml).
"""

from __future__ import annotations

import sys

import numpy as np

_N_SAMPLES = 4
_PHRASES = [
    "Hey Mimir, this is my voice.",
    "The quick brown fox jumps over the lazy dog.",
    "Please open my documents folder.",
    "This is a sample of how I sound when I talk.",
]


def main() -> None:
    from core import stt
    from core.voice_profile import save_profile
    from resemblyzer import preprocess_wav

    print(f"Recording {_N_SAMPLES} short samples of your voice.")
    print("Speak clearly, in a normal voice, ideally somewhere quiet.\n")

    raw_samples: list[np.ndarray] = []
    for i, phrase in enumerate(_PHRASES[:_N_SAMPLES], start=1):
        input(f"[{i}/{_N_SAMPLES}] Press Enter, then say: {phrase!r}")
        print("Recording...")
        audio = stt.record_until_silence()
        duration = len(audio) / stt.config.stt.sample_rate
        if duration < 0.5:
            print(f"  That was very short ({duration:.1f}s) - let's try again.")
            continue
        print(f"  Captured {duration:.1f}s.")
        raw_samples.append(audio)

    if len(raw_samples) < 2:
        print("\nNot enough usable samples were recorded. Run this script again.")
        sys.exit(1)

    print("\nProcessing samples...")
    from core.voice_profile import _get_encoder

    preprocessed = [preprocess_wav(a, source_sr=stt.config.stt.sample_rate) for a in raw_samples]
    preprocessed = [w for w in preprocessed if len(w) > 0]
    if len(preprocessed) < 2:
        print("Samples were too quiet/short after processing. Run this script again in a quieter spot.")
        sys.exit(1)

    encoder = _get_encoder()
    embedding = encoder.embed_speaker(preprocessed)
    save_profile(embedding)

    print(f"\nVoice profile saved from {len(preprocessed)} samples.")
    print("Restart MIMIR for speaker verification to take effect.")

    # Sanity check: one held-out sample's similarity against the profile
    # just saved, so you have a concrete number to compare against
    # speaker_verification.similarity_threshold in config.yaml.
    input("\nOptional check - press Enter, then say one more short phrase:")
    print("Recording...")
    check_audio = stt.record_until_silence()
    check_wav = preprocess_wav(check_audio, source_sr=stt.config.stt.sample_rate)
    if len(check_wav) == 0:
        print("That was too quiet to check.")
        return
    check_embed = encoder.embed_utterance(check_wav)
    similarity = float(np.dot(check_embed, embedding) / (np.linalg.norm(check_embed) * np.linalg.norm(embedding)))
    print(f"Similarity to your new profile: {similarity:.2f}")
    print(f"(current threshold is {stt.config.speaker_verification.similarity_threshold:.2f} - "
          f"{'above' if similarity >= stt.config.speaker_verification.similarity_threshold else 'BELOW'} it)")


if __name__ == "__main__":
    main()
