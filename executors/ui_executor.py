"""Semantic UI control executor - "click Search", "type hello in the
address bar", "check remember me", "search for Python tutorials".

Plugs into the standard MIMIR pipeline. On each call it scans the
foreground window (core.ui_scanner), resolves the spoken target to an
element (core.element_matcher, tiers A/B/C), and acts on it
(core.action_executor). This is MIMIR's answer to Windows Voice Access's
"click <name>" - same UIA mechanism, driven by our own STT/routing.

Deterministic-first: simple "click X" / "type X in Y" commands resolve by
exact/fuzzy name with no LLM at all. The LLM tier only engages for
commands that name no findable element or need multi-step planning
("search for X" = type then click) - and even then only up to the user's
active tier ceiling.
"""

from __future__ import annotations

import logging
import re

from core.action_executor import execute_actions
from core.element_matcher import match_all_exact, match_fuzzy, match_llm
from core.text_utils import strip_filler_prefixes
from core.ui_scanner import UIElement, scan
from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.ui_executor")


def _clean(text: str) -> str:
    """Strip leading filler ("please", "ok,") and trailing sentence
    punctuation, WITHOUT lowercasing - typed text must keep its original
    case (e.g. "type John@example.com in the email field").

    Bug this fixes: every anchored pattern here (^show ...$, etc.) failed
    on real STT output like "show numbers." (trailing period), silently
    falling through to the LLM fallback for what should have been an
    instant, deterministic match - observed live 2026-07-16."""
    return strip_filler_prefixes(text.strip()).strip().rstrip(".!?,;:").strip()

# Number-word map for parsing spoken element numbers ("click five" when
# Whisper writes the word instead of the digit). Covers 0-59 - the scanner
# caps at 50 elements, so this is comfortably enough.
_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}

# The elements shown under the current number overlay (id -> UIElement),
# so a follow-up "click 5" acts on exactly what was numbered, without
# re-scanning (which could renumber if the screen shifted).
_numbered: dict[int, UIElement] = {}


def _parse_number(text: str) -> int | None:
    """Extract a spoken element number: "5", "click 5", "click on 9",
    "number 7", "click number 12", "five", "twenty three". Returns None
    if the text isn't a bare number selection.

    Bug this fixes: "click on 9" (very natural phrasing - _CLICK_RE
    already tolerates the same "on") fell through this parser entirely
    (only "click " was stripped, leaving "on 9"), so it never reached
    _act_by_number and instead got treated as a search for an element
    literally named "9" - always failing with "I couldn't find that on
    the screen" even though the overlay was showing and #9 existed.
    Observed live, 2026-07-16."""
    t = text.lower().strip().strip(".!?")
    t = re.sub(r"^(click|press|tap|select|choose)\s+(?:on\s+)?", "", t)
    t = re.sub(r"^number\s+", "", t)
    t = t.strip()
    if t.isdigit():
        return int(t)
    words = t.split()
    if len(words) == 1 and words[0] in _ONES:
        return _ONES[words[0]]
    if len(words) == 2 and words[0] in _TENS and words[1] in _ONES and _ONES[words[1]] < 10:
        return _TENS[words[0]] + _ONES[words[1]]
    if len(words) == 1 and words[0] in _TENS:
        return _TENS[words[0]]
    return None

# "click (the) (button) search"
_CLICK_RE = re.compile(
    r"^(?:click|press|tap|choose)\s+(?:on\s+)?(?:the\s+)?(?:button\s+|link\s+|icon\s+|menu\s+|tab\s+)?(.+)$",
    re.IGNORECASE,
)
# "type <text> in/into/on (the) <target>"
_TYPE_RE = re.compile(
    r"^(?:type|write|enter|input|fill)\s+(.+?)\s+(?:in|into|on)\s+(?:the\s+)?(.+)$",
    re.IGNORECASE,
)
# "check/uncheck (the) <target>"
_CHECK_RE = re.compile(r"^(check|uncheck|tick|untick)\s+(?:the\s+)?(.+)$", re.IGNORECASE)
# "select <option> from (the) <target>"
_SELECT_RE = re.compile(r"^select\s+(.+?)\s+from\s+(?:the\s+)?(.+)$", re.IGNORECASE)

