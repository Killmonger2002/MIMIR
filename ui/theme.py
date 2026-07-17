"""Shared dark visual theme for MIMIR's Tk windows.

Tkinter has no native dark theme or rounded-corner widgets, so this module
hand-rolls the handful of pieces every settings/wizard window needs (cards,
step indicators, progress bars, status dots, pill buttons) once, so every
window that imports it looks like the same product instead of a patchwork
of ad-hoc `tk.Label`/`tk.Button` calls with their own colors.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

BG_WINDOW = "#1b1b1e"
BG_CARD = "#242428"
BG_INSET = "#333338"
BORDER = "#3a3a40"

FG_TEXT = "#f2f2f4"
FG_MUTED = "#9a9aa2"

ACCENT_BLUE = "#4287f5"
ACCENT_GREEN = "#4caf50"
ACCENT_ORANGE = "#d99a3f"
ACCENT_RED = "#e05555"

FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_SECTION = ("Segoe UI", 11, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_SMALL_BOLD = ("Segoe UI", 9, "bold")

_PROGRESSBAR_STYLE_READY = False


def apply_window_theme(window: tk.Misc) -> None:
    """Set the standard dark background on a Toplevel/Tk window."""
    window.configure(bg=BG_WINDOW)


def ensure_progressbar_style() -> None:
    """Register the dark/green ttk progress bar style once per process."""
    global _PROGRESSBAR_STYLE_READY
    if _PROGRESSBAR_STYLE_READY:
        return
    style = ttk.Style()
    # 'clam' is the only built-in ttk theme that honors custom trough/bar
    # colors on Windows - the default 'vista' theme ignores them.
    style.theme_use("clam")
    style.configure(
        "Mimir.Horizontal.TProgressbar",
        troughcolor=BG_INSET,
        bordercolor=BG_INSET,
        background=ACCENT_GREEN,
        lightcolor=ACCENT_GREEN,
        darkcolor=ACCENT_GREEN,
        thickness=10,
    )
    _PROGRESSBAR_STYLE_READY = True


def ensure_thin_progressbar_style() -> None:
    """A slimmer variant of the dark/green progress bar (thickness 6 vs
    10) for space-constrained widgets like the transcript bar - a
    separate style name from Mimir.Horizontal.TProgressbar since ttk
    styles are process-global and other windows still want the taller
    bar."""
    style = ttk.Style()
    style.theme_use("clam")
    style.configure(
        "MimirThin.Horizontal.TProgressbar",
        troughcolor=BG_INSET,
        bordercolor=BG_INSET,
        background=ACCENT_GREEN,
        lightcolor=ACCENT_GREEN,
        darkcolor=ACCENT_GREEN,
        thickness=6,
    )


def card(parent: tk.Misc, **kwargs) -> tk.Frame:
    """A raised panel: the container every wizard step/section lives in."""
    frame = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1, **kwargs)
    return frame


def title_row(parent: tk.Misc, title: str, right_text: str = "") -> tuple[tk.Frame, tk.Label]:
    """Header row: '<gear> Title' on the left, small muted text on the
    right (e.g. "Step 2 of 4"). Returns (row, right_label) so callers can
    update the right-hand text as a wizard progresses."""
    row = tk.Frame(parent, bg=BG_CARD)
    tk.Label(row, text=f"⚙  {title}", font=FONT_TITLE, bg=BG_CARD, fg=FG_TEXT).pack(side=tk.LEFT)
    right_label = tk.Label(row, text=right_text, font=FONT_SMALL, bg=BG_CARD, fg=FG_MUTED)
    right_label.pack(side=tk.RIGHT)
    return row, right_label


def section_heading(parent: tk.Misc, text: str) -> tk.Label:
    return tk.Label(parent, text=text, font=FONT_SECTION, bg=BG_CARD, fg=FG_TEXT, anchor="w", justify="left")


def body_text(parent: tk.Misc, text: str, wraplength: int = 420) -> tk.Label:
    return tk.Label(
        parent, text=text, font=FONT_BODY, bg=BG_CARD, fg=FG_MUTED, anchor="w", justify="left", wraplength=wraplength
    )


def note_box(parent: tk.Misc, text: str, wraplength: int = 400) -> tk.Frame:
    """A subtle inset box for a small aside note, matching the calibration
    wizard's "re-run this step if..." callouts."""
    frame = tk.Frame(parent, bg=BG_INSET, highlightbackground=BORDER, highlightthickness=1)
    tk.Label(
        frame, text=text, font=FONT_SMALL, bg=BG_INSET, fg=FG_MUTED, wraplength=wraplength, justify="left", anchor="w"
    ).pack(padx=10, pady=8, fill=tk.X)
    return frame


