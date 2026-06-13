"""Resolve which audio input device MIMIR should use.

Built-in laptop mic arrays can fail silently (return near-zero audio) while
working fine via Sound Recorder, e.g. when a Bluetooth headset mic is the
only functional input. `config.audio.input_device_name` lets the user pin
a specific device by name substring; this module resolves that to a
sounddevice device index, preferring WASAPI for Bluetooth/USB headsets.
"""

from __future__ import annotations

import logging

import sounddevice as sd

from config import config

logger = logging.getLogger("mimir.audio_device")

_resolved_device: int | None | str = "unresolved"


def get_input_device() -> int | None:
    """Return the sounddevice input device index to use, or None for default."""
    global _resolved_device
    if _resolved_device != "unresolved":
        return _resolved_device  # type: ignore[return-value]

    name_filter = config.audio.input_device_name.strip().lower()
    if not name_filter:
        _resolved_device = None
        return None

    devices = sd.query_devices()
    hostapis = sd.query_hostapis()

    candidates = []
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] < 1:
            continue
        if name_filter not in dev["name"].lower():
            continue
        api_name = hostapis[dev["hostapi"]]["name"]
        candidates.append((idx, api_name, dev))

    if not candidates:
        logger.warning("No input device matching %r found; using system default", name_filter)
        _resolved_device = None
        return None

    # Prefer WASAPI (most reliable for Bluetooth headset mics on Windows).
    for idx, api_name, dev in candidates:
        if "WASAPI" in api_name:
            logger.info("Using input device [%d] %s (%s)", idx, dev["name"], api_name)
            _resolved_device = idx
            return idx

    idx, api_name, dev = candidates[0]
    logger.info("Using input device [%d] %s (%s)", idx, dev["name"], api_name)
    _resolved_device = idx
    return idx
