"""Executes a list of UI actions against scanned elements' live pywinauto
wrappers. Action methods here were each validated against real UIA
controls (see the module self-test and the build notes in ui_scanner):

    click   -> invoke() [InvokePattern, no mouse movement]
               falling back to click_input() [real mouse click]
    type    -> ValuePattern SetValue [clean, literal, instant]
               falling back to set_focus() + keyboard.write(delay=0.01)
               (keyboard.write with delay=0 produces key-repeat garbage;
                the small delay is required)
    select  -> ValuePattern/select for combo boxes
    focus   -> set_focus()
    check/uncheck -> TogglePattern toggle(), else a click

Returns a spoken confirmation naming the RESOLVED element, never the raw
command - so a fuzzy/LLM match that landed on the wrong-but-plausible
element is audible to the user immediately.
"""

from __future__ import annotations

import logging
import time

from core.ui_scanner import UIElement

logger = logging.getLogger("mimir.action_executor")

_KEY_DELAY = 0.01  # per-char delay for the keyboard.write fallback; 0 causes key-repeat artifacts


def _do_click(wrapper) -> None:
    try:
        wrapper.invoke()
        return
    except Exception:
        logger.debug("invoke() failed, falling back to click_input()", exc_info=True)
    wrapper.click_input()


def _do_type(wrapper, text: str) -> None:
    # Prefer UIA ValuePattern: literal, instant, no simulated keystrokes.
    try:
        wrapper.iface_value.SetValue(text)
        return
    except Exception:
        logger.debug("ValuePattern SetValue failed, falling back to keyboard typing", exc_info=True)
    import keyboard

    try:
        wrapper.set_focus()
    except Exception:
        logger.debug("set_focus before typing failed; typing into whatever has focus", exc_info=True)
    time.sleep(0.15)
    keyboard.send("ctrl+a")
    keyboard.send("delete")
    time.sleep(0.05)
    keyboard.write(text, delay=_KEY_DELAY)


def _do_select(wrapper, option: str) -> None:
    try:
        wrapper.select(option)
        return
    except Exception:
        logger.debug("select() failed, trying expand + list item", exc_info=True)
    # Expand the combo, then invoke the matching list item.
    try:
        wrapper.expand()
        time.sleep(0.2)
        for child in wrapper.descendants():
            try:
                if (child.element_info.name or "").strip().lower() == option.lower():
                    child.invoke()
                    return
            except Exception:
                continue
    except Exception:
        logger.debug("expand/select fallback failed", exc_info=True)


def _do_toggle(wrapper, want_checked: bool) -> None:
    try:
        state = wrapper.get_toggle_state()  # 0 off, 1 on
        if (state == 1) != want_checked:
            wrapper.toggle()
        return
    except Exception:
        logger.debug("TogglePattern failed, falling back to click", exc_info=True)
    _do_click(wrapper)


def _confirm_text(act: str, el: UIElement, text: str, option: str) -> str:
    if act == "click":
        return f"Clicked {el.name}"
    if act == "type":
        return f"Typed {text} into {el.name}" if el.name else f"Typed {text}"
    if act == "select":
        return f"Selected {option}"
    if act == "focus":
        return f"Focused {el.name}"
    if act == "check":
        return f"Checked {el.name}"
    if act == "uncheck":
        return f"Unchecked {el.name}"
    return "Done"


def execute_actions(actions: list[dict], elements: list[UIElement]) -> str:
    """Run each action against the matching element's live wrapper. Returns
    a combined spoken confirmation. Skips (and logs) any action referencing
    an unknown element id or that raises, so one bad step can't abort a
    multi-step plan or crash the caller."""
    by_id = {el.id: el for el in elements}
    confirmations: list[str] = []

    for action in actions:
        el_id = action.get("element")
        act = (action.get("action") or "").lower()
        text = action.get("text", "")
        option = action.get("option", "")

        el = by_id.get(el_id)
        if el is None or el._wrapper is None:
            logger.warning("Action %r references unknown/inactive element id %r", act, el_id)
            continue

        try:
            if act == "click":
                _do_click(el._wrapper)
            elif act == "type":
                _do_type(el._wrapper, text)
            elif act == "select":
                _do_select(el._wrapper, option)
            elif act == "focus":
                el._wrapper.set_focus()
            elif act == "check":
                _do_toggle(el._wrapper, True)
            elif act == "uncheck":
                _do_toggle(el._wrapper, False)
            else:
                logger.warning("Unknown action type %r", act)
                continue
            confirmations.append(_confirm_text(act, el, text, option))
            time.sleep(0.15)  # let the UI settle between chained steps
        except Exception:
            logger.exception("Action %r on element %r failed", act, el_id)

    return ". ".join(confirmations) if confirmations else "I couldn't do that on screen."


if __name__ == "__main__":
    import subprocess

    logging.basicConfig(level=logging.INFO)
    # Live end-to-end: open Run dialog, type into it, then click Cancel.
    import keyboard

    keyboard.send("win+r")
    time.sleep(1.0)
    from core.ui_scanner import scan

    els = scan()
    edit = next((e for e in els if e.type == "Edit"), None)
    cancel = next((e for e in els if e.type == "Button" and e.name == "Cancel"), None)
    plan = []
    if edit:
        plan.append({"action": "type", "element": edit.id, "text": "hello from the action executor"})
    if cancel:
        plan.append({"action": "click", "element": cancel.id})
    print("Plan:", plan)
    print("Result:", execute_actions(plan, els))
