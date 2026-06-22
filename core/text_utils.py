"""Shared text normalization helpers for command classification and executors.

Kept separate from intent_router/executors to avoid circular imports - both
core/intent_router.py and the executors import from here.
"""

from __future__ import annotations

import re

# Stripped repeatedly from the front of the transcript before classification,
# so polite phrasing like "Please open notepad" routes the same as "open notepad".
_FILLER_PREFIX_RE = re.compile(
    r"^(please|hey( mimir)?|mimir|can you|could you|would you|will you)[\s,]+",
    re.IGNORECASE,
)


def strip_filler_prefixes(text: str) -> str:
    """Repeatedly strip leading filler phrases like 'please'/'can you'."""
    while True:
        new_text = _FILLER_PREFIX_RE.sub("", text, count=1)
        if new_text == text:
            return text
        text = new_text


def normalize_command(text: str) -> str:
    """Lowercase, strip filler prefixes, and normalize stray punctuation.

    STT sometimes inserts a comma right after a leading verb (e.g.
    "Open, Dell." instead of "Open Dell."), which breaks every `^verb\\s+`
    regex used for command parsing. This collapses internal punctuation to
    spaces and trims leading/trailing punctuation so downstream regexes see
    clean, space-separated words.
    """
    text = text.lower().strip()
    text = strip_filler_prefixes(text)
    text = re.sub(r"[,]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(" .!?")
    return text
