"""Shared types and helpers for all MIMIR executors."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class ExecutorResult:
    """Standard result returned by every executor's execute() function."""

    success: bool
    speak: str  # what MIMIR should say back to the user
    needs_followup: bool = False  # True if MIMIR should listen again immediately
    shutdown: bool = False  # True if MIMIR should exit after speaking
    # Set `confirm` to a spoken yes/no question to ask before acting. The
    # executor must NOT have performed the action yet - it goes in
    # `on_confirm`, which runs only if the user says yes and returns the
    # final ExecutorResult. On "no" the main loop cancels without calling it.
    confirm: Optional[str] = None
    on_confirm: Optional[Callable[[], "ExecutorResult"]] = None


def run_hidden(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
    """Run a subprocess command without flashing a console window.

    Wraps subprocess.run with CREATE_NO_WINDOW, captures stdout/stderr as
    text, and applies a default 10 second timeout.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
