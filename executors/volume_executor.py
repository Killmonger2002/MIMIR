"""Controls system audio volume and mute state.

Voice command patterns handled:
    - "volume 40" / "set volume to 40"
    - "mute" / "unmute"
    - "turn it up" / "volume down"
"""

from __future__ import annotations

import logging
import re

from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.volume_executor")

_NUMBER_RE = re.compile(r"\b(\d{1,3})\b")


def _get_volume_interface():
    """Return the IAudioEndpointVolume COM interface for the default device."""
    from pycaw.pycaw import AudioUtilities

    device = AudioUtilities.GetSpeakers()
    return device.EndpointVolume


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Adjust master volume or mute state based on the spoken command."""
    try:
        from config import config

        text = command_text.lower().strip()
        volume = _get_volume_interface()

        if "unmute" in text:
            volume.SetMute(0, None)
            return ExecutorResult(success=True, speak="Unmuted")

        if "mute" in text:
            volume.SetMute(1, None)
            return ExecutorResult(success=True, speak="Muted")

        number_match = _NUMBER_RE.search(text)
        if "volume" in text or "louder" in text or "quieter" in text or "up" in text or "down" in text:
            current = volume.GetMasterVolumeLevelScalar()
            step = config.volume.volume_step_percent / 100.0

            if number_match:
                target = max(0, min(100, int(number_match.group(1))))
                volume.SetMasterVolumeLevelScalar(target / 100.0, None)
                return ExecutorResult(success=True, speak=f"Volume set to {target} percent")

            if "up" in text or "louder" in text:
                target = min(1.0, current + step)
            elif "down" in text or "quieter" in text:
                target = max(0.0, current - step)
            else:
                return ExecutorResult(success=False, speak="I'm not sure what volume change you want.")

            volume.SetMasterVolumeLevelScalar(target, None)
            return ExecutorResult(success=True, speak=f"Volume set to {round(target * 100)} percent")

        return ExecutorResult(success=False, speak="I'm not sure what volume change you want.")
    except Exception:
        logger.exception("volume_executor failed")
        return ExecutorResult(success=False, speak="I couldn't change the volume.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["volume 40", "mute", "unmute", "turn it up"]:
        print(cmd, "->", execute(cmd, _state))
