"""MIMIR self-referential commands: capability listing and shutdown.

Voice command patterns handled:
    - "what can you do" / "list your commands" / "help"
    - "quit mimir" / "close yourself" / "shut down mimir" / "exit"
"""

from __future__ import annotations

import re

from executors.base import ExecutorResult
from state import AppState

_QUIT_RE = re.compile(
    r"(quit|exit|close|shut\s*down|stop)\s*(yourself|mimir)|"
    r"(mimir\s*,?\s*(quit|exit|shut\s*down))",
    re.IGNORECASE,
)

_CAPABILITIES = (
    "Here's what I can do: open apps and files, control volume and "
    "brightness, manage wifi and bluetooth, print documents, control "
    "windows, play media, report system info like battery, CPU, RAM and "
    "disk space, type text for you, and shut myself down when you ask."
)


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Handle help/capability queries and self-shutdown requests."""
    text = command_text.lower().strip()

    if _QUIT_RE.search(text):
        return ExecutorResult(success=True, speak="Goodbye.", shutdown=True)

    return ExecutorResult(success=True, speak=_CAPABILITIES)


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["what can you do", "list out all the commands you can follow", "please close yourself", "quit mimir"]:
        print(cmd, "->", execute(cmd, _state))
