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
        # Play a short chime instead of speaking the word "Listening"
        # before every command - the spoken version adds a real,
        # perceptible ~500ms-1s delay before the user can even start
        # talking. Set false for no cue at all (rely on the tray icon).
        "listening_cue_enabled": True,
        # "chime" (fast two-tone beep) or "voice" (a quick spoken "Yes?" -
        # slower, but unmistakable if the chime is too easy to miss).
        "listening_cue_style": "chime",
    },
    "wake_word": {
        "phrase": "hey mimir",
        "model_path": "models/wake_word",
        "sensitivity": 0.5,
        # Require this many consecutive chunks above sensitivity before
        # firing, instead of a single frame - cuts down on brief false
        # positives from noise/other speech briefly spiking one model's
        # score. Higher = fewer false positives but slower to trigger on a
        # real "hey mimir".
        "min_activation_chunks": 3,
    },
    "stt": {
        "model_size": "tiny.en",
        "device": "cpu",
        "compute_type": "int8",
        "silence_threshold": 500,
        # How long a trailing silence has to last before a recording is
        # considered finished. Lower = snappier responses; too low risks
        # cutting the user off mid-sentence during a natural pause. Raise
        # this back toward 1.0 if that starts happening in practice.
        "silence_duration_sec": 0.6,
        "sample_rate": 16000,
        # Bias Whisper toward real folder/app names on this machine (see
        # core/vocabulary.py) - the fix for the "Dell"/"then",
        # "Codex"/"codecs" mishearing class in UX_LOG.md.
        "vocabulary_hint_enabled": True,
        # Silero VAD's speech-probability threshold (0-1) used to detect
        # end-of-utterance in record_until_silence(). Lower = more
        # sensitive to quiet speech, but more prone to false positives on
        # background noise.
        "vad_speech_threshold": 0.5,
        # Apply spectral-gating noise reduction to the recorded clip
        # before transcription. Can add tens to a few hundred ms on CPU;
        # disable if that latency isn't worth it on a quiet setup.
        "denoise_enabled": True,
    },
    "speaker_verification": {
        # Filters recorded audio down to segments matching the enrolled
        # voice (see enroll_voice.py) - the fix for MIMIR picking up other
        # people talking nearby instead of staying on whoever said the
        # wake word. Safe to leave on: it's a no-op until you've enrolled.
        "enabled": True,
        # Cosine similarity (0-1) a segment must reach against the
        # enrolled reference to be kept. Lower = more tolerant of mic/
        # background variation but more likely to let other voices
        # through; higher = stricter but may reject your own voice more
        # often (e.g. when sick, shouting, or on a different mic).
        "similarity_threshold": 0.75,
        # Partial-utterance windows per second (resemblyzer's own
        # granularity for the sliding-window comparison) - the default
        # matches resemblyzer's own default and shouldn't normally need
        # changing.
        "rate": 1.3,
    },
    "tts": {
        "voice_model_path": "models/piper/voice.onnx",
        "voice_config_path": "models/piper/voice.onnx.json",
        "speed": 1.0,
        # Piper/VITS prosody knobs, left at the voice's own baked-in
        # defaults (None) unless overridden here. noise_scale adds
        # variation to the voice itself (higher = more expressive, too
        # high = unstable/garbled); noise_w_scale adds variation to
        # timing/pacing (higher = less monotone rhythm). Try nudging
        # these up slightly (e.g. 0.75/0.9) if the voice sounds flat.
        "noise_scale": None,
        "noise_w_scale": None,
        # Let the user interrupt MIMIR mid-sentence by talking, instead of
        # having to wait for it to finish. Requires several consecutive
        # VAD-positive chunks (not one frame) before triggering, both to
        # avoid noise false-positives and because MIMIR's own voice can
        # leak into the mic without a headset.
        "barge_in_enabled": True,
        "barge_in_vad_threshold": 0.6,
        "barge_in_consecutive_chunks": 3,
    },
    "llm": {
        # --- Tier 1: command routing (small, kept warm) ---
        # qwen2.5:1.5b beat phi3:mini on classification accuracy (70% vs
        # 50%) at 40% lower latency and half the RAM in the live
        # benchmark (benchmark_llm_tier1.py, 2026-07-06) - and keeps the
        # whole tier ladder in one model family. Slot extraction runs on
        # tier 2 instead (better semantics; rare, failure-path-only).
        "model": "qwen2.5:1.5b",
        "num_predict": 5,
        "temperature": 0,
        # Warm-inference timeout for tier 1. Measured live on a 16GB
        # CPU-only machine: ~0.6s steady-state, ~1.7s first-warm-call -
        # the old 1.5s value randomly failed borderline calls. Cold model
        # loads are handled separately (see _COLD_LOAD_TIMEOUT_SEC).
        "timeout_sec": 10.0,
        "tier1_keep_alive": "30m",
        # --- Tier 2: the workhorse (drafting, summarizing, tool calling) ---
        # Qwen2.5 chosen for reliable function-calling at 7B (the agent
        # loop depends on it), Apache-2.0 license, and multilingual support.
        "tier2_model": "qwen2.5:7b",
        "tier2_keep_alive": "10m",
        "tier2_timeout_sec": 60.0,
        # --- Tier 3: deep work (long documents, debates, planning) ---
        # ~9GB resident at Q4; never kept loaded (keep_alive=0) and
        # auto-disabled below tier3_min_ram_gb total RAM. 15.0, not 16.0:
        # nominal-16GB machines report ~15.7GB usable, so a 16.0 gate
        # would wrongly exclude exactly the machines it's meant for.
        "tier3_model": "qwen2.5:14b",
        "tier3_timeout_sec": 180.0,
        "tier3_min_ram_gb": 15.0,
        # Launch `ollama serve` automatically at MIMIR startup when Ollama
        # is installed but not running.
        "autostart_ollama": True,
    },
    "confirmation": {
        # Spoken yes/no confirmation before uncertain or destructive actions.
        "enabled": True,
        # Confirm the transcript itself when Whisper's mean avg_logprob is
        # below this (clean speech ~ -0.2..-0.4; garbled < -0.8).
        "stt_logprob_threshold": -0.8,
        # How long to wait for the yes/no answer before treating it as no.
        "reply_wait_sec": 6.0,
    },
    "followup": {
        # After answering a command, briefly keep listening without
        # requiring the wake word again, so a quick next command doesn't
        # need "hey mimir" repeated.
        "enabled": True,
        "window_sec": 4.0,
    },
    "lifecycle": {
        "idle_unload_minutes": 5,
        "check_interval_seconds": 30,
    },
    "volume": {
        "volume_step_percent": 10,
    },
    "media": {
        # How many Right-Arrow seek-forward presses "skip ad" sends to the
        # foreground window. Each press's actual seek distance depends on
        # the player (often ~5-10s), so this is a rough knob for "how far
        # forward" rather than an exact seconds value.
        "ad_skip_seek_presses": 6,
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
    "ui": {
        # Small always-on-top live-transcript bar docked near the top of
        # the screen. Off by default so it never surprises anyone on
        # first launch; toggled from the tray menu or Settings.
        "transcript_bar_enabled": False,
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
