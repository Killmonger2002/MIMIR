"""Controls Wi-Fi connections via netsh.

Voice command patterns handled:
    - "connect to homewifi"
    - "turn off wifi" / "turn on wifi"
    - "disconnect wifi"
"""

from __future__ import annotations

import logging
import re

from thefuzz import fuzz, process

from executors.base import ExecutorResult, run_hidden
from state import AppState

logger = logging.getLogger("mimir.wifi_executor")

_CONNECT_RE = re.compile(r"connect to\s+(.+)", re.IGNORECASE)
_MATCH_THRESHOLD = 60
# Below this, connect only after a spoken confirmation - a garbled network
# name shouldn't silently connect to a different saved profile than the
# one meant (the same "don't act confidently on a weak match" pattern
# applied to app_executor/window_executor after a live matching bug there;
# also switched to fuzz.ratio, the length-aware scorer - default WRatio's
# aggressive partial-match mode was observed there inflating short/unrelated
# candidates well above threshold).
_CONFIDENT_SCORE = 85


def _list_profiles() -> list[str]:
    """Return saved Wi-Fi profile names via netsh."""
    result = run_hidden(["netsh", "wlan", "show", "profiles"])
    profiles = []
    for line in result.stdout.splitlines():
        match = re.search(r":\s*(.+)$", line)
        if "All User Profile" in line and match:
            profiles.append(match.group(1).strip())
    return profiles


def _connect(profile_name: str) -> ExecutorResult:
    result = run_hidden(["netsh", "wlan", "connect", f"name={profile_name}"])
    if result.returncode == 0:
        return ExecutorResult(success=True, speak=f"Connecting to {profile_name}")
    logger.debug("netsh connect failed: %s", result.stderr)
    return ExecutorResult(success=False, speak=f"I couldn't connect to {profile_name}")


def _disconnect() -> ExecutorResult:
    result = run_hidden(["netsh", "wlan", "disconnect"])
    if result.returncode == 0:
        return ExecutorResult(success=True, speak="Disconnected from Wi-Fi")
    return ExecutorResult(success=False, speak="I couldn't disconnect from Wi-Fi")


def _set_radio(enable: bool) -> ExecutorResult:
    state_word = "enabled" if enable else "disabled"
    result = run_hidden(
        ["netsh", "interface", "set", "interface", "Wi-Fi", f"admin={state_word}"]
    )
    if result.returncode == 0:
        return ExecutorResult(success=True, speak=f"Wi-Fi turned {'on' if enable else 'off'}")
    if "elevation" in (result.stdout + result.stderr).lower():
        return ExecutorResult(
            success=False,
            speak="I need administrator permissions to change Wi-Fi. Please restart MIMIR as administrator.",
        )
    return ExecutorResult(success=False, speak="I couldn't change the Wi-Fi state")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Connect to a saved Wi-Fi network, disconnect, or toggle the Wi-Fi radio."""
    try:
        text = command_text.lower().strip()

        if "disconnect" in text:
            return _disconnect()

        if "turn off" in text or "disable" in text:
            return _set_radio(False)

        if "turn on" in text or "enable" in text:
            return _set_radio(True)

        match = _CONNECT_RE.search(text)
        if match:
            target = match.group(1).strip().strip('"')
            profiles = _list_profiles()
            if not profiles:
                return ExecutorResult(
                    success=False,
                    speak=f"{target} isn't a saved network. Say the password and I'll connect you.",
                    needs_followup=True,
                )

            best = process.extractOne(target, profiles, scorer=fuzz.ratio)
            if best is None or best[1] <= _MATCH_THRESHOLD:
                return ExecutorResult(
                    success=False,
                    speak=f"{target} isn't a saved network. Say the password and I'll connect you.",
                    needs_followup=True,
                )

            # TODO(phase2): if no saved profile matches, accept a spoken password
            # and create a new Wi-Fi profile via netsh add profile.
            profile_name, score = best
            if score < _CONFIDENT_SCORE:
                return ExecutorResult(
                    success=True,
                    speak="",
                    confirm=f"Did you mean {profile_name}?",
                    on_confirm=lambda: _connect(profile_name),
                )
            return _connect(profile_name)

        return ExecutorResult(success=False, speak="I'm not sure what Wi-Fi action you want.")
    except Exception:
        logger.exception("wifi_executor failed")
        return ExecutorResult(success=False, speak="I couldn't change the Wi-Fi settings.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["connect to homewifi", "turn off wifi", "disconnect wifi"]:
        print(cmd, "->", execute(cmd, _state))
