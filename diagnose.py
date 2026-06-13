"""Layered diagnostic script for MIMIR's audio pipeline.

Run this directly in a normal terminal (not through an automation/sandbox
session) with a working microphone:

    venv\\Scripts\\activate
    python diagnose.py

It runs through several layers and reports pass/fail for each:

  1. List audio devices and confirm a default input device exists.
  2. Record 3 seconds of raw audio and report min/max/mean amplitude
     (confirms the mic is actually capturing sound).
  3. Listen for 15 seconds and print live openWakeWord scores for all
     bundled models (say "hey jarvis", "alexa", etc. during this time).
  4. Record 3 seconds of speech and run it through faster-whisper to
     confirm transcription works.
"""

from __future__ import annotations

import sys

import numpy as np
import sounddevice as sd

from core.audio_device import get_input_device

SAMPLE_RATE = 16000
DEVICE = get_input_device()
print(f"Resolved input device: {DEVICE}")
if DEVICE is not None:
    print(f"  -> {sd.query_devices(DEVICE)}")


def layer1_devices() -> bool:
    print("\n=== Layer 1: Audio devices ===")
    try:
        devices = sd.query_devices()
        default_in, default_out = sd.default.device
        print(f"Default input device index: {default_in}")
        print(f"Default input device: {devices[default_in]['name']}")
        print(f"Default output device: {devices[default_out]['name']}")
        return True
    except Exception as exc:
        print(f"FAILED: {exc}")
        return False


def layer2_raw_levels() -> bool:
    print("\n=== Layer 2: Raw mic levels (3s) - speak/make noise now ===")
    try:
        audio = sd.rec(int(3 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16", device=DEVICE)
        sd.wait()
        flat = audio.flatten()
        print(f"min={flat.min()} max={flat.max()} mean_abs={np.abs(flat).mean():.1f}")
        if np.abs(flat).mean() < 5:
            print("WARNING: audio looks silent (mean_abs < 5). Mic may not be capturing.")
            return False
        print("OK: mic is capturing audio.")
        return True
    except Exception as exc:
        print(f"FAILED: {exc}")
        return False


def layer3_wakeword() -> bool:
    print("\n=== Layer 3: openWakeWord live scores (15s) ===")
    print("Say 'hey jarvis', 'alexa', 'hey mycroft', etc. during this window.")
    try:
        from openwakeword.model import Model

        oww_model = Model(inference_framework="onnx")
        print("Loaded models:", list(oww_model.models.keys()))

        chunk_size = 1280
        n_chunks = int(15 * SAMPLE_RATE / chunk_size)
        max_scores: dict[str, float] = {}

        with sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=chunk_size, device=DEVICE
        ) as stream:
            for _ in range(n_chunks):
                audio_chunk, _ = stream.read(chunk_size)
                audio_data = audio_chunk.flatten().astype(np.int16)
                predictions = oww_model.predict(audio_data)
                for name, score in predictions.items():
                    max_scores[name] = max(max_scores.get(name, 0.0), float(score))

        print("Max scores observed:", {k: round(v, 3) for k, v in max_scores.items()})
        if max(max_scores.values()) > 0.5:
            print("OK: at least one wake word model fired (score > 0.5).")
            return True
        else:
            print("WARNING: no model exceeded 0.5. Try speaking louder/closer to mic.")
            return False
    except Exception as exc:
        print(f"FAILED: {exc}")
        return False


def layer4_whisper() -> bool:
    print("\n=== Layer 4: Whisper transcription (3s) - say a short sentence now ===")
    try:
        from faster_whisper import WhisperModel

        print("Loading whisper model (tiny.en)...")
        model = WhisperModel("tiny.en", device="cpu", compute_type="int8")

        audio = sd.rec(int(3 * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=DEVICE)
        sd.wait()
        flat = audio.flatten()

        segments, _info = model.transcribe(flat, language="en")
        text = " ".join(seg.text for seg in segments).strip()
        print(f"Transcribed text: {text!r}")
        if text:
            print("OK: whisper produced text.")
            return True
        else:
            print("WARNING: whisper returned empty text.")
            return False
    except Exception as exc:
        print(f"FAILED: {exc}")
        return False


if __name__ == "__main__":
    results = {
        "devices": layer1_devices(),
        "raw_levels": layer2_raw_levels(),
        "wakeword": layer3_wakeword(),
        "whisper": layer4_whisper(),
    }

    print("\n=== Summary ===")
    for name, ok in results.items():
        print(f"{name}: {'PASS' if ok else 'FAIL/WARN'}")

    sys.exit(0)
