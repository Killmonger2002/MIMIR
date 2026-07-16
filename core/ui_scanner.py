"""Semantic UI scanner - reads interactive elements from every visible
top-level window on screen (all monitors, not just the foreground one)
via Windows UI Automation (UIA), so MIMIR can act on things by NAME
("click Search", "type ... in the address bar") without coordinates,
screenshots, or the user knowing anything about the layout.

This is the same mechanism Windows Voice Access uses. Both native apps
AND browsers (Chrome/Edge expose their accessibility tree through UIA)
are read through one path here - no Playwright/CDP setup required. A
Playwright browser path is possible later as an optional enhancement,
but UIA already covers browsers well enough to rival Voice Access, so
it's deliberately not a dependency.

Whole-screen scope (2026-07-16): originally scoped to just the foreground
window (Desktop(...).window(active_only=True)), which meant "show
numbers" couldn't number desktop icons or other visible windows, and
silently missed anything on a second monitor. Now walks every visible
top-level window UIA reports (this already spans all monitors and
includes the desktop's own icon layer - confirmed live on a real 2-monitor
setup) - foreground window first with a generous element budget, other
windows/desktop given a smaller guaranteed slice each, both capped so a
handful of heavy windows (a complex webpage's UIA tree alone measured
1000+ nodes / ~1.2s on one real window) can't blow the total scan time or
element count without bound. This is a real latency/coverage trade-off,
not free - see _MAX_WINDOWS/_MAX_ELEMENTS_* below.

Key design choice vs. the original plan: each scanned UIElement keeps a
live reference to its pywinauto wrapper (`_wrapper`) rather than trying
to re-find the element later by window handle. Most UIA elements share
their top window's hwnd, so handle-based re-lookup is unreliable; scan
and act happen milliseconds apart in one call, so the live wrapper is
both simpler and correct. UIA invoke()/value-set act on an element via
its own wrapper regardless of which window currently has focus, so
acting on a numbered element from a non-foreground window works the same
way as any other.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("mimir.ui_scanner")

# Names from the most recent scan, so STT can bias transcription toward
# what's actually on screen (see core/vocabulary.py) - recognizing an
# app-specific button label is far more reliable when Whisper is primed
# with it. Populated by scan(); read by recent_element_names().
_last_scan_names: list[str] = []
_last_scan_time: float = 0.0

# Control types worth acting on (clicking / typing / toggling). Bare
# "Text" is excluded - status-bar labels like "37 characters" are for
# reading, not clicking. "Document"/"Edit" ARE included: they're the
# typeable text surfaces.
INTERACTIVE_TYPES = {
    "Button", "Edit", "ComboBox", "CheckBox", "RadioButton",
    "MenuItem", "ListItem", "Hyperlink", "Tab", "DataItem",
    "Document", "SplitButton", "TreeItem",
}

# Hard caps for the whole-screen scan. A big web page can expose 1000+
# UIA nodes on its own (measured live) - without bounds, "show numbers"
# on a desktop with a browser open would both blow the LLM matcher's
# token budget and take multiple seconds. The foreground window gets a
# generous share since it's still the most likely target; every other
# window (including the desktop's icon layer) gets a smaller guaranteed
# slice so it isn't starved out entirely when the foreground app is huge.
_MAX_ELEMENTS_TOTAL = 80
_MAX_ELEMENTS_FOREGROUND = 40
_MAX_ELEMENTS_OTHER_WINDOW = 12
_MAX_WINDOWS = 8

# Windows report a minimized window's rectangle at this kind of off-screen
# sentinel position - filter those out rather than scanning/numbering
# something invisible.
_MINIMIZED_COORD_THRESHOLD = -10000

_BROWSER_PROCESSES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}


@dataclass
class UIElement:
    """One interactive element on screen. `_wrapper` is the live pywinauto
    control used to act on it - kept out of repr/compare since it's an
    opaque COM object, not data."""

    id: int
    type: str
    name: str
    value: str
    auto_id: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # (left, top, right, bottom) - for future overlay/disambiguation
    _wrapper: Any = field(default=None, repr=False, compare=False)


def foreground_process_name() -> str:
    """Lowercased process name of the foreground window (e.g. 'chrome.exe'),
    or '' if it can't be determined."""
    try:
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        return psutil.Process(pid).name().lower()
    except Exception:
        logger.debug("Couldn't resolve foreground process", exc_info=True)
        return ""


def is_browser_focused() -> bool:
    return foreground_process_name() in _BROWSER_PROCESSES


def _safe_value(wrapper) -> str:
    """Best-effort current value of an element (an Edit's text, a
    ComboBox's selection). UIA exposes this inconsistently across control
    types, so every access is guarded."""
    for getter in ("get_value", "window_text"):
        try:
            fn = getattr(wrapper, getter, None)
            if fn is None:
                continue
            val = fn()
            if val:
                return str(val).strip()
        except Exception:
            continue
    return ""


def _rect_tuple(wrapper) -> tuple[int, int, int, int]:
    try:
        r = wrapper.rectangle()
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return (0, 0, 0, 0)


def _is_onscreen_rect(rect) -> bool:
    try:
        if rect.width() <= 0 or rect.height() <= 0:
            return False
        if rect.left <= _MINIMIZED_COORD_THRESHOLD or rect.top <= _MINIMIZED_COORD_THRESHOLD:
            return False
        return True
    except Exception:
        return False