def labeled_progress(parent: tk.Misc, label: str) -> tuple[tk.Frame, ttk.Progressbar, tk.Label]:
    """A titled progress bar row: label + percentage on one line, bar below.
    Returns (container, progressbar, value_label) - call value_label.configure
    and progressbar['value'] = ... to update."""
    ensure_progressbar_style()
    frame = tk.Frame(parent, bg=BG_CARD)
    header = tk.Frame(frame, bg=BG_CARD)
    header.pack(fill=tk.X)
    tk.Label(header, text=label, font=FONT_BODY, bg=BG_CARD, fg=FG_TEXT).pack(side=tk.LEFT)
    value_label = tk.Label(header, text="0%", font=FONT_BODY, bg=BG_CARD, fg=FG_TEXT)
    value_label.pack(side=tk.RIGHT)
    bar = ttk.Progressbar(
        frame, style="Mimir.Horizontal.TProgressbar", orient="horizontal", mode="determinate", maximum=100
    )
    bar.pack(fill=tk.X, pady=(4, 0))
    return frame, bar, value_label


def status_dot(parent: tk.Misc, text: str, color: str = ACCENT_GREEN) -> tk.Frame:
    """One '<colored dot> status text' row."""
    row = tk.Frame(parent, bg=BG_CARD)
    dot = tk.Canvas(row, width=10, height=10, bg=BG_CARD, highlightthickness=0)
    dot.create_oval(1, 1, 9, 9, fill=color, outline="")
    dot.pack(side=tk.LEFT, padx=(0, 8))
    tk.Label(row, text=text, font=FONT_BODY, bg=BG_CARD, fg=FG_TEXT, anchor="w", justify="left", wraplength=380).pack(
        side=tk.LEFT, fill=tk.X
    )
    return row


def status_dot_dynamic(parent: tk.Misc) -> tuple[tk.Frame, Callable[[str, str], None]]:
    """Like status_dot(), but returns (row, update_fn) so a live-polling
    caller can recolor/retext the same row instead of rebuilding widgets
    on every tick."""
    row = tk.Frame(parent, bg=BG_CARD)
    dot = tk.Canvas(row, width=10, height=10, bg=BG_CARD, highlightthickness=0)
    oval = dot.create_oval(1, 1, 9, 9, fill=ACCENT_GREEN, outline="")
    dot.pack(side=tk.LEFT, padx=(0, 8))
    label = tk.Label(row, text="", font=FONT_BODY, bg=BG_CARD, fg=FG_TEXT, anchor="w", justify="left", wraplength=380)
    label.pack(side=tk.LEFT, fill=tk.X)

    def update(text: str, color: str = ACCENT_GREEN) -> None:
        dot.itemconfig(oval, fill=color)
        label.configure(text=text)

    return row, update


def primary_button(parent: tk.Misc, text: str, command: Callable[[], None] | None = None, width: int = 14) -> tk.Button:
    """Solid light pill button - the main forward action (Continue, Save)."""
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=FG_TEXT,
        fg="#111113",
        activebackground="#d8d8db",
        activeforeground="#111113",
        relief="flat",
        bd=0,
        padx=16,
        pady=6,
        width=width,
        font=FONT_SMALL_BOLD,
        cursor="hand2",
    )


