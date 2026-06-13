"""Types text into the currently focused window.

Voice command patterns handled:
    - "type hello world"
    - "write dear sir"
"""

from __future__ import annotations

import logging
import re

import pyautogui

from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.typing_executor")

_PREFIX_RE = re.compile(r"^(?:type|write|dictate)\s*:?\s*", re.IGNORECASE)

_KEY_RE = re.compile(
    r"^(?:press\s+)?(backspace|delete|enter|return|tab|escape|new line)\b(.*)$",
    re.IGNORECASE,
)

_KEY_ALIASES = {
    "backspace": "backspace",
    "delete": "delete",
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "escape": "esc",
    "new line": "enter",
}

_NUMBER_WORDS = {
    "one": 1, "once": 1, "two": 2, "twice": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}


def _count_from_remainder(remainder: str) -> int:
    """Best-effort parse of a repeat count from trailing words like 'twice' or '3 times'."""
    remainder = remainder.strip(" .!?,").lower()
    if not remainder:
        return 1

    digit_match = re.search(r"\d+", remainder)
    if digit_match:
        return max(1, int(digit_match.group(0)))

    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", remainder):
            return value

    return 1


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Type text, or press a named key (optionally a number of times)."""
    try:
        text = command_text.strip().strip(" .!?,")

        key_match = _KEY_RE.match(text)
        if key_match:
            key_name = key_match.group(1).lower()
            key = _KEY_ALIASES[key_name]
            count = _count_from_remainder(key_match.group(2))
            pyautogui.press(key, presses=count)
            return ExecutorResult(success=True, speak=f"Pressed {key_name}")

        text = _PREFIX_RE.sub("", text, count=1)
        if not text:
            return ExecutorResult(success=False, speak="I didn't catch what to type.")

        pyautogui.write(text, interval=0.03)
        return ExecutorResult(success=True, speak="Typed it")
    except Exception:
        logger.exception("typing_executor failed")
        return ExecutorResult(success=False, speak="I couldn't type that.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["type hello world", "write dear sir"]:
        print(cmd, "->", execute(cmd, _state))