# "show numbers" / "show me the buttons" / "show me all the clickable
# things on the screen" / "put labels on the screen" / "what can I
# click" - the natural-language spread users actually say, not just the
# terse canonical phrase. "show\s*(me\s+)?" (note \s*, not \s+) also
# tolerates "shownumbers" - observed live as an STT word-merge artifact.
# "put ... on screen" requires the "on screen" tail (unlike "show ...",
# where it's optional) - "put" alone is too generic a verb to trigger on
# without it.
_SHOW_TARGET = r"(numbers?|labels?|grid|buttons?|clickable\s+(buttons?|things?|elements?|items?))"
_ON_SCREEN = r"(\s+on\s+(the\s+)?screen)"
_SHOW_NUMBERS_RE = re.compile(
    rf"^show\s*(me\s+)?(all\s+(of\s+)?)?(the\s+)?{_SHOW_TARGET}{_ON_SCREEN}?$"
    rf"|^put\s+(me\s+)?(all\s+(of\s+)?)?(the\s+)?{_SHOW_TARGET}{_ON_SCREEN}$"
    rf"|^what\s+can\s+i\s+click$",
    re.IGNORECASE,
)
# "height numbers" alongside "hide" - observed live as an STT mishearing
# of "hide numbers" (tiny.en homophone confusion), the same class of fix
# as the "Codex"/"codecs" case elsewhere in this project.
_HIDE_NUMBERS_RE = re.compile(
    rf"^(hide|height)\s*(me\s+)?(all\s+(of\s+)?)?(the\s+)?{_SHOW_TARGET}{_ON_SCREEN}?$",
    re.IGNORECASE,
)
# Meta/help query about on-screen control itself, not an action attempt -
# "what on-screen commands can you perform" was observed live falling
# through to the action-resolution path and failing with "I couldn't
# find that on the screen", which is actively misleading for a question
# that was never about finding an element.
_UI_HELP_RE = re.compile(
    r"^what\s+(on[\s-]?screen\s+)?(commands?|actions?)\s+can\s+(you|i)\s+(perform|do|use)\??$"
    r"|^what\s+can\s+you\s+do\s+on\s+(the\s+)?screen\??$",
    re.IGNORECASE,
)
_UI_HELP_TEXT = (
    "On screen, I can click buttons and links, type into fields, check boxes, "
    "select from dropdowns, and show numbered labels on everything clickable - "
    "say 'show numbers', or just name what you want, like 'click search'."
)

# Patterns the intent router uses to send commands here. This list is the
# single source of truth - core/intent_router.py references these same
# compiled regexes' .pattern (see _system_executor._QUIT_RE for the
# established convention) instead of keeping a second hand-copied list,
# which is exactly what let the router and executor drift out of sync
# for "show me the clickable buttons on the screen" (observed live,
# 2026-07-16: matched neither list, fell through to window_executor).
PATTERNS = [
    r"^(click|press|tap|choose)\s+.+",
    r"^(type|write|enter|input|fill)\s+.+\b(in|into|on)\b\s+.+",
    r"^(check|uncheck|tick|untick)\s+.+",
    r"^select\s+.+\bfrom\b\s+.+",
    r"\b(click|press|tap)\b.*\b(button|link|icon|checkbox|check box|menu|tab|field|box)\b",
    _SHOW_NUMBERS_RE.pattern,
    _HIDE_NUMBERS_RE.pattern,
    _UI_HELP_RE.pattern,
    # Bare number selection while the overlay is up ("5", "number 7",
    # "twenty three"). Standalone numbers aren't used by other executors.
    r"^(number\s+)?\d{1,3}$",
    r"^number\s+\w+$",
]

# When a spoken target matches several elements, prefer the control type
# that fits the verb: "click search" -> the Search *button*, "type ... in
# search" -> the Search *edit field*.
_CLICK_PREF = ["Button", "Hyperlink", "MenuItem", "SplitButton", "Tab", "ListItem", "CheckBox", "RadioButton"]
_TYPE_PREF = ["Edit", "ComboBox", "Document"]


def _prefer(candidates: list[UIElement], pref: list[str]) -> UIElement:
    return sorted(candidates, key=lambda e: pref.index(e.type) if e.type in pref else 99)[0]


def _resolve(target: str, elements: list[UIElement], pref: list[str]) -> tuple[UIElement | None, bool]:
    """Resolve a spoken target to one element. Returns (element,
    was_ambiguous). Exact matches win; ties are broken by verb-appropriate
    control type; otherwise fall back to fuzzy."""
    exact = match_all_exact(target, elements)
    if len(exact) == 1:
        return exact[0], False
    if len(exact) > 1:
        return _prefer(exact, pref), True
    return match_fuzzy(target, elements), False


