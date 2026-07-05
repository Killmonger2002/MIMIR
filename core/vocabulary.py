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

_cached_prompt: str | None = None


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


def build_vocabulary_prompt() -> str:
    """Return a comma-separated list of real names on this machine, used
    as Whisper's initial_prompt. Cached for the process lifetime - the
    folder/app sets rarely change mid-session, and rebuilding on every
    utterance would add a directory-walk's worth of latency to every
    single command. Call reset_cache() to force a rebuild (done
    automatically once app_executor's index finishes building)."""
    global _cached_prompt
    if _cached_prompt is not None:
        return _cached_prompt

    names: list[str] = []
    try:
        names.extend(_collect_folder_names())
    except Exception:
        logger.debug("Failed to collect folder names for vocabulary hint", exc_info=True)
    try:
        names.extend(_collect_app_names())
    except Exception:
        logger.debug("Failed to collect app names for vocabulary hint", exc_info=True)

    seen: set[str] = set()
    deduped: list[str] = []
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(name)

    _cached_prompt = ", ".join(deduped)
    logger.info("Built vocabulary hint with %d names", len(deduped))
    return _cached_prompt


def reset_cache() -> None:
    """Force build_vocabulary_prompt() to rebuild on its next call."""
    global _cached_prompt
    _cached_prompt = None


if __name__ == "__main__":
    print(build_vocabulary_prompt())
