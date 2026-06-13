"""Controls media playback via simulated media keys.

Voice command patterns handled:
    - "play" / "pause" / "resume"
    - "next song" / "skip"
    - "previous track" / "back"
"""

from __future__ import annotations

import logging

import pyautogui

from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.media_executor")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Send a media key press based on the spoken playback command."""
    try:
        text = command_text.lower().strip()

        if any(word in text for word in ("play", "pause", "resume")):
            pyautogui.press("playpause")
            return ExecutorResult(success=True, speak="Done")

        if any(word in text for word in ("next", "skip")):
            pyautogui.press("nexttrack")
            return ExecutorResult(success=True, speak="Skipping")

        if any(word in text for word in ("previous", "back")):
            pyautogui.press("prevtrack")
            return ExecutorResult(success=True, speak="Going back")

        if "stop" in text:
            pyautogui.press("stop")
            return ExecutorResult(success=True, speak="Stopped")

        return ExecutorResult(success=False, speak="I'm not sure what media action you want.")
    except Exception:
        logger.exception("media_executor failed")
        return ExecutorResult(success=False, speak="I couldn't control playback.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["play", "next song", "previous track"]:
        print(cmd, "->", execute(cmd, _state))
