"""Controls window state and system power/lock actions.

Voice command patterns handled:
    - "minimise this" / "maximise this"
    - "switch to chrome" / "close this window"
    - "lock the screen" / "sleep"
"""

from __future__ import annotations

import ctypes
import logging
import re

from thefuzz import process

from core.intent_router import _strip_filler_prefixes
from executors.app_executor import _ALIASES
from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.window_executor")

_SWITCH_RE = re.compile(r"switch to\s+(.+)", re.IGNORECASE)
_CLOSE_TARGET_RE = re.compile(r"^(?:close|quit|exit)\s+(.+)$", re.IGNORECASE)
_MATCH_THRESHOLD = 60


def _minimize() -> ExecutorResult:
    import win32con
    import win32gui

    hwnd = win32gui.GetForegroundWindow()
    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    return ExecutorResult(success=True, speak="Minimised")


def _maximize() -> ExecutorResult:
    import win32con
    import win32gui

    hwnd = win32gui.GetForegroundWindow()
    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    return ExecutorResult(success=True, speak="Maximised")


def _close() -> ExecutorResult:
    import win32con
    import win32gui

    hwnd = win32gui.GetForegroundWindow()
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    return ExecutorResult(success=True, speak="Closing window")


def _list_visible_windows() -> dict[str, int]:
    import win32gui

    titles: dict[str, int] = {}

    def _enum_handler(hwnd: int, _ctx: None) -> None:
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                titles[title] = hwnd

    win32gui.EnumWindows(_enum_handler, None)
    return titles


def _switch_to(target: str) -> ExecutorResult:
    titles = _list_visible_windows()

    if not titles:
        return ExecutorResult(success=False, speak="I couldn't find any open windows")

    search_target = _ALIASES.get(target, target)
    best = process.extractOne(search_target, titles.keys())
    if best is None or best[1] <= _MATCH_THRESHOLD:
        return ExecutorResult(success=False, speak=f"I couldn't find a window for {target}")

    import win32gui

    hwnd = titles[best[0]]
    win32gui.SetForegroundWindow(hwnd)
    return ExecutorResult(success=True, speak=f"Switching to {target}")


def _close_target(target: str) -> ExecutorResult:
    import win32con
    import win32gui

    titles = _list_visible_windows()

    if not titles:
        return ExecutorResult(success=False, speak="I couldn't find any open windows")

    search_target = _ALIASES.get(target, target)
    best = process.extractOne(search_target, titles.keys())
    if best is None or best[1] <= _MATCH_THRESHOLD:
        return ExecutorResult(success=False, speak=f"I couldn't find a window for {target}")

    hwnd = titles[best[0]]
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    return ExecutorResult(success=True, speak=f"Closing {target}")


def _lock_screen() -> ExecutorResult:
    ctypes.windll.user32.LockWorkStation()
    return ExecutorResult(success=True, speak="Locking the screen")


def _sleep() -> ExecutorResult:
    ctypes.windll.PowrProf.SetSuspendState(0, 1, 0)
    return ExecutorResult(success=True, speak="Going to sleep")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Minimize, maximize, close, switch windows, lock, or sleep the PC."""
    try:
        text = _strip_filler_prefixes(command_text.lower().strip()).strip(" .!?,")

        if "lock" in text:
            return _lock_screen()

        if "sleep" in text:
            return _sleep()

        match = _SWITCH_RE.search(text)
        if match:
            return _switch_to(match.group(1).strip())

        if "minimise" in text or "minimize" in text:
            return _minimize()

        if "maximise" in text or "maximize" in text:
            return _maximize()

        match = _CLOSE_TARGET_RE.match(text)
        if match:
            target = match.group(1).strip()
            if target in ("this", "this window", "the window", "it"):
                return _close()
            return _close_target(target)

        if "close" in text:
            return _close()

        return ExecutorResult(success=False, speak="I'm not sure what window action you want.")
    except Exception:
        logger.exception("window_executor failed")
        return ExecutorResult(success=False, speak="I couldn't do that with the window.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["minimise this", "lock the screen"]:
        print(cmd, "->", execute(cmd, _state))
