"""Builds a Whisper vocabulary hint from names actually present on this
machine (real folder names, installed app names), to bias transcription
toward them.

Directly targets the mishearing class documented in UX_LOG.md: "Dell"
transcribed as "then", "Codex" transcribed as "codecs" - both real,
locally-known names that a generic English language model has no reason
to prefer over a more common homophone. Feeding these through Whisper's
initial_prompt biases the decoder toward them without any fine-tuning.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("mimir.vocabulary")

# Whisper's initial_prompt has a real (small) token budget; a long list
# dilutes the bias toward any one name rather than strengthening it, so
# folder names (the actual UX_LOG mishearing cases) get priority over the
# much larger pool of installed app names.
_MAX_FOLDER_NAMES = 40
_MAX_APP_NAMES = 20
_MAX_UI_NAMES = 25  # on-screen element names get priority (placed first) but stay bounded

# Only the folder/app portion is cached (it rarely changes); the on-screen
# UI-element portion is recomputed each call since it changes per screen.
_static_prompt: str | None = None


def _collect_folder_names() -> list[str]:
    """Immediate subfolder names of the known root folders (Desktop,
    Downloads, Documents, etc.) - a shallow listdir, not the full
    recursive walk file_executor does when actually resolving a folder."""
    from executors.file_executor import _FOLDER_MAP

    names: list[str] = []
    for root in _FOLDER_MAP.values():
        if not os.path.isdir(root):
            continue
        try:
            for entry in os.listdir(root):
                if os.path.isdir(os.path.join(root, entry)):
                    names.append(entry)
        except OSError:
            continue
    return names[:_MAX_FOLDER_NAMES]


def _collect_app_names() -> list[str]:
    """Installed app names, but only if app_executor's index has already
    been built by an earlier command - never force-build it here, since
    that would add a directory-walk's worth of latency to every
    transcription until the first "open X" command happens to trigger it."""
    from executors import app_executor

    if app_executor._APP_INDEX is None:
        return []
    return list(app_executor._APP_INDEX.keys())[:_MAX_APP_NAMES]


def _build_static_prompt() -> str:
    global _static_prompt
    if _static_prompt is not None:
        return _static_prompt

    names: list[str] = []
    try:
        names.extend(_collect_folder_names())
    except Exception:
        logger.debug("Failed to collect folder names for vocabulary hint", exc_info=True)
    try:
        names.extend(_collect_app_names())
    except Exception:
        logger.debug("Failed to collect app names for vocabulary hint", exc_info=True)

    _static_prompt = ", ".join(_dedupe(names))
    logger.info("Built static vocabulary hint with %d names", len(_static_prompt.split(", ")) if _static_prompt else 0)
    return _static_prompt


def _dedupe(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = name.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


def build_vocabulary_prompt() -> str:
    """Whisper initial_prompt biasing transcription toward names that
    actually exist here: on-screen UI element names FIRST (the most
    immediately relevant context during UI control - "click <this app's
    weird button>"), then the cached folder/app names.

    The folder/app part is cached; the on-screen part is recomputed each
    call (cheap - it's just reading the last scan's cached names) since it
    changes per screen."""
    from core.ui_scanner import recent_element_names

    ui_names = recent_element_names()[:_MAX_UI_NAMES]
    static = _build_static_prompt()

    if not ui_names:
        return static
    ui_part = ", ".join(_dedupe(ui_names))
    return f"{ui_part}, {static}" if static else ui_part


def reset_cache() -> None:
    """Force the static (folder/app) portion to rebuild on its next call.
    The on-screen UI portion is never cached, so it's always current."""
    global _static_prompt
    _static_prompt = None


if __name__ == "__main__":
    print(build_vocabulary_prompt())
