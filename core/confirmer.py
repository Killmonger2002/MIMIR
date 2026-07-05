"""Spoken yes/no confirmation round-trips for uncertain or destructive commands.

Used by the main command loop when an executor returns a result with a
`confirm` question, or when the transcript itself scored low STT
confidence. Speaks the question, listens briefly for a reply, and parses
it as yes/no. Anything unclear (silence, timeout, unrelated speech)
counts as "no" - never act on an unconfirmed uncertain command.
"""

from __future__ import annotations

import logging
import re

from config import config
from core import stt, tts
from core.text_utils import normalize_command
from state import AppState

logger = logging.getLogger("mimir.confirmer")

_YES_RE = re.compile(
    r"\b(yes|yeah|yep|yup|sure|correct|right|confirm|affirmative|"
    r"go ahead|do it|proceed|okay|ok)\b"
)

# Checked before _YES_RE: "no, that's not right" must parse as no even
# though it contains "right".
_NO_RE = re.compile(
    r"\b(no|nope|nah|not|don'?t|do not|cancel|stop|wrong|incorrect|"
    r"negative|never ?mind|nevermind)\b"
)


def parse_reply(reply: str) -> bool | None:
    """Parse a transcribed reply as yes (True), no (False), or unclear (None)."""
    text = normalize_command(reply)
    if not text:
        return None
    if _NO_RE.search(text):
        return False
    if _YES_RE.search(text):
        return True
    return None


def confirm(question: str, state: AppState) -> bool:
    """Speak a yes/no question and listen for the answer.

    Returns True only on a clear yes. Silence, timeout, or an unclear
    reply all return False. If confirmation is disabled in config,
    returns True immediately without speaking.
    """
    if not config.confirmation.enabled:
        return True

    tts.speak(question, state)

    state.set_mode("listening")
    audio = stt.record_until_silence(max_wait_sec=config.confirmation.reply_wait_sec)
    state.set_mode("thinking")
    reply = stt.transcribe(audio)

    verdict = parse_reply(reply)
    logger.info("Confirmation %r -> reply %r -> %s", question, reply, verdict)
    return verdict is True


if __name__ == "__main__":
    _cases = [
        ("yes", True),
        ("yeah go ahead", True),
        ("yes please", True),
        ("that's right", True),
        ("okay", True),
        ("no", False),
        ("nope", False),
        ("no that's wrong", False),
        ("no, that's not right", False),
        ("cancel that", False),
        ("never mind", False),
        ("", None),
        ("banana", None),
    ]
    for reply_text, expected in _cases:
        got = parse_reply(reply_text)
        status = "OK " if got == expected else "FAIL"
        print(f"{status} {reply_text!r:28} -> {got} (expected {expected})")
