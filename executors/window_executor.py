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

import win32api
import win32process
from thefuzz import process

from core.text_utils import normalize_command
from executors.app_executor import _ALIASES
from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.window_executor")

_SWITCH_RE = re.compile(r"switch to\s+(.+)", re.IGNORECASE)
_CLOSE_TARGET_RE = re.compile(r"^(?:close|quit|exit)\s+(.+)$", re.IGNORECASE)
_MATCH_THRESHOLD = 60
# Fuzzy scores between _MATCH_THRESHOLD and this are usable but shaky
# (e.g. a mis-transcribed target like "pile explorer") - act only after
# a spoken confirmation instead of guessing silently.
_CONFIDENT_SCORE = 85


def _force_foreground(hwnd: int) -> None:
    """Bring hwnd to the foreground, working around Windows' foreground-switch lock.

    Plain SetForegroundWindow can fail (or, on this machine, raise pywintypes
    error 126 via the win32gui wrapper) unless the calling thread's input is
    attached to the target/current-foreground window's thread.
    """
    import win32con
    import win32gui

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

    user32 = ctypes.windll.user32
    cur_thread = win32api.GetCurrentThreadId()
    fg_hwnd = win32gui.GetForegroundWindow()
    fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd)
    target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)

    attached_fg = fg_thread != cur_thread and bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
    attached_target = target_thread != cur_thread and bool(
        user32.AttachThreadInput(cur_thread, target_thread, True)
    )
    try:
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached_fg:
            user32.AttachThreadInput(cur_thread, fg_thread, False)
        if attached_target:
            user32.AttachThreadInput(cur_thread, target_thread, False)


def _best_window_match(search_target: str, titles: dict[str, int]) -> tuple[str | None, bool]:
    """Find the window title best matching search_target.

    Returns (title, confident). Tries a substring match first - short
    queries like "code" or "chrome" can fuzzy-score worse against
    unrelated titles than an exact substring match would, the same
    pitfall as app_executor's fuzzy app lookup. Substring hits and
    high-scoring fuzzy hits are confident; low-scoring fuzzy hits are
    not, and callers should confirm before acting on them.
    """
    search_lower = search_target.lower()
    for title in titles:
        if search_lower in title.lower():
            return title, True

    best = process.extractOne(search_target, titles.keys())
    if best is not None and best[1] > _MATCH_THRESHOLD:
        return best[0], best[1] >= _CONFIDENT_SCORE
    return None, True


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
    best_title, confident = _best_window_match(search_target, titles)
    if best_title is None:
        from core.slot_extractor import extract_slot

        refined = extract_slot(target, "the name of the application or window to switch to")
        if refined:
            best_title, confident = _best_window_match(_ALIASES.get(refined, refined), titles)
    if best_title is None:
        return ExecutorResult(success=False, speak=f"I couldn't find a window for {target}")

    def _do_switch(title: str = best_title) -> ExecutorResult:
        _force_foreground(titles[title])
        return ExecutorResult(success=True, speak=f"Switching to {title}")

    if not confident:
        return ExecutorResult(
            success=True, speak="", confirm=f"Did you mean {best_title}?", on_confirm=_do_switch
        )
    return _do_switch()


def _close_target(target: str) -> ExecutorResult:
    import win32con
    import win32gui

    titles = _list_visible_windows()

    if not titles:
        return ExecutorResult(success=False, speak="I couldn't find any open windows")

    close_all = False
    cleaned_target = target
    if cleaned_target.lower().startswith("all "):
        close_all = True
        cleaned_target = cleaned_target[4:].strip()

    search_target = _ALIASES.get(cleaned_target, cleaned_target)

    # File Explorer windows all share the identical title "File Explorer",
    # so "closest match" arbitrarily picks one - close every match instead
    # of forcing the user to repeat the command per window.
    if search_target == "explorer":
        close_all = True

    if close_all:
        weak_match = False
        matching_titles = [title for title in titles if search_target.lower() in title.lower()]
        if not matching_titles:
            best_title, confident = _best_window_match(search_target, titles)
            matching_titles = [best_title] if best_title else []
            weak_match = not confident
        if not matching_titles:
            return ExecutorResult(success=False, speak=f"I couldn't find a window for {target}")

        # Speak the resolved window title(s), not the raw (possibly
        # mis-transcribed) spoken target - e.g. "pile explorer" should be
        # confirmed back as "File Explorer", what is actually being closed.
        count = len(matching_titles)
        distinct_names = sorted(set(matching_titles))
        # Fall back to the target *without* any "all " prefix - it reads as
        # part of the window name in the spoken question otherwise.
        label = distinct_names[0] if len(distinct_names) == 1 else cleaned_target

        def _do_close_all() -> ExecutorResult:
            for title in matching_titles:
                win32gui.PostMessage(titles[title], win32con.WM_CLOSE, 0, 0)
            speak = f"Closing {count} {label} windows" if count > 1 else f"Closing {label}"
            return ExecutorResult(success=True, speak=speak)

        if count > 1:
            return ExecutorResult(
                success=True,
                speak="",
                confirm=f"That will close {count} {label} windows. Go ahead?",
                on_confirm=_do_close_all,
            )
        if weak_match:
            return ExecutorResult(
                success=True, speak="", confirm=f"Did you mean {label}?", on_confirm=_do_close_all
            )
        return _do_close_all()

    best_title, confident = _best_window_match(search_target, titles)
    if best_title is None:
        from core.slot_extractor import extract_slot

        refined = extract_slot(target, "the name of the application or window to close")
        if refined:
            best_title, confident = _best_window_match(_ALIASES.get(refined, refined), titles)
    if best_title is None:
        return ExecutorResult(success=False, speak=f"I couldn't find a window for {target}")

    def _do_close_one(title: str = best_title) -> ExecutorResult:
        win32gui.PostMessage(titles[title], win32con.WM_CLOSE, 0, 0)
        return ExecutorResult(success=True, speak=f"Closing {title}")

    if not confident:
        return ExecutorResult(
            success=True, speak="", confirm=f"Close {best_title}?", on_confirm=_do_close_one
        )
    return _do_close_one()


def _lock_screen() -> ExecutorResult:
    ctypes.windll.user32.LockWorkStation()
    return ExecutorResult(success=True, speak="Locking the screen")


def _sleep() -> ExecutorResult:
    ctypes.windll.PowrProf.SetSuspendState(0, 1, 0)
    return ExecutorResult(success=True, speak="Going to sleep")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Minimize, maximize, close, switch windows, lock, or sleep the PC."""
    try:
        text = normalize_command(command_text)

        if "lock" in text:
            return _lock_screen()

        if "sleep" in text:
            # Suspends the PC immediately - a false trigger strands the user,
            # so always confirm.
            return ExecutorResult(
                success=True,
                speak="",
                confirm="That will put the computer to sleep. Should I?",
                on_confirm=_sleep,
            )

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
    for cmd in ["minimise this", "lock the screen", "switch to zzz nonexistent gibberish"]:
        print(cmd, "->", execute(cmd, _state))