def _enumerate_top_level_windows() -> list:
    """Return live top-level window wrappers for everything actually
    visible on screen right now - across every monitor, not just the
    foreground window. Foreground window first (so it gets priority if
    caps are hit), remaining windows in whatever order UIA's desktop
    element reports them (observed to be roughly z-order), capped to
    _MAX_WINDOWS so a desktop with many windows open can't make one
    "show numbers" call scan an unbounded number of them.
    """
    try:
        from pywinauto import Desktop

        candidates = Desktop(backend="uia").windows(visible_only=True)
    except Exception:
        logger.exception("Failed to enumerate top-level windows")
        return []

    fg_hwnd = None
    try:
        import win32gui

        fg_hwnd = win32gui.GetForegroundWindow()
    except Exception:
        pass

    windows = []
    for w in candidates:
        try:
            if _is_onscreen_rect(w.rectangle()):
                windows.append(w)
        except Exception:
            continue

    def _is_foreground(w) -> bool:
        try:
            return w.handle == fg_hwnd
        except Exception:
            return False

    def _is_desktop_icon_layer(w) -> bool:
        # Locale-independent (unlike the "Program Manager" window_text) -
        # desktop icons live under one of these two window classes
        # depending on Windows version/config.
        try:
            return w.class_name() in ("Progman", "WorkerW")
        except Exception:
            return False

    def _priority(w) -> int:
        if _is_foreground(w):
            return 0
        if _is_desktop_icon_layer(w):
            # Second, not wherever z-order happens to sort it: on a
            # cluttered desktop with several windows open, the icon layer
            # otherwise reliably lands last and gets truncated by
            # _MAX_WINDOWS/_MAX_ELEMENTS_TOTAL before ever being scanned -
            # observed live, and desktop icons are exactly what "the whole
            # screen" means to a user with several windows open plus a
            # desktop full of shortcuts (see the reported bug).
            return 1
        return 2

    windows.sort(key=_priority)
    return windows[:_MAX_WINDOWS]


def _scan_window(window, start_idx: int, budget: int) -> tuple[list[UIElement], int]:
    """Scan one window's descendants, assigning ids starting at
    `start_idx`, keeping at most `budget` elements from it. Returns
    (elements, next_idx)."""
    try:
        descendants = window.descendants()
    except Exception:
        logger.debug("Failed to enumerate descendants for %r", window, exc_info=True)
        return [], start_idx

    elements: list[UIElement] = []
    idx = start_idx
    kept = 0
    for wrapper in descendants:
        if kept >= budget:
            break
        try:
            info = wrapper.element_info
            if not info.visible or not info.enabled:
                continue
            ctrl = str(info.control_type)
            if ctrl not in INTERACTIVE_TYPES:
                continue
            name = (info.name or "").strip()
            value = _safe_value(wrapper)
            if not name and not value:
                continue  # unlabelled AND valueless - nothing to match a spoken target against
            elements.append(
                UIElement(
                    id=idx,
                    type=ctrl,
                    name=name,
                    value=value,
                    auto_id=(info.automation_id or ""),
                    rect=_rect_tuple(wrapper),
                    _wrapper=wrapper,
                )
            )
            idx += 1
            kept += 1
        except Exception:
            continue

    return elements, idx


def scan() -> list[UIElement]:
    """Scan every visible top-level window on screen (all monitors, plus
    the desktop's own icon layer) and return their interactive elements,
    the foreground window's elements numbered first.

    Unified UIA path for both desktop apps and browsers. Returns [] if
    nothing usable is found (caller should tell the user it can't read
    the screen). Never raises.
    """
    windows = _enumerate_top_level_windows()
    if not windows:
        return []

    elements: list[UIElement] = []
    idx = 1
    for i, window in enumerate(windows):
        if idx > _MAX_ELEMENTS_TOTAL:
            break
        per_window_budget = _MAX_ELEMENTS_FOREGROUND if i == 0 else _MAX_ELEMENTS_OTHER_WINDOW
        remaining = _MAX_ELEMENTS_TOTAL - (idx - 1)
        found, idx = _scan_window(window, idx, min(per_window_budget, remaining))
        elements.extend(found)

    global _last_scan_names, _last_scan_time
    _last_scan_names = [el.name for el in elements if el.name]
    _last_scan_time = time.time()

    logger.info("UI scan found %d interactive elements across %d windows", len(elements), len(windows))
    return elements


def recent_element_names(max_age_sec: float = 30.0) -> list[str]:
    """Element names from the most recent scan, if it was recent enough to
    still reflect what's on screen. Empty if no recent scan - used to
    context-bias STT toward on-screen labels during UI control."""
    if not _last_scan_names or (time.time() - _last_scan_time) > max_age_sec:
        return []
    return list(_last_scan_names)


def snapshot_text(elements: list[UIElement]) -> str:
    """Compact, LLM-friendly rendering of the element list (also handy for
    debugging / the 'show numbers' style readout)."""
    lines = []
    for el in elements:
        line = f'[{el.id}] {el.type} "{el.name}"'
        if el.value:
            line += f' value="{el.value}"'
        lines.append(line)
    return "\n".join(lines)


if __name__ == "__main__":
    import subprocess
    import sys
    import time

    logging.basicConfig(level=logging.INFO)
    # Launch Notepad as a known target if no window arg given.
    proc = None
    if "--no-launch" not in sys.argv:
        proc = subprocess.Popen(["notepad.exe"])
        time.sleep(1.5)
    try:
        els = scan()
        print(f"Foreground process: {foreground_process_name()}  browser={is_browser_focused()}")
        print(snapshot_text(els))
        print(f"\n{len(els)} interactive elements.")
    finally:
        if proc is not None:
            proc.terminate()
