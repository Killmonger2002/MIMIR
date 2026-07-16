"""Maps a spoken target ("the search box", "sign in") to a scanned
UIElement, in three escalating tiers - stopping at the first that
succeeds:

    Tier A  exact name match         (~0ms)   handles most real commands
    Tier B  fuzzy name/value match   (~5ms)   typos, partials, synonyms
    Tier C  LLM semantic match       (~1-30s) only when A and B both fail;
            understands intent ("the thing to log in with" -> "Sign in")
            and returns a full action plan as JSON.

Tier C routes through core.llm_runtime, NOT a hardcoded model - so it
honors the user-controlled tier ("get smarter") and the RAM/availability
gating. Semantic UI planning is a reasoning task, so it REQUESTS tier 2;
on a machine (or session) capped at tier 1 it still tries, just less
reliably, consistent with MIMIR's "escalation is the user's choice" rule.
"""

from __future__ import annotations

import json
import logging
import re

from thefuzz import process as fuzzy

from core.ui_scanner import UIElement, snapshot_text

logger = logging.getLogger("mimir.element_matcher")

_FUZZY_THRESHOLD = 70

_SYSTEM_PROMPT = """You control a Windows PC by voice. Given a numbered list of on-screen \
UI elements and a user command, output a JSON array of actions to perform.

Action types:
  {"action":"click","element":N}
  {"action":"type","element":N,"text":"..."}
  {"action":"select","element":N,"option":"..."}
  {"action":"focus","element":N}
  {"action":"check","element":N}
  {"action":"uncheck","element":N}

Rules:
- Output ONLY a valid JSON array. No text before or after, no markdown.
- Map the user's INTENT to the best element even if the words differ
  (e.g. "log in" -> click a "Sign in" button; "search for X" -> type X
  in the search field then click the search button).
- Use only element numbers that appear in the list.
- If truly nothing matches, output: []

Example:
Elements:
[1] Edit "Search"
[2] Button "Search"
[3] Button "Sign in"
[4] ComboBox "Sort by" value="Relevance"
Command: "log in"
[{"action":"click","element":3}]"""


def match_exact(target: str, elements: list[UIElement]) -> UIElement | None:
    t = target.lower().strip()
    for el in elements:
        if el.name.lower() == t:
            return el
    return None


def match_all_exact(target: str, elements: list[UIElement]) -> list[UIElement]:
    """All elements whose name exactly equals the target - used to detect
    ambiguity ('two Search buttons') so the caller can ask which one."""
    t = target.lower().strip()
    return [el for el in elements if el.name.lower() == t]


def match_fuzzy(target: str, elements: list[UIElement], threshold: int = _FUZZY_THRESHOLD) -> UIElement | None:
    named = {el.name: el for el in elements if el.name}
    if not named:
        return None
    best = fuzzy.extractOne(target, list(named.keys()))
    if best is None:
        return None
    name, score = best[0], best[1]
    if score >= threshold:
        logger.debug("Fuzzy matched %r -> %r (score=%d)", target, name, score)
        return named[name]
    return None


def _parse_action_json(raw: str) -> list[dict]:
    """Extract a JSON action array from a model response, tolerating
    markdown fences and leading/trailing prose."""
    text = re.sub(r"```(?:json)?|```", "", raw).strip()
    # Grab the outermost [...] if the model wrapped it in explanation.
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    parsed = json.loads(text)
    if not isinstance(parsed, list):
        raise ValueError("expected a JSON array")
    # Keep only well-formed action dicts.
    return [a for a in parsed if isinstance(a, dict) and "action" in a and "element" in a]


def match_llm(command: str, elements: list[UIElement]) -> list[dict]:
    """Ask the LLM tier for an action plan. Returns [] if no tier is
    available or the model can't produce valid JSON after one retry."""
    from core.llm_runtime import generate

    user_message = f"Elements:\n{snapshot_text(elements)}\n\nCommand: \"{command}\""
    prompt = f"{_SYSTEM_PROMPT}\n\n{user_message}"

    response = generate(prompt, tier=2, num_predict=200, temperature=0)
    if response is None:
        logger.info("LLM UI matching unavailable (no usable tier)")
        return []

    try:
        return _parse_action_json(response)
    except Exception:
        logger.debug("First LLM JSON parse failed; retrying with a stricter nudge")

    strict = prompt + '\n\nYour last reply was not valid JSON. Reply with ONLY a JSON array, e.g. [{"action":"click","element":2}].'
    response = generate(strict, tier=2, num_predict=200, temperature=0)
    if response is None:
        return []
    try:
        return _parse_action_json(response)
    except Exception:
        logger.warning("LLM produced unparseable UI action JSON twice; giving up")
        return []


if __name__ == "__main__":
    # Pure-logic tiers A/B against a synthetic element list (no live window).
    from core.ui_scanner import UIElement as E

    els = [
        E(1, "Edit", "Search", ""),
        E(2, "Button", "Search", ""),
        E(3, "Hyperlink", "Home", ""),
        E(4, "ComboBox", "Sort by", "Relevance"),
        E(5, "Button", "Sign in", ""),
    ]
    print("exact 'search':", match_exact("search", els))
    print("all exact 'search':", [e.id for e in match_all_exact("search", els)])
    print("fuzzy 'sign':", match_fuzzy("sign", els))
    print("fuzzy 'srt by':", match_fuzzy("srt by", els))
    print("fuzzy 'zzzz':", match_fuzzy("zzzz", els))
    print("parse test:", _parse_action_json('```json\n[{"action":"click","element":2}]\n```'))
