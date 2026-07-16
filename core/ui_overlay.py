"""Numbered on-screen overlay - MIMIR's answer to Windows Voice Access's
"show numbers" grid.

Draws a small numbered badge over each interactive element on screen, so
the user can act by number ("click 5", "5") instead of by name. This is a
double win: it's the signature visible Voice-Access UI, AND it massively
improves recognition reliability - transcribing the digit "five" is far
more robust than transcribing an arbitrary app-specific button label.

Rendering runs on MIMIR's shared Tk UI thread (ui_root). The overlay is a
fullscreen, always-on-top, transparent, click-through window: it shows
badges without capturing the mouse or blocking the app underneath (the
user acts by voice; MIMIR invokes the element via UIA, not a physical
click).

Coordinate model: UIA element rectangles are physical pixels, absolute
across the whole virtual desktop (can be negative - a monitor placed left
of or above the primary one has a negative origin; confirmed live on a
real 2-monitor setup). This process is DPI-unaware (like default
Python/Tk), so tkinter uses logical pixels and Windows scales them -
therefore physical rects are divided by one global monitor scale factor
before placement. At 100% scale that's a no-op. Known limitation: badges
are only pixel-accurate when every monitor shares the same DPI scale -
true per-monitor mixed-DPI alignment would need the whole process to
opt into per-monitor DPI awareness (SetProcessDpiAwarenessContext at
startup), which affects every MIMIR window, not just this one, so it's
deliberately out of scope here.
"""

from __future__ import annotations

import ctypes
import logging
import tkinter as tk

from ui.ui_root import get_root, run_on_ui_thread

logger = logging.getLogger("mimir.ui_overlay")

_TRANSPARENT_KEY = "#ff00ff"  # magic color rendered fully transparent + click-through
_BADGE_BG = "#1a73e8"
_BADGE_FG = "#ffffff"

_overlay: tk.Toplevel | None = None
_canvas: tk.Canvas | None = None
_origin: tuple[int, int] = (0, 0)

_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79


def _virtual_screen_bounds() -> tuple[int, int, int, int] | None:
    """(x, y, width, height) of the full virtual desktop spanning every
    monitor, in physical pixels - x/y can be negative. Returns None if
    the metrics can't be read (caller falls back to the primary monitor
    only)."""
    try:
        user32 = ctypes.windll.user32
        x = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        logger.debug("Couldn't read virtual screen metrics", exc_info=True)
    return None


def _scale_factor() -> float:
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        dpi = gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        user32.ReleaseDC(0, hdc)
        return (dpi / 96.0) or 1.0
    except Exception:
        return 1.0


def _make_click_through(window: tk.Toplevel) -> None:
    """Apply WS_EX_LAYERED | WS_EX_TRANSPARENT so the overlay never
    intercepts the mouse - clicks pass straight through to the app."""
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT
        )
    except Exception:
        logger.debug("Couldn't set click-through style on overlay", exc_info=True)


def _ensure_overlay() -> tuple[tk.Toplevel, tk.Canvas]:
    global _overlay, _canvas, _origin
    if _overlay is not None and _overlay.winfo_exists():
        return _overlay, _canvas  # type: ignore[return-value]

    root = get_root()
    window = tk.Toplevel(root)
    window.overrideredirect(True)  # no title bar / borders
    window.attributes("-topmost", True)
    window.attributes("-transparentcolor", _TRANSPARENT_KEY)
    window.configure(bg=_TRANSPARENT_KEY)

    bounds = _virtual_screen_bounds()
    if bounds is not None:
        vx, vy, vw, vh = bounds
    else:
        vx, vy = 0, 0
        vw, vh = window.winfo_screenwidth(), window.winfo_screenheight()
    _origin = (vx, vy)
    window.geometry(f"{vw}x{vh}+{vx}+{vy}")

    canvas = tk.Canvas(window, bg=_TRANSPARENT_KEY, highlightthickness=0, bd=0)
    canvas.pack(fill=tk.BOTH, expand=True)

    window.update_idletasks()
    _make_click_through(window)
    window.withdraw()

    _overlay, _canvas = window, canvas
    return window, canvas


def _draw_badge(canvas: tk.Canvas, number: int, x: int, y: int) -> None:
    label = str(number)
    pad_x, pad_y = 5, 2
    text_w = 8 * len(label)
    # Small pill anchored at the element's top-left, nudged to stay on-screen.
    x = max(0, x)
    y = max(0, y)
    canvas.create_rectangle(
        x, y, x + text_w + 2 * pad_x, y + 16 + 2 * pad_y,
        fill=_BADGE_BG, outline="#ffffff", width=1,
    )
    canvas.create_text(
        x + pad_x + text_w / 2, y + pad_y + 8,
        text=label, fill=_BADGE_FG, font=("Segoe UI", 9, "bold"),
    )


def show_numbers(numbered_rects: list[tuple[int, tuple[int, int, int, int]]]) -> None:
    """Show numbered badges. `numbered_rects` is a list of
    (number, (left, top, right, bottom)) in physical screen pixels.
    Safe to call from any thread."""

    def _do() -> None:
        window, canvas = _ensure_overlay()
        canvas.delete("all")
        scale = _scale_factor()
        origin_x, origin_y = _origin
        for number, rect in numbered_rects:
            left, top, _r, _b = rect
            # Canvas coords are relative to the overlay window's own
            # top-left, which now sits at the virtual desktop's origin
            # (possibly negative) rather than always (0, 0) - subtract it
            # before scaling, or anything on a monitor left of/above the
            # primary one would land off the canvas.
            x = int((left - origin_x) / scale)
            y = int((top - origin_y) / scale)
            _draw_badge(canvas, number, x, y)
        window.deiconify()
        window.lift()
        window.attributes("-topmost", True)

    run_on_ui_thread(_do)


def hide() -> None:
    """Hide the overlay. Safe to call from any thread (no-op if not shown)."""

    def _do() -> None:
        if _overlay is not None and _overlay.winfo_exists():
            if _canvas is not None:
                _canvas.delete("all")
            _overlay.withdraw()

    run_on_ui_thread(_do)


def is_showing() -> bool:
    return _overlay is not None and _overlay.winfo_exists() and _overlay.state() == "normal"


if __name__ == "__main__":
    import time

    # Visual self-test: badges 1-6 across the screen for 6 seconds.
    demo = [
        (1, (100, 100, 200, 140)),
        (2, (500, 300, 620, 340)),
        (3, (900, 200, 1000, 240)),
        (4, (1400, 600, 1520, 640)),
        (5, (300, 800, 420, 840)),
        (6, (1700, 950, 1820, 990)),
    ]
    print("Showing overlay badges for 6 seconds - look at your screen...")
    show_numbers(demo)
    time.sleep(6)
    hide()
    time.sleep(0.5)
    print("Done.")
