"""MIMIR self-referential commands: capability listing and shutdown.

Voice command patterns handled:
    - "what can you do" / "list your commands" / "help"
    - "quit mimir" / "close yourself" / "shut down mimir" / "exit"
"""

from __future__ import annotations

import re

from core.text_utils import normalize_command
from executors.base import ExecutorResult
from state import AppState

_QUIT_RE = re.compile(
    r"(quit|exit|close|shut\s*down|stop|power\s*off)\s*(yourself|mimir)|"
    r"(mimir\s*,?\s*(quit|exit|shut\s*down))|"
    r"^(shut\s*down|power\s*off)\W*$|"
    r"\bturn\s+(yourself|mimir)\s+off\b|"
    r"\bshut\s+(yourself|mimir)\s+down\b|"
    r"^good\s*bye\b",
    re.IGNORECASE,
)

_CAPABILITIES = (
    "Here's what I can do: open apps and files, control volume and "
    "brightness, manage wifi and bluetooth, print documents, control "
    "windows, play media, report system info like battery, CPU, RAM and "
    "disk space, type text for you, and shut myself down when you ask."
)

# "Can you hear me?" arrives here as just "hear me" - normalize_command
# strips "can you" as a filler prefix before classification. Observed live:
# this was the first thing the user said in a session, twice, and got
# "I didn't understand that command" both times.
_HEAR_ME_RE = re.compile(
    r"\bhear me\b|\bare you (there|listening|awake|working)\b|^(hello|hi|hey)$",
    re.IGNORECASE,
)


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Handle help/capability queries, presence checks, and self-shutdown."""
    text = normalize_command(command_text)

    if _HEAR_ME_RE.search(text):
        return ExecutorResult(success=True, speak="Yes, I can hear you loud and clear.")

    if _QUIT_RE.search(text):
        # A mishearing here kills the assistant, so always confirm first.
        return ExecutorResult(
            success=True,
            speak="",
            confirm="That will shut down MIMIR. Are you sure?",
            on_confirm=lambda: ExecutorResult(success=True, speak="Goodbye.", shutdown=True),
        )

    return ExecutorResult(success=True, speak=_CAPABILITIES)


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["what can you do", "list out all the commands you can follow", "please close yourself", "quit mimir"]:
        print(cmd, "->", execute(cmd, _state))
