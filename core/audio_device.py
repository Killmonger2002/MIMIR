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


def reset_cache() -> None:
    """Drop the cached device resolution so the next get_input_device()
    call re-reads config.audio.input_device_name. Call this after changing
    the device from the Settings UI so the new device takes effect without
    restarting MIMIR."""
    global _resolved_device
    _resolved_device = "unresolved"


def list_input_devices() -> list[dict]:
    """Return every input-capable device as
    {"name": str, "hostapi": str, "is_default": bool}, WASAPI-preferred
    duplicates first - the same devices/order the Settings dropdown and
    the calibration wizard's device picker show."""
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    try:
        default_idx = sd.default.device[0]
    except Exception:
        default_idx = None

    result = []
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] < 1:
            continue
        api_name = hostapis[dev["hostapi"]]["name"]
        result.append({"name": dev["name"], "hostapi": api_name, "is_default": idx == default_idx})

    result.sort(key=lambda d: 0 if "WASAPI" in d["hostapi"] else 1)
    return result
