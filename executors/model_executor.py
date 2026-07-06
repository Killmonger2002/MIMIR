"""User-controlled LLM tier switching by voice ("big brother protocol").

Voice command patterns handled:
    - "get smarter" / "switch to a smarter model"   -> one tier up
    - "switch to the smartest model" / "get smartest" -> tier 3
    - "switch to the basic model" / "get simpler"   -> tier 1
    - "which model are you using"                   -> status

Escalation is deliberately explicit: MIMIR starts every session on the
least-resource tier and never climbs on its own - see core/llm_runtime.
"""

from __future__ import annotations

import logging
import re

from core.text_utils import normalize_command
from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.model_executor")

_SMARTEST_RE = re.compile(r"\b(smartest|biggest|best|maximum)\s+(model|brain)\b|\bget smartest\b", re.IGNORECASE)
_SMARTER_RE = re.compile(r"\bget smarter\b|\b(smarter|bigger|better)\s+(model|brain)\b|\bthink harder\b", re.IGNORECASE)
_BASIC_RE = re.compile(
    r"\b(basic|smaller|simpler|default|normal|fastest)\s+(model|brain)\b|\bget (simpler|dumber)\b|\bsave (some )?(memory|ram|resources)\b",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(r"\b(which|what)\s+(model|brain)\b|\bhow smart are you\b", re.IGNORECASE)

# The union of everything above, for the intent router's pattern table.
PATTERNS = [
    r"\b(smartest|smarter|bigger|biggest|better|best|maximum|basic|smaller|simpler|default|normal|fastest)\s+(model|brain)\b",
    r"\bget (smarter|smartest|simpler|dumber)\b",
    r"\bthink harder\b",
    r"\b(which|what)\s+(model|brain)\b",
    r"\bhow smart are you\b",
    r"\bsave (some )?(memory|ram|resources)\b",
]


def _tier_speak_name(tier: int) -> str:
    from core.llm_runtime import TIER_LABELS, _tier_config

    model, _ka, _t = _tier_config(tier)
    # "qwen2.5:7b" reads terribly aloud; speak the label plus a clean size.
    size = model.split(":")[-1] if ":" in model else ""
    return f"the {TIER_LABELS.get(tier, str(tier))} model{f', {size}' if size else ''}"


def _switch_to(tier: int) -> ExecutorResult:
    from core.llm_runtime import get_active_tier, set_active_tier

    current = get_active_tier()
    if tier == current:
        return ExecutorResult(success=True, speak=f"I'm already using {_tier_speak_name(tier)}.")

    resolved = set_active_tier(tier)
    if resolved == tier:
        return ExecutorResult(success=True, speak=f"Switched to {_tier_speak_name(tier)}.")
    if resolved > 0:
        # The ceiling was raised, but this machine can't actually deliver
        # the requested tier (RAM gate or model not pulled) - say so
        # honestly instead of claiming an upgrade that didn't happen.
        return ExecutorResult(
            success=True,
            speak=f"That model isn't available on this machine, so I'll keep using {_tier_speak_name(resolved)}.",
        )
    return ExecutorResult(success=False, speak="No language model is available right now.")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Switch the active LLM tier, or report which model is in use."""
    try:
        from core.llm_runtime import get_active_tier

        text = normalize_command(command_text)

        if _STATUS_RE.search(text):
            return ExecutorResult(success=True, speak=f"I'm using {_tier_speak_name(get_active_tier())}.")

        # Order matters: "smartest" contains no "smarter" but "get smarter"
        # phrasing overlaps - check the superlative first.
        if _SMARTEST_RE.search(text):
            return _switch_to(3)

        if _SMARTER_RE.search(text):
            return _switch_to(min(3, get_active_tier() + 1))

        if _BASIC_RE.search(text):
            return _switch_to(1)

        return ExecutorResult(success=False, speak="I'm not sure which model change you want.")
    except Exception:
        logger.exception("model_executor failed")
        return ExecutorResult(success=False, speak="I couldn't switch models.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in [
        "which model are you using",
        "get smarter",
        "switch to the smartest model",
        "switch to the basic model",
        "get smarter",
    ]:
        print(cmd, "->", execute(cmd, _state).speak)
