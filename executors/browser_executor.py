"""Browser automation - Phase 2 placeholder.

PHASE 2 PLACEHOLDER. Browser automation via Playwright will be implemented
in Phase 2. Do not implement beyond this stub.
"""

from __future__ import annotations

from executors.base import ExecutorResult
from state import AppState


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Stub for Phase 2 browser automation; always returns a not-available message."""
    # TODO(phase2): implement browser automation via Playwright.
    return ExecutorResult(
        success=False,
        speak="Browser control isn't available yet. That's coming in a future update.",
    )


if __name__ == "__main__":
    _state = AppState()
    print("open google.com ->", execute("open google.com", _state))
