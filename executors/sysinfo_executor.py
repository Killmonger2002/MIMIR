"""Reports system information: CPU, memory, disk, battery.

Voice command patterns handled:
    - "how much battery"
    - "cpu usage"
    - "how much ram" / "disk space"
"""

from __future__ import annotations

import logging

import psutil

from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.sysinfo_executor")


def _cpu_info() -> str:
    percent = psutil.cpu_percent(interval=0.5)
    return f"CPU usage is at {percent:.0f} percent"


def _ram_info() -> str:
    mem = psutil.virtual_memory()
    return f"You're using {mem.percent:.0f} percent of your memory"


def _disk_info() -> str:
    usage = psutil.disk_usage("C:/")
    free_gb = usage.free / (1024 ** 3)
    return f"You have {free_gb:.0f} gigabytes free on your C drive"


def _battery_info() -> str:
    battery = psutil.sensors_battery()
    if battery is None:
        return "This device doesn't have a battery"
    status = "charging" if battery.power_plugged else "not charging"
    return f"You have {battery.percent:.0f} percent battery remaining, and it's {status}"


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Speak the requested system stat: CPU, RAM, disk, or battery."""
    try:
        text = command_text.lower().strip()

        if "cpu" in text or "processor" in text:
            return ExecutorResult(success=True, speak=_cpu_info())

        if "ram" in text or "memory" in text:
            return ExecutorResult(success=True, speak=_ram_info())

        if "disk" in text or "storage" in text or "space" in text:
            return ExecutorResult(success=True, speak=_disk_info())

        if "battery" in text:
            return ExecutorResult(success=True, speak=_battery_info())

        return ExecutorResult(success=False, speak="I'm not sure which system info you want.")
    except Exception:
        logger.exception("sysinfo_executor failed")
        return ExecutorResult(success=False, speak="I couldn't get that system information.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["how much battery", "cpu usage", "how much ram", "disk space"]:
        print(cmd, "->", execute(cmd, _state))
