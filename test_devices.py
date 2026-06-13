"""Test every input device for 1.5s and report which ones capture real audio.

Run in a normal terminal:
    venv\\Scripts\\activate
    python test_devices.py

Speak continuously while this runs through the list.
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000

devices = sd.query_devices()
hostapis = sd.query_hostapis()
print(f"Default input index: {sd.default.device[0]}\n")

for idx, dev in enumerate(devices):
    if dev["max_input_channels"] < 1:
        continue
    api_name = hostapis[dev["hostapi"]]["name"]
    try:
        rate = int(dev["default_samplerate"])
        audio = sd.rec(
            int(1.5 * rate),
            samplerate=rate,
            channels=1,
            dtype="int16",
            device=idx,
        )
        sd.wait()
        flat = audio.flatten()
        mean_abs = np.abs(flat).mean()
        marker = "  <-- DEFAULT" if idx == sd.default.device[0] else ""
        print(f"[{idx:2d}] ({api_name:12s}) rate={rate:6d} mean_abs={mean_abs:7.1f}  {dev['name']}{marker}")
    except Exception as exc:
        print(f"[{idx:2d}] ({api_name:12s}) ERROR: {exc}  {dev['name']}")
