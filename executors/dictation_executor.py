"""Continuous dictation mode: "start dictation" -> everything you say is
typed into the focused window until you say "stop dictation" (or press the
pause hotkey). MIMIR's answer to Windows Voice Access dictation.

Design (v1, agreed 2026-07-17):
 - Whisper already punctuates and capitalizes ("What can you do?",
   "Open Microsoft Excel." came through our own logs correctly), so this
   is NOT Dragon-style "say comma, say period" dictation. You speak
   naturally; Whisper punctuates; we only add spoken commands for what
   speech can't express: line/paragraph breaks, edits, and stop.
 - This is the one executor that runs a SUSTAINED loop instead of
   returning after one action. It's called synchronously from main.py's
   command cycle, which holds the command-cycle lock for its whole
   duration - so the wake word and other commands can't interrupt a
   dictation session, exactly as wanted. The loop ends on a recognized
   stop phrase or state.dictation_stop_requested() (set by the pause
   hotkey), then returns normally.

Deferred to v2 (deliberately out of scope here): explicit punctuation
overrides / a "literal" mode, LLM spell/tone/grammar cleanup of the
dictated text (Tier 2), number/date formatting.
"""

from __future__ import annotations

import logging
import re

from core.text_utils import strip_filler_prefixes
from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.dictation_executor")

# How long to wait for speech before a record call gives up and returns,
# so the loop can re-check the stop flag during a long silence. Does NOT
# cut off active speech - record_until_silence only applies max_wait
# before the user starts talking (see its docstring).
_IDLE_RECHECK_SEC = 15.0

# --- start trigger (routes here) ------------------------------------------
_START_RE = re.compile(
    r"^(start|begin|enter)\s+(dictation|dictating)$"
    r"|^dictation\s+mode$"
    r"|^take\s+(a\s+)?(dictation|note|memo)$"
    r"|^start\s+typing\s+(for\s+me|what\s+i\s+say)$",
    re.IGNORECASE,
)

# --- in-session control phrases (matched against a whole short utterance) -
# Anchored on the FULL cleaned utterance: you pause, say just the command,
# pause. A sentence that merely contains "new line" as content won't match.
_STOP_RE = re.compile(
    r"^(stop|end|finish|exit|quit|cancel)\s+(dictation|dictating|typing|listening|(the\s+)?note)$"
    r"|^(i'?m\s+)?done\s+(dictating|with\s+(the\s+)?(dictation|note))$",
    re.IGNORECASE,
)
_NEWLINE_RE = re.compile(r"^new\s*line$", re.IGNORECASE)
_NEWPARA_RE = re.compile(r"^new\s*paragraph$", re.IGNORECASE)
_SCRATCH_RE = re.compile(r"^(scratch|delete|erase|remove)\s+(that|the\s+last(\s+line)?|last)$", re.IGNORECASE)
_UNDO_RE = re.compile(r"^undo(\s+that)?$", re.IGNORECASE)

# Patterns the intent router uses to send commands here (single source of
# truth - referenced from core/intent_router.py via _START_RE.pattern).
PATTERNS = [_START_RE.pattern]

# Curly quotes / dashes / ellipsis Whisper can emit that keystroke
# injection may not type on a standard layout - normalized to ASCII.
_UNICODE_MAP = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "…": "...", " ": " ",
}


def _norm(text: str) -> str:
    """Lowercase, strip filler prefixes and trailing sentence punctuation -
    for matching control phrases regardless of how Whisper cased/punctuated
    them ("Stop dictation." -> "stop dictation")."""
    return strip_filler_prefixes(text.strip()).strip().rstrip(".!?,;:").strip().lower()


def _normalize_for_typing(text: str) -> str:
    for uni, ascii_ in _UNICODE_MAP.items():
        text = text.replace(uni, ascii_)
    return text


def classify_utterance(text: str) -> tuple[str, str]:
    """Classify one dictated utterance. Returns (kind, payload) where kind
    is one of: stop, newline, newparagraph, scratch, undo, text. For
    'text', payload is the original (un-normalized) text to type; for the
    rest, payload is ''. Pure function - unit-tested without a mic."""
    norm = _norm(text)
    if not norm:
        return "text", ""
    if _STOP_RE.match(norm):
        return "stop", ""
    if _NEWPARA_RE.match(norm):
        return "newparagraph", ""
    if _NEWLINE_RE.match(norm):
        return "newline", ""
    if _SCRATCH_RE.match(norm):
        return "scratch", ""
    if _UNDO_RE.match(norm):
        return "undo", ""
    return "text", text.strip()


