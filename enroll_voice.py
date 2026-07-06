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

This is also available as a UI wizard in MIMIR's Settings window (see
ui/voice_training_window.py) if you'd rather not use the terminal - both
call the exact same core.voice_profile.enroll_from_samples() underneath.
"""

from __future__ import annotations

import sys

from core.voice_profile import ENROLLMENT_PHRASES, MIN_ENROLLMENT_SAMPLES


def main() -> None:
    from core import stt
    from core.voice_profile import enroll_from_samples

    print(f"Recording {len(ENROLLMENT_PHRASES)} short samples of your voice.")
    print("Speak clearly, in a normal voice, ideally somewhere quiet.\n")

    raw_samples = []
    for i, (phrase, guidance) in enumerate(ENROLLMENT_PHRASES, start=1):
        print(f"({guidance})")
        input(f"[{i}/{len(ENROLLMENT_PHRASES)}] Press Enter, then say: {phrase!r}")
        print("Recording...")
        audio = stt.record_until_silence()
        duration = len(audio) / stt.config.stt.sample_rate
        if duration < 0.5:
            print(f"  That was very short ({duration:.1f}s) - let's try again.")
            continue
        print(f"  Captured {duration:.1f}s.")
        raw_samples.append(audio)

    if len(raw_samples) < MIN_ENROLLMENT_SAMPLES:
        print("\nNot enough usable samples were recorded. Run this script again.")
        sys.exit(1)

    print("\nProcessing samples...")
    try:
        embedding = enroll_from_samples(raw_samples, stt.config.stt.sample_rate)
    except ValueError as exc:
        print(str(exc))
        sys.exit(1)

    print(f"\nVoice profile saved from {len(raw_samples)} samples.")
    print("Restart MIMIR for speaker verification to take effect.")

    # Sanity check: one held-out sample's similarity against the profile
    # just saved, so you have a concrete number to compare against
    # speaker_verification.similarity_threshold in config.yaml.
    input("\nOptional check - press Enter, then say one more short phrase:")
    print("Recording...")
    from resemblyzer import preprocess_wav

    from core.voice_profile import _get_encoder

    check_audio = stt.record_until_silence()
    check_wav = preprocess_wav(check_audio, source_sr=stt.config.stt.sample_rate)
    if len(check_wav) == 0:
        print("That was too quiet to check.")
        return
    check_embed = _get_encoder().embed_utterance(check_wav)

    import numpy as np

    similarity = float(np.dot(check_embed, embedding) / (np.linalg.norm(check_embed) * np.linalg.norm(embedding)))
    print(f"Similarity to your new profile: {similarity:.2f}")
    print(f"(current threshold is {stt.config.speaker_verification.similarity_threshold:.2f} - "
          f"{'above' if similarity >= stt.config.speaker_verification.similarity_threshold else 'BELOW'} it)")


if __name__ == "__main__":
    main()
