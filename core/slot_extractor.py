"""LLM-backed slot extraction: a fallback-only mechanism for pulling a
specific value (an app name, a folder name, a window target) out of a
command the regex-based extractor in an executor couldn't parse.

Never on the common/fast path - only called after an executor's own
regex/keyword logic has already failed to find a usable value, so it
never adds latency to the normal case.
"""

from __future__ import annotations

import logging

from config import config

logger = logging.getLogger("mimir.slot_extractor")


def extract_slot(text: str, slot_description: str, timeout: float | None = None) -> str | None:
    """Ask the local Ollama model to pull `slot_description` out of `text`.

    Returns None on any failure (timeout, unreachable Ollama, junk
    response) - callers must already have their own "didn't catch that"
    fallback message for this case.
    """
    try:
        import ollama

        resolved_timeout = timeout if timeout is not None else config.llm.timeout_sec

        prompt = (
            f"Extract {slot_description} from this voice command. "
            f'Reply with ONLY the extracted value, no explanation. '
            f'If nothing relevant is present, reply with exactly "NONE".\n\n'
            f"Command: {text}"
        )
        client = ollama.Client(timeout=resolved_timeout)
        response = client.generate(
            model=config.llm.model,
            prompt=prompt,
            options={"num_predict": 10, "temperature": 0},
        )
        value = response.get("response", "").strip().strip('"').strip(".")
        if not value or value.upper() == "NONE":
            return None
        return value
    except Exception as exc:
        logger.warning("Slot extraction unavailable: %s", exc)
        return None


if __name__ == "__main__":
    print(extract_slot("open that thing i was using yesterday for photos", "the name of the application to open"))
    print(extract_slot("umm can you get me into that folder with all the invoices", "the name of the folder to open"))
