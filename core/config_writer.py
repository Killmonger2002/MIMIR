"""Shared config.yaml read-patch-write-reload helper.

Every UI surface that persists a setting immediately on change (Settings,
the audio calibration wizard, the transcript-bar toggle) needs the same
read-merge-write-reload round trip; this is the one place that does it,
instead of each window re-implementing its own copy.
"""

from __future__ import annotations

import yaml

from config import config

_CONFIG_PATH = "config.yaml"


def patch_config(updates: dict[str, dict]) -> None:
    """Merge `updates` (a dict of section -> {key: value}) into
    config.yaml on disk and reload the shared config object."""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    for section, section_updates in updates.items():
        data.setdefault(section, {}).update(section_updates)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False)
    config.reload(_CONFIG_PATH)