def smart_join(prev_char: str | None, chunk: str) -> tuple[str, str]:
    """Join a new text chunk to what's already typed. Returns
    (text_to_type, new_last_char). Inserts a single space between chunks
    unless we're at the very start or right after whitespace/newline;
    preserves Whisper's own capitalization and punctuation (v1 keeps this
    simple - a mid-sentence pause may leave an extra capital/period at the
    boundary, which the v2 LLM-cleanup pass is meant to smooth). Pure
    function - unit-tested."""
    chunk = _normalize_for_typing(chunk).strip()
    if not chunk:
        return "", prev_char or ""
    if prev_char is None or prev_char in " \n\t":
        to_type = chunk
    else:
        to_type = " " + chunk
    return to_type, to_type[-1]


# --- keystroke helpers (thin wrappers so the pure logic above stays
#     testable without importing pyautogui) --------------------------------


def _type_text(text: str) -> None:
    import pyautogui

    pyautogui.write(text, interval=0.01)


def _type_newline(count: int) -> None:
    import pyautogui

    pyautogui.press("enter", presses=count)


def _backspace(count: int) -> None:
    import pyautogui

    if count > 0:
        pyautogui.press("backspace", presses=count)


def _undo() -> None:
    import pyautogui

    pyautogui.hotkey("ctrl", "z")


def _run_dictation(state: AppState) -> ExecutorResult:
    from core import stt, tts

    tts.speak(
        "Dictation on. Say stop dictation, or press the pause hotkey, when you're done.",
        state,
        allow_interrupt=False,
    )

    state.set_dictating(True)
    last_char: str | None = None
    last_typed = ""  # the exact string typed for the last text chunk (for "scratch that")

    try:
        while not state.dictation_stop_requested():
            state.set_mode("dictating")
            audio = stt.record_until_silence(max_wait_sec=_IDLE_RECHECK_SEC, apply_speaker_filter=False)
            if state.dictation_stop_requested():
                break
            if not stt.contains_speech(audio):
                continue  # silence timed out - loop to re-check the stop flag
            text = stt.transcribe(audio).strip()
            if not text:
                continue

            kind, payload = classify_utterance(text)
            if kind == "stop":
                break
            if kind == "newparagraph":
                _type_newline(2)
                last_char, last_typed = "\n", ""
            elif kind == "newline":
                _type_newline(1)
                last_char, last_typed = "\n", ""
            elif kind == "scratch":
                _backspace(len(last_typed))
                last_char, last_typed = " ", ""  # boundary now unknown; assume a space is safe
            elif kind == "undo":
                _undo()
                last_char, last_typed = " ", ""
            else:  # text
                to_type, last_char = smart_join(last_char, payload)
                if to_type:
                    _type_text(to_type)
                    last_typed = to_type
                    # Mirror what was typed into the live caption feed so the
                    # transcript bar shows the dictation as it happens.
                    state.add_caption("you", payload)
    except Exception:
        logger.exception("dictation loop failed")
        state.set_dictating(False)
        state.set_mode("idle")
        return ExecutorResult(success=False, speak="Dictation stopped after an error.")

    state.set_dictating(False)
    state.set_mode("idle")
    return ExecutorResult(success=True, speak="Dictation off.")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Enter continuous dictation mode."""
    norm = _norm(command_text)
    if not _START_RE.match(norm):
        # Router sent something here that isn't a start phrase - shouldn't
        # happen, but fail clearly rather than silently entering the loop.
        return ExecutorResult(
            success=False, speak="Say 'start dictation' to begin dictating."
        )
    return _run_dictation(state)


if __name__ == "__main__":
    # Pure-logic self-test (no mic / no typing).
    logging.basicConfig(level=logging.INFO)

    classify_cases = {
        "stop dictation": "stop",
        "Stop dictation.": "stop",
        "end dictation": "stop",
        "I'm done dictating": "stop",
        "new line": "newline",
        "New line.": "newline",
        "new paragraph": "newparagraph",
        "scratch that": "scratch",
        "delete that": "scratch",
        "undo that": "undo",
        "undo": "undo",
        "Hello there, how are you?": "text",
        "the Jurassic period was long ago": "text",  # 'period' inside content, not a command
        "stop the car": "text",  # not a stop phrase (needs 'stop dictation')
    }
    ok = True
    for text, expected in classify_cases.items():
        kind, _payload = classify_utterance(text)
        status = "OK " if kind == expected else "FAIL"
        if kind != expected:
            ok = False
        print(f"{status} classify {text!r:38} -> {kind} (expected {expected})")

    join_cases = [
        (None, "Hello there.", "Hello there."),
        (".", "How are you?", " How are you?"),
        ("\n", "New sentence.", "New sentence."),
        ("e", "and more", " and more"),
    ]
    for prev, chunk, expected in join_cases:
        out, _last = smart_join(prev, chunk)
        status = "OK " if out == expected else "FAIL"
        if out != expected:
            ok = False
        print(f"{status} join prev={prev!r} chunk={chunk!r:20} -> {out!r} (expected {expected!r})")

    print("\nALL PASS" if ok else "\nSOME FAILED")