def _act_by_number(n: int) -> ExecutorResult:
    """Click the element the overlay numbered `n`, then dismiss the overlay."""
    from core import ui_overlay

    el = _numbered.get(n)
    if el is None:
        return ExecutorResult(success=False, speak=f"I don't see a number {n} on the screen.")
    confirmation = execute_actions([{"action": "click", "element": el.id}], [el])
    ui_overlay.hide()
    _numbered.clear()
    return ExecutorResult(success=True, speak=confirmation)


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Resolve and act on an on-screen UI target."""
    try:
        from core import ui_overlay

        text = _clean(command_text)

        # --- Meta/help query, not an action attempt ---
        if _UI_HELP_RE.match(text):
            return ExecutorResult(success=True, speak=_UI_HELP_TEXT)

        # --- Overlay controls (these don't need a fresh element scan) ---
        if _HIDE_NUMBERS_RE.match(text):
            ui_overlay.hide()
            _numbered.clear()
            return ExecutorResult(success=True, speak="Hidden.")

        # A bare number selects a badge - but only while the overlay is up,
        # so "5" said in any other context can't accidentally click things.
        if _numbered:
            n = _parse_number(text)
            if n is not None:
                return _act_by_number(n)

        if _SHOW_NUMBERS_RE.match(text):
            elements = scan()
            if not elements:
                return ExecutorResult(success=False, speak="I can't read anything to number on this screen.")
            _numbered.clear()
            _numbered.update({el.id: el for el in elements})
            ui_overlay.show_numbers([(el.id, el.rect) for el in elements])
            # Listen immediately for the number, no wake word needed.
            return ExecutorResult(
                success=True,
                speak=f"Showing {len(elements)} numbers. Say a number to click it.",
                needs_followup=True,
            )

        # --- Named-target control (requires a fresh scan) ---
        elements = scan()
        if not elements:
            return ExecutorResult(
                success=False,
                speak="I can't read anything I can click on this screen.",
            )

        click_m = _CLICK_RE.match(text)
        type_m = _TYPE_RE.match(text)
        check_m = _CHECK_RE.match(text)
        select_m = _SELECT_RE.match(text)

        actions: list[dict] = []

        if type_m:
            type_text, target = type_m.group(1).strip(), type_m.group(2).strip()
            el, _amb = _resolve(target, elements, _TYPE_PREF)
            if el:
                actions = [
                    {"action": "focus", "element": el.id},
                    {"action": "type", "element": el.id, "text": type_text},
                ]
        elif select_m:
            option, target = select_m.group(1).strip(), select_m.group(2).strip()
            el, _amb = _resolve(target, elements, _TYPE_PREF)
            if el:
                actions = [{"action": "select", "element": el.id, "option": option}]
        elif check_m:
            verb, target = check_m.group(1).lower(), check_m.group(2).strip()
            el, _amb = _resolve(target, elements, _CLICK_PREF)
            if el:
                act = "uncheck" if verb in ("uncheck", "untick") else "check"
                actions = [{"action": act, "element": el.id}]
        elif click_m:
            target = click_m.group(1).strip()
            el, _amb = _resolve(target, elements, _CLICK_PREF)
            if el:
                actions = [{"action": "click", "element": el.id}]

        # No deterministic match (or a command like "search for X" that
        # names no element directly) -> hand the whole thing to the LLM,
        # which can also plan multi-step actions (type then click).
        llm_guessed = False
        if not actions:
            actions = match_llm(text, elements)
            llm_guessed = True

        if not actions:
            return ExecutorResult(
                success=False,
                speak="I couldn't find that on the screen. Try naming the button or field exactly.",
            )

        # Safety guard: an LLM plan with SEVERAL independent clicks (no
        # type action tying them to one target) means the model is
        # guessing across candidates, not executing a clear single
        # intent - a legitimate "search for X" plan is exactly one type
        # + one click. Observed live, 2026-07-16: a garbled command
        # ("Stroke clickable buttons on the screen") reached this path
        # and the model clicked three unrelated system-tray icons with
        # no chance to stop it. Confirm before acting on more than one
        # guessed click.
        click_ids = [a.get("element") for a in actions if (a.get("action") or "").lower() == "click"]
        if llm_guessed and len(click_ids) > 1 and len(click_ids) == len(actions):
            by_id = {el.id: el for el in elements}
            names = [by_id[i].name for i in click_ids if i in by_id]
            plan = list(actions)
            return ExecutorResult(
                success=True,
                speak="",
                confirm=f"I'm not sure exactly what you meant - should I click {', then '.join(names)}?",
                on_confirm=lambda: ExecutorResult(success=True, speak=execute_actions(plan, elements)),
            )

        confirmation = execute_actions(actions, elements)
        return ExecutorResult(success=True, speak=confirmation)

    except Exception:
        logger.exception("ui_executor failed")
        return ExecutorResult(success=False, speak="I had trouble doing that on screen.")


if __name__ == "__main__":
    import time

    import keyboard

    logging.basicConfig(level=logging.INFO)

    # Number parser unit checks (no live window needed).
    cases = {
        "5": 5, "click 5": 5, "number 7": 7, "click number 12": 12,
        "five": 5, "twenty three": 23, "forty": 40, "number five": 5,
        "click on 9": 9, "press on 3": 3, "click on number 4": 4,
        "hello": None, "": None,
    }
    ok = all(_parse_number(k) == v for k, v in cases.items())
    print("number parser:", "OK" if ok else "FAIL",
          {k: _parse_number(k) for k in cases})

    _state = AppState()
    # Live: open Run dialog and drive it purely by spoken-style commands.
    keyboard.send("win+r")
    time.sleep(1.0)
    print("type ->", execute("type calc into the open field", _state).speak)
    time.sleep(0.5)
    print("click ->", execute("click Cancel", _state).speak)
