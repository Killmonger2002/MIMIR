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
    """Parse a transcribed reply as yes (True), no (False), or unclear (None).

    Deliberately does NOT use normalize_command() here: that helper strips
    conversational lead-ins ("ok", "alright") as command filler, but in a
    yes/no reply those words ARE the answer - "okay then" must stay intact
    to parse as yes.
    """
    text = reply.lower().strip()
    text = re.sub(r"[,]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .!?")
    if not text:
        return None
    if _NO_RE.search(text):
        return False
    if _YES_RE.search(text):
        return True
    return None


def confirm_with_reply(question: str, state: AppState) -> tuple[bool | None, str]:
    """Speak a yes/no question and listen for the answer.

    Returns (verdict, raw_reply_text). verdict is True/False for a clear
    yes/no, or None if the reply was silence, a timeout, or a real phrase
    that isn't yes/no - callers that need to tell those last two apart
    (see main.py's low-confidence-transcript retry, which treats a
    non-yes/no-but-real reply as a restated command instead of discarding
    it) should inspect raw_reply_text themselves. If confirmation is
    disabled in config, returns (True, "") immediately without speaking.
    """
    if not config.confirmation.enabled:
        return True, ""

    tts.speak(question, state)

    state.set_mode("listening")
    # apply_speaker_filter=False: never let speaker verification drop a
    # yes/no reply - it was eating "yes" answers and turning them into
    # "no, cancelled" (see record_until_silence's docstring).
    audio = stt.record_until_silence(
        max_wait_sec=config.confirmation.reply_wait_sec, apply_speaker_filter=False
    )
    state.set_mode("thinking")
    reply = stt.transcribe(audio)
    if reply:
        state.add_caption("you", reply)

    verdict = parse_reply(reply)
    logger.info("Confirmation %r -> reply %r -> %s", question, reply, verdict)
    return verdict, reply


def confirm(question: str, state: AppState) -> bool:
    """Speak a yes/no question and listen for the answer.

    Returns True only on a clear yes. Silence, timeout, or an unclear
    reply all return False. If confirmation is disabled in config,
    returns True immediately without speaking.
    """
    verdict, _reply = confirm_with_reply(question, state)
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
