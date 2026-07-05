"""Controls media playback via simulated media keys.

Voice command patterns handled:
    - "play" / "pause" / "resume"
    - "next song" / "skip"
    - "previous track" / "back"
    - "skip ad" / "skip this ad" / "skip the commercial"
"""

from __future__ import annotations

import logging
import re

import pyautogui

from config import config
from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.media_executor")

# Word-boundary match, not a plain substring check - "ad" as a bare
# substring would false-positive inside "already", "address", "advance",
# "load", etc. Checked before the generic "skip"/"next" branch below,
# since "skip ad" would otherwise be caught by "skip" -> next-track.
_AD_RE = re.compile(r"\b(ads?|advertisements?|commercials?)\b", re.IGNORECASE)


def _skip_ad() -> ExecutorResult:
    """Seek forward several times via the Right Arrow key - the most
    widely-supported seek-forward shortcut across web video players
    (YouTube, Netflix, Vimeo, etc.). There's no universal OS-level "skip
    ad" action; this works because ad breaks are almost always seekable
    even before their own skip button becomes available. Won't do
    anything on players/ads that block seeking (e.g. some unskippable
    inserted ads) - there's no generic way to detect or bypass those."""
    for _ in range(config.media.ad_skip_seek_presses):
        pyautogui.press("right")
    return ExecutorResult(success=True, speak="Skipping the ad")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Send a media key press based on the spoken playback command."""
    try:
        text = command_text.lower().strip()

        if _AD_RE.search(text):
            return _skip_ad()

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
    for cmd in ["play", "next song", "previous track", "skip ad", "skip this ad", "skip the commercial"]:
        print(cmd, "->", execute(cmd, _state))
