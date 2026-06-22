"""Routes transcribed text to the appropriate executor module name.

Two-tier classification:
    1. Regex tier - fast, deterministic pattern matching.
    2. LLM fallback tier - asks the local Ollama phi3:mini model to
       classify the command when no regex matches.
"""

from __future__ import annotations

import logging
import re

from core.text_utils import normalize_command, strip_filler_prefixes
from executors import system_executor as _system_executor

logger = logging.getLogger("mimir.intent_router")

# Re-exported for backwards compatibility - executors historically imported
# this name directly from intent_router.
_strip_filler_prefixes = strip_filler_prefixes

# Ordered list of (executor_name, [regex patterns]).
# First matching pattern wins, in declaration order.
_PATTERNS: list[tuple[str, list[str]]] = [
    (
        "app_executor",
        [
            r"^(open|launch|start|run)\s+"
            r"(?!.*\b(downloads|desktop|documents|pictures|music|videos|folder|this pc|my computer)\b)"
            r"(?!.*\b(disk|drive)\s+[a-z]\b).+",
        ],
    ),
    (
        "file_executor",
        [
            r"^(open|go to|navigate to)\s+.*\b(downloads|desktop|documents|pictures|music|videos|folder)\b",
            r"^(find|search for|locate)\s+(my\s+)?.+",
            r"\bthis pc\b",
            r"\bmy computer\b",
            r"\b(disk|drive)\s+[a-z]\b",
            r"\b(go back|go up|previous (directory|folder)|parent (directory|folder))\b",
            r"\b(list|show|tell)\b.*\bfiles?\b",
            r"\bwhat files\b",
        ],
    ),
    (
        "volume_executor",
        [
            r"\bvolume\b",
            r"^(mute|unmute)$",
            r"\b(turn it up|turn it down|louder|quieter)\b",
            r"\b(volume up|volume down)\b",
        ],
    ),
    (
        "brightness_executor",
        [
            r"\bbrightness\b",
            r"\b(brighter|dimmer|dim the screen)\b",
        ],
    ),
    (
        "wifi_executor",
        [
            r"\bwi[\s-]?fi\b",
            r"connect to\s+.+",
            r"disconnect.*wifi",
        ],
    ),
    (
        "bluetooth_executor",
        [
            r"\bbluetooth\b",
            r"\b(connect|pair)\s+(my\s+)?(headphones|speaker|earbuds|earphones|headset)\b",
        ],
    ),
    (
        "printer_executor",
        [
            r"\bprint\b",
            r"\bprinters?\b",
        ],
    ),
    (
        "window_executor",
        [
            r"\b(minimi[sz]e|maximi[sz]e)\b",
            r"^switch to\s+.+",
            r"\bclose (this|the) window\b",
            r"\block (the )?screen\b",
            r"^(go to )?sleep$",
            r"^(close|quit|exit)\s+(?!.*\b(yourself|mimir)\b).+",
        ],
    ),
    (
        "media_executor",
        [
            r"^(play|pause|resume|stop)$",
            r"\b(next|previous|skip)\b.*\b(song|track)?\b",
            r"^(next|previous|skip|back)$",
        ],
    ),
    (
        "sysinfo_executor",
        [
            r"\b(cpu|processor)\b.*\busage\b",
            r"\bhow much (battery|ram|memory|disk|storage|space)\b",
            r"\b(battery|cpu usage|ram usage|disk space)\b",
        ],
    ),
    (
        "typing_executor",
        [
            r"^(type|write|dictate)\b",
            r"^(press\s+)?(backspace|delete|enter|return|tab|escape|new line)\b",
        ],
    ),
    (
        "system_executor",
        [
            r"\bwhat can you do\b",
            r"\b(list|show)\b.*\b(commands?|capabilities)\b",
            r"^help$",
            _system_executor._QUIT_RE.pattern,
        ],
    ),
]

_LLM_CATEGORIES = [name for name, _ in _PATTERNS] + ["browser_executor", "unknown"]


def _regex_classify(text: str) -> str | None:
    """Return the first executor whose pattern matches, or None."""
    for executor_name, patterns in _PATTERNS:
        for pattern in patterns:
            if re.search(pattern, text):
                return executor_name
    return None


def _llm_classify(text: str) -> str:
    """Ask the local Ollama model to classify the command."""
    try:
        import ollama

        from config import config

        categories_str = ", ".join(_LLM_CATEGORIES)
        prompt = (
            f"Classify this voice command into exactly one category: "
            f"[{categories_str}]. Reply with ONLY the category name.\n\n"
            f"Command: {text}"
        )
        client = ollama.Client(timeout=2)
        response = client.generate(
            model=config.llm.model,
            prompt=prompt,
            options={
                "num_predict": config.llm.num_predict,
                "temperature": config.llm.temperature,
            },
        )
        category = response.get("response", "").strip().lower()
        for valid in _LLM_CATEGORIES:
            if valid in category:
                return valid
        return "unknown"
    except Exception as exc:
        logger.warning("LLM classification unavailable: %s", exc)
        return "unknown"


def classify(text: str) -> str:
    """Classify a transcript into an executor module name.

    Returns the executor module name (e.g. "volume_executor"), or
    "unknown" if neither the regex tier nor the LLM fallback can
    classify the command.
    """
    text = normalize_command(text)
    if not text:
        return "unknown"

    executor_name = _regex_classify(text)
    if executor_name is not None:
        logger.debug("Regex classified %r -> %s", text, executor_name)
        return executor_name

    executor_name = _llm_classify(text)
    logger.debug("LLM classified %r -> %s", text, executor_name)
    return executor_name


if __name__ == "__main__":
    _test_phrases = [
        "open chrome",
        "launch spotify",
        "start notepad",
        "open downloads folder",
        "find my resume",
        "open desktop",
        "volume 40",
        "mute",
        "unmute",
        "turn it up",
        "connect to homewifi",
        "turn off wifi",
        "disconnect wifi",
        "connect my headphones",
        "turn on bluetooth",
        "pair my speaker",
        "print this",
        "i want to print this pdf",
        "show printers",
        "minimise this",
        "switch to chrome",
        "close this window",
        "lock the screen",
        "play",
        "pause",
        "next song",
        "skip",
        "previous track",
        "how much battery",
        "cpu usage",
        "how much ram",
        "disk space",
        "type hello world",
        "write dear sir",
    ]
    for phrase in _test_phrases:
        print(f"{phrase!r:40} -> {classify(phrase)}")