def secondary_button(parent: tk.Misc, text: str, command: Callable[[], None] | None = None, width: int = 14) -> tk.Button:
    """Bordered outline button - a secondary action (Record, Activate)."""
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=BG_CARD,
        fg=FG_TEXT,
        activebackground=BG_INSET,
        activeforeground=FG_TEXT,
        relief="flat",
        bd=1,
        highlightbackground=BORDER,
        highlightthickness=1,
        padx=14,
        pady=6,
        width=width,
        font=FONT_SMALL,
        cursor="hand2",
    )


def ghost_button(parent: tk.Misc, text: str, command: Callable[[], None] | None = None) -> tk.Button:
    """Flat, borderless text-only button - a low-emphasis action (Back)."""
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=BG_CARD,
        fg=ACCENT_BLUE,
        activebackground=BG_CARD,
        activeforeground=ACCENT_BLUE,
        relief="flat",
        bd=0,
        padx=8,
        pady=6,
        font=FONT_SMALL_BOLD,
        cursor="hand2",
    )


def styled_option_menu(
    parent: tk.Misc, var: tk.StringVar, choices: list[str], command: Callable[[str], None] | None = None
) -> tk.OptionMenu:
    """A tk.OptionMenu re-themed to match the dark card style - the
    stock widget renders with the OS's light default and would otherwise
    be the one visibly out-of-place control on every card."""
    if not choices:
        choices = [var.get() or ""]
    menu = tk.OptionMenu(parent, var, *choices, command=command)
    menu.configure(
        bg=BG_INSET,
        fg=FG_TEXT,
        activebackground=BORDER,
        activeforeground=FG_TEXT,
        highlightthickness=1,
        highlightbackground=BORDER,
        relief="flat",
        bd=0,
        font=FONT_BODY,
        anchor="w",
        padx=10,
        pady=4,
        cursor="hand2",
    )
    menu["menu"].configure(bg=BG_INSET, fg=FG_TEXT, activebackground=ACCENT_BLUE, activeforeground="#ffffff", font=FONT_BODY)
    return menu


class StepIndicator(tk.Frame):
    """Horizontal numbered-circle progress track: done steps get a
    checkmark on a green fill, the current step is a filled blue circle,
    future steps are hollow gray circles - all connected by a line."""

    _RADIUS = 12
    _GAP = 70

    def __init__(self, parent: tk.Misc, labels: list[str], current: int = 0) -> None:
        super().__init__(parent, bg=BG_CARD)
        self._labels = labels
        width = max(1, len(labels) - 1) * self._GAP + self._RADIUS * 2 + 20
        self._canvas = tk.Canvas(self, width=width, height=56, bg=BG_CARD, highlightthickness=0)
        self._canvas.pack()
        self.set_current(current)

    def set_current(self, current: int) -> None:
        c = self._canvas
        c.delete("all")
        r = self._RADIUS
        cy = 16
        n = len(self._labels)
        xs = [20 + r + i * self._GAP for i in range(n)]

        if n > 1:
            c.create_line(xs[0], cy, xs[-1], cy, fill=BORDER, width=2)
            done_end = xs[min(current, n - 1)] if current > 0 else xs[0]
            if current > 0:
                c.create_line(xs[0], cy, done_end, cy, fill=ACCENT_GREEN, width=2)

        for i, (x, label) in enumerate(zip(xs, self._labels)):
            if i < current:
                c.create_oval(x - r, cy - r, x + r, cy + r, fill=ACCENT_GREEN, outline="")
                c.create_text(x, cy, text="✓", fill="#0c0c0d", font=FONT_SMALL_BOLD)
                label_color = FG_MUTED
            elif i == current:
                c.create_oval(x - r, cy - r, x + r, cy + r, fill=ACCENT_BLUE, outline="")
                c.create_text(x, cy, text=str(i + 1), fill="#ffffff", font=FONT_SMALL_BOLD)
                label_color = FG_TEXT
            else:
                c.create_oval(x - r + 1, cy - r + 1, x + r - 1, cy + r - 1, fill=BG_CARD, outline=BORDER, width=2)
                c.create_text(x, cy, text=str(i + 1), fill=FG_MUTED, font=FONT_SMALL)
                label_color = FG_MUTED
            c.create_text(x, cy + r + 12, text=label, fill=label_color, font=FONT_SMALL)
