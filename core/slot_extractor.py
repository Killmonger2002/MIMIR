"""LLM-backed slot extraction: a fallback-only mechanism for pulling a
specific value (an app name, a folder name, a window target) out of a
command the regex-based extractor in an executor couldn't parse.

Never on the common/fast path - only called after an executor's own
regex/keyword logic has already failed to find a usable value, so it
never adds latency to the normal case.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("mimir.slot_extractor")


def extract_slot(text: str, slot_description: str, timeout: float | None = None) -> str | None:
    """Ask the local tier-1 model to pull `slot_description` out of `text`.

    Returns None on any failure (no usable tier, junk response) - callers
    must already have their own "didn't catch that" fallback message for
    this case. `timeout` is accepted for backward compatibility but tier
    timeouts now live in config (llm.timeout_sec for tier 1).
    """
    from core.llm_runtime import generate

    # "Identify ... the actual proper name of the thing they mean", not
    # "Extract ...": Qwen models follow "extract" literally and return
    # the user's own words ("spreadsheet program from microsoft") instead
    # of inferring the referent (Microsoft Excel). Verified live against
    # qwen2.5:7b - the wording change alone flipped it from literal
    # copying to correct inference on every test case.
    prompt = (
        f"Identify {slot_description} from this voice command. "
        f"The user may describe it indirectly - reply with the actual proper "
        f"name of the thing they mean, not their words. "
        f"Reply with ONLY the name, no explanation. "
        f'If nothing relevant is present, reply with exactly "NONE".\n\n'
        f"Command: {text}"
    )
    # Requests tier 2: extracting "Microsoft Excel" from "that
    # spreadsheet program from microsoft" needs world knowledge the small
    # routing model measurably lacks (benchmark_llm_tier1.py: tier-1
    # candidates extracted the literal word "spreadsheet"). Slot
    # extraction only fires on a failure path, so tier 2's extra seconds
    # are acceptable. NOTE: the actual tier is capped by the user's
    # session-selected active tier (default: tier 1) - escalation is
    # user-controlled, so until the user says "get smarter" this runs on
    # the basic model with correspondingly weaker extraction.
    response = generate(prompt, tier=2, num_predict=10)
    if response is None:
        logger.warning("Slot extraction unavailable (no usable tier)")
        return None

    value = response.strip().strip('"').strip(".")
    if not value or value.upper() == "NONE":
        return None
    return value


if __name__ == "__main__":
    print(extract_slot("open that thing i was using yesterday for photos", "the name of the application to open"))
    print(extract_slot("umm can you get me into that folder with all the invoices", "the name of the folder to open"))
