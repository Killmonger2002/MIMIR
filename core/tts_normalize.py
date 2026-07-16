"""Text normalization applied to every response before Piper synthesis.

This is deliberately NOT a general number-to-words pass: Piper's
phonemizer (espeak-ng) already reads plain digits correctly, and MIMIR's
own executors already spell out units/percent in words at the source
(see executors/sysinfo_executor.py - "gigabytes free", "40 percent").

The actual gap is strings MIMIR didn't author itself and can't control
the format of - Bluetooth device names, Wi-Fi SSIDs, window titles pulled
from arbitrary running apps - which can carry raw acronyms and
underscored identifiers a phonemizer reads badly (e.g. "TP-LINK_5G",
"USB", "4GB").
"""

from __future__ import annotations

import re

# Acronyms read as a mispronounced "word" if left alone, spelled out
# letter-by-letter instead - matched case-sensitively and only as a whole
# word, so normal text is never touched. Units are expanded to the same
# words MIMIR's own executors already use, for consistency.
_ACRONYM_LEXICON: dict[str, str] = {
    "GB": "gigabytes",
    "MB": "megabytes",
    "KB": "kilobytes",
    "TB": "terabytes",
    "PDF": "P D F",
    "USB": "U S B",
    "URL": "U R L",
    "API": "A P I",
    "IDE": "I D E",
    "CPU": "C P U",
    "GPU": "G P U",
    "SSD": "S S D",
    "HDMI": "H D M I",
    "UI": "U I",
    "OS": "O S",
    "AI": "A I",
}
# Not a plain \b...\b: units routinely butt up against a digit with no
# space ("64GB"), and \b doesn't count as a boundary between a digit and
# a letter (both are "word" characters) - so \bGB\b would silently miss
# exactly the "64GB" case this lexicon exists for. Lookarounds that only
# reject an adjacent LETTER (not a digit) fix that while still not
# matching acronyms embedded in other words ("GBike").
_ACRONYM_RE = re.compile(
    "|".join(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])" for word in _ACRONYM_LEXICON)
)

# Proper-noun pronunciation overrides. Empty by default - there's no
# confirmed mispronunciation to fix yet. Add a lowercase-keyed entry here
# if MIMIR is ever heard mangling a specific word (matched
# case-insensitively; the replacement's own casing is what gets spoken).
_PRONUNCIATION_LEXICON: dict[str, str] = {}


def _expand_acronym_match(m: re.Match) -> str:
    """Insert a space before the expansion when it directly follows a
    digit ("64GB") - the lookaround match itself is deliberately
    zero-width there, so without this "64GB" would become "64gigabytes"
    with no pause between the number and the word."""
    replacement = _ACRONYM_LEXICON[m.group(0)]
    start = m.start()
    if start > 0 and m.string[start - 1].isdigit():
        return " " + replacement
    return replacement


def normalize_for_speech(text: str) -> str:
    """Return `text` rewritten so Piper's phonemizer reads acronyms,
    units, and raw identifiers (device names, SSIDs, window titles)
    more naturally. Applied once per response, before sentence
    splitting."""
    if not text:
        return text

    for word, replacement in _PRONUNCIATION_LEXICON.items():
        text = re.sub(rf"\b{re.escape(word)}\b", replacement, text, flags=re.IGNORECASE)

    text = _ACRONYM_RE.sub(_expand_acronym_match, text)
    text = text.replace("%", " percent")
    text = text.replace("_", " ")
    text = re.sub(r"\s{2,}", " ", text)

    return text


if __name__ == "__main__":
    cases = [
        "You have 47% battery remaining, and it's charging",
        "Connecting to TP-LINK_5G",
        "I found a 64GB USB drive called SanDisk_Backup",
        "Opening the PDF in your default reader",
        "CPU usage is at 12 percent",
    ]
    for case in cases:
        print(f"{case!r} -> {normalize_for_speech(case)!r}")
