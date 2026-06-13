"""Controls display brightness.

Voice command patterns handled:
    - "set brightness to 50%" / "set the brightness at 50"
    - "brightness up" / "brightness down"
    - "increase/decrease brightness"
"""

from __future__ import annotations

import logging
import re

from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.brightness_executor")

_NUMBER_RE = re.compile(r"\b(\d{1,3})\b")
_STEP_PERCENT = 10


def _get_brightness_controller():
    import wmi

    conn = wmi.WMI(namespace="wmi")
    current = conn.WmiMonitorBrightness()[0].CurrentBrightness
    methods = conn.WmiMonitorBrightnessMethods()[0]
    return current, methods


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Adjust display brightness based on the spoken command."""
    try:
        text = command_text.lower().strip()
        current, methods = _get_brightness_controller()

        number_match = _NUMBER_RE.search(text)
        if number_match:
            target = max(0, min(100, int(number_match.group(1))))
        elif "up" in text or "increase" in text or "brighter" in text:
            target = min(100, current + _STEP_PERCENT)
        elif "down" in text or "decrease" in text or "dim" in text:
            target = max(0, current - _STEP_PERCENT)
        else:
            return ExecutorResult(success=False, speak="I'm not sure what brightness change you want.")

        methods.WmiSetBrightness(target, 0)
        return ExecutorResult(success=True, speak=f"Brightness set to {target} percent")
    except Exception:
        logger.exception("brightness_executor failed")
        return ExecutorResult(success=False, speak="I couldn't change the brightness.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["set brightness to 50%", "brightness up", "brightness down"]:
        print(cmd, "->", execute(cmd, _state))
