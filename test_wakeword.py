"""Standalone wake-word test harness.

Run this directly (with MIMIR's main app NOT running) to see live
prediction scores from openWakeWord for all bundled models. Say
"alexa", "hey jarvis", "hey mycroft", etc. and watch the scores.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd
from openwakeword.model import Model

_CHUNK_SIZE = 1280
_SAMPLE_RATE = 16000

print("Default input device:", sd.query_devices(sd.default.device[0]))

oww_model = Model(inference_framework="onnx")
print("Loaded models:", list(oww_model.models.keys()))

print("Listening... speak a wake word (Ctrl+C to stop)")

with sd.InputStream(samplerate=_SAMPLE_RATE, channels=1, dtype="int16", blocksize=_CHUNK_SIZE) as stream:
    try:
        while True:
            audio_chunk, overflowed = stream.read(_CHUNK_SIZE)
            audio_data = audio_chunk.flatten().astype(np.int16)

            volume = np.abs(audio_data).mean()

            predictions = oww_model.predict(audio_data)
            scores = {k: round(float(v), 3) for k, v in predictions.items()}
            top = max(scores.values())

            if top > 0.05 or volume > 200:
                print(f"vol={volume:7.1f}  scores={scores}")
    except KeyboardInterrupt:
        pass
