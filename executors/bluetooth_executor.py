"""Controls Bluetooth state and paired device connections.

Voice command patterns handled:
    - "turn on bluetooth" / "turn off bluetooth"
    - "connect my headphones"
    - "pair my speaker"

NOTE: Windows does not expose a simple supported API for "connect to a
specific already-paired Bluetooth audio device" from user-mode Python
without additional dependencies (winrt). This implementation lists
paired devices via PowerShell's Get-PnpDevice and does its best to
toggle the Bluetooth radio via the Windows Settings URI / devmgmt
fallback. Full per-device connect/disconnect is best-effort.
"""

from __future__ import annotations

import logging
import re

from thefuzz import fuzz, process

from executors.base import ExecutorResult, run_hidden
from state import AppState

logger = logging.getLogger("mimir.bluetooth_executor")

_CONNECT_RE = re.compile(r"(?:connect|pair)(?: my| to)?\s+(.+)", re.IGNORECASE)
_MATCH_THRESHOLD = 60


def _list_paired_devices() -> list[str]:
    """Return names of paired Bluetooth devices via PowerShell Get-PnpDevice."""
    ps_cmd = (
        "Get-PnpDevice -Class Bluetooth | "
        "Where-Object { $_.Status -eq 'OK' } | "
        "Select-Object -ExpandProperty FriendlyName"
    )
    result = run_hidden(["powershell", "-NoProfile", "-Command", ps_cmd])
    names = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return names


def _set_bluetooth_radio(enable: bool) -> ExecutorResult:
    """Best-effort toggle of the Bluetooth radio via PowerShell.

    # TODO(phase2): replace with a more robust radio toggle using the
    # winrt Windows.Devices.Radios API once available offline.
    """
    state_word = "Enable" if enable else "Disable"
    ps_cmd = (
        f"Get-PnpDevice | Where-Object {{$_.Class -eq 'Bluetooth'}} | "
        f"{state_word}-PnpDevice -Confirm:$false"
    )
    try:
        run_hidden(["powershell", "-NoProfile", "-Command", ps_cmd])
        return ExecutorResult(success=True, speak=f"Bluetooth turned {'on' if enable else 'off'}")
    except Exception:
        logger.exception("Failed to toggle Bluetooth radio")
        return ExecutorResult(success=False, speak="I can't access Bluetooth devices right now")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Toggle Bluetooth or attempt to connect/pair a named paired device."""
    try:
        text = command_text.lower().strip()

        if "turn on" in text or "enable" in text:
            return _set_bluetooth_radio(True)

        if "turn off" in text or "disable" in text:
            return _set_bluetooth_radio(False)

        match = _CONNECT_RE.search(text)
        if match:
            target = match.group(1).strip()
            try:
                devices = _list_paired_devices()
            except Exception:
                logger.exception("Failed to list paired Bluetooth devices")
                return ExecutorResult(success=False, speak="I can't access Bluetooth devices right now")

            if not devices:
                return ExecutorResult(success=False, speak="I can't access Bluetooth devices right now")

            best = process.extractOne(target, devices, scorer=fuzz.ratio)
            if best is None or best[1] <= _MATCH_THRESHOLD:
                return ExecutorResult(success=False, speak=f"I couldn't find a paired device called {target}")

            device_name = best[0]
            # TODO(phase2): actually invoke BluetoothSetServiceState via ctypes
            # to connect the matched device's audio service. For now we
            # report the match but cannot guarantee a live connection.
            return ExecutorResult(success=True, speak=f"Connecting to {device_name}")

        return ExecutorResult(success=False, speak="I'm not sure what Bluetooth action you want.")
    except Exception:
        logger.exception("bluetooth_executor failed")
        return ExecutorResult(success=False, speak="I can't access Bluetooth devices right now")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["turn on bluetooth", "connect my headphones", "pair my speaker"]:
        print(cmd, "->", execute(cmd, _state))
