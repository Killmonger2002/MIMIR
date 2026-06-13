"""Configuration loader for MIMIR.

Loads config.yaml once at startup into a Config object whose attributes
mirror the YAML structure, with sensible defaults for any missing keys.
"""

from __future__ import annotations

import os
from typing import Any

import yaml


DEFAULTS: dict[str, Any] = {
    "audio": {
        # Substring match (case-insensitive) against input device names,
        # preferring WASAPI devices. Empty string = use system default.
        "input_device_name": "",
    },
    "wake_word": {
        "phrase": "hey mimir",
        "model_path": "models/wake_word",
        "sensitivity": 0.5,
    },
    "stt": {
        "model_size": "tiny.en",
        "device": "cpu",
        "compute_type": "int8",
        "silence_threshold": 500,
        "silence_duration_sec": 1.0,
        "sample_rate": 16000,
    },
    "tts": {
        "voice_model_path": "models/piper/voice.onnx",
        "voice_config_path": "models/piper/voice.onnx.json",
        "speed": 1.0,
    },
    "llm": {
        "model": "phi3:mini",
        "num_predict": 5,
        "temperature": 0,
    },
    "lifecycle": {
        "idle_unload_minutes": 5,
        "check_interval_seconds": 30,
    },
    "volume": {
        "volume_step_percent": 10,
    },
    "hotkeys": {
        "pause_resume": "ctrl+shift+m",
        "quit": "ctrl+shift+q",
    },
    "logging": {
        "level": "INFO",
        "max_bytes": 5 * 1024 * 1024,
        "backup_count": 3,
    },
    "printer": {
        "last_opened_file_placeholder": "",
    },
}


class _Section:
    """Simple attribute-access wrapper around a config dict section."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __repr__(self) -> str:
        return f"_Section({self._data!r})"


class Config:
    """Top-level configuration object. Access sections as attributes,
    e.g. ``config.stt.model_size``.
    """

    def __init__(self, path: str = "config.yaml") -> None:
        merged = _deep_merge(DEFAULTS, _load_yaml(path))
        self._raw = merged
        for section_name, section_data in merged.items():
            setattr(self, section_name, _Section(section_data))

    def reload(self, path: str = "config.yaml") -> None:
        """Reload configuration from disk (used after settings are saved)."""
        merged = _deep_merge(DEFAULTS, _load_yaml(path))
        self._raw = merged
        for section_name, section_data in merged.items():
            setattr(self, section_name, _Section(section_data))


def _load_yaml(path: str) -> dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into a copy of base."""
    result: dict[str, Any] = {}
    for key, value in base.items():
        if isinstance(value, dict):
            result[key] = _deep_merge(value, override.get(key, {}) or {})
        else:
            result[key] = override.get(key, value)
    for key, value in override.items():
        if key not in result:
            result[key] = value
    return result


# Single shared instance, loaded on import.
config = Config()
