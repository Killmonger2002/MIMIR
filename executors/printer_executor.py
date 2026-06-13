"""Multi-turn printing workflow.

Voice command patterns handled:
    - "print this"
    - "i want to print this pdf"
    - "show printers"

Phase 1 limitation:
    # TODO(phase2): The file to print is not yet derived from screen/app
    # context. For now we use config.printer.last_opened_file_placeholder
    # as the target file path. Full file-context awareness is Phase 2.

This executor implements a small conversational state machine. State is
kept in a module-level dict keyed by a single "pending job" since MIMIR
handles one conversation at a time.
"""

from __future__ import annotations

import logging
import re

from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.printer_executor")

# Module-level pending job state for the multi-turn flow.
# Shape: {"step": "choose_printer" | "choose_color" | "choose_copies",
#         "printers": [...], "printer": str | None,
#         "color": str | None, "copies": int | None}
_pending_job: dict | None = None

_PRINT_TRIGGER_RE = re.compile(r"\bprint\b", re.IGNORECASE)
_COPIES_RE = re.compile(r"\b(\d+)\b")


def _list_printers() -> list[str]:
    """Return names of installed printers via win32print."""
    import win32print

    printers = win32print.EnumPrinters(
        win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    )
    return [p[2] for p in printers]


def _start_print_job() -> ExecutorResult:
    """Step 1: figure out which printer(s) are available and ask the user."""
    global _pending_job
    try:
        printers = _list_printers()
    except Exception:
        logger.exception("Failed to enumerate printers")
        return ExecutorResult(success=False, speak="I couldn't access the printer list.")

    if not printers:
        return ExecutorResult(
            success=False,
            speak="I can't find any printers connected to this PC. Would you like help connecting one?",
            needs_followup=True,
        )

    if len(printers) == 1:
        _pending_job = {
            "step": "choose_color",
            "printers": printers,
            "printer": printers[0],
            "color": None,
            "copies": None,
        }
        return ExecutorResult(
            success=True,
            speak=f"I found {printers[0]}. Colour or black and white?",
            needs_followup=True,
        )

    _pending_job = {
        "step": "choose_printer",
        "printers": printers,
        "printer": None,
        "color": None,
        "copies": None,
    }
    names = ", ".join(printers)
    return ExecutorResult(
        success=True,
        speak=f"I found {len(printers)} printers: {names}. Which one would you like to use?",
        needs_followup=True,
    )


def _do_print(job: dict) -> ExecutorResult:
    """Final step: send the file to the chosen printer."""
    from config import config
    import win32api
    import win32print

    filepath = config.printer.last_opened_file_placeholder
    if not filepath:
        return ExecutorResult(
            success=False,
            speak="I don't have a file to print yet. That's coming in a future update.",
        )

    try:
        previous_default = win32print.GetDefaultPrinter()
        win32print.SetDefaultPrinter(job["printer"])
        try:
            win32api.ShellExecute(0, "print", filepath, None, None, 0)
        finally:
            win32print.SetDefaultPrinter(previous_default)
        return ExecutorResult(success=True, speak=f"Sending to {job['printer']}. Printing now.")
    except Exception:
        logger.exception("Failed to send print job")
        return ExecutorResult(success=False, speak="I couldn't send that to the printer.")


def _handle_followup(command_text: str, job: dict) -> ExecutorResult:
    """Step 2: progress the conversational state machine to completion."""
    global _pending_job
    text = command_text.lower().strip()

    if job["step"] == "choose_printer":
        for printer in job["printers"]:
            if printer.lower() in text or text in printer.lower():
                job["printer"] = printer
                job["step"] = "choose_color"
                return ExecutorResult(
                    success=True,
                    speak=f"Got it, {printer}. Colour or black and white?",
                    needs_followup=True,
                )
        return ExecutorResult(
            success=False,
            speak="I didn't catch which printer. Which one would you like to use?",
            needs_followup=True,
        )

    if job["step"] == "choose_color":
        if "colour" in text or "color" in text:
            job["color"] = "color"
        elif "black" in text or "white" in text or "b&w" in text or "bw" in text:
            job["color"] = "black and white"
        else:
            return ExecutorResult(
                success=False,
                speak="Colour or black and white?",
                needs_followup=True,
            )
        job["step"] = "choose_copies"
        return ExecutorResult(success=True, speak="How many copies?", needs_followup=True)

    if job["step"] == "choose_copies":
        match = _COPIES_RE.search(text)
        copies = int(match.group(1)) if match else 1
        job["copies"] = copies
        result = _do_print(job)
        _pending_job = None
        return result

    _pending_job = None
    return ExecutorResult(success=False, speak="Something went wrong with the print job.")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Drive a multi-turn print job: pick printer, color mode, copies, then print."""
    global _pending_job
    try:
        if _pending_job is not None:
            return _handle_followup(command_text, _pending_job)

        text = command_text.lower().strip()

        if "show printers" in text or "list printers" in text:
            try:
                printers = _list_printers()
            except Exception:
                logger.exception("Failed to enumerate printers")
                return ExecutorResult(success=False, speak="I couldn't access the printer list.")
            if not printers:
                return ExecutorResult(success=False, speak="I couldn't find any printers.")
            return ExecutorResult(success=True, speak=f"Your printers are: {', '.join(printers)}")

        if _PRINT_TRIGGER_RE.search(text):
            return _start_print_job()

        return ExecutorResult(success=False, speak="I'm not sure what printing action you want.")
    except Exception:
        logger.exception("printer_executor failed")
        _pending_job = None
        return ExecutorResult(success=False, speak="Something went wrong with that print command.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["show printers", "print this"]:
        print(cmd, "->", execute(cmd, _state))
