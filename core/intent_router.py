"""Routes transcribed text to the appropriate executor module name.

Two-tier classification:
    1. Regex tier - confidence-scored pattern matching (see
       _regex_classify_scored). Each matching executor gets a score
       combining anchor strength, span coverage, and pattern specificity;
       the highest-scoring executor wins, rather than whichever pattern
       happens to be declared first in _PATTERNS.
    2. LLM fallback tier - asks the local Ollama phi3:mini model to
       classify the command when the regex tier's best score is too low
       to trust (below _LLM_FALLBACK_FLOOR).
"""

from __future__ import annotations

import logging
import re

from core.text_utils import normalize_command, strip_filler_prefixes
from executors import system_executor as _system_executor

logger = logging.getLogger("mimir.intent_router")

# Re-exported for backwards compatibility - executors historically imported
# this name directly from intent_router.
_strip_filler_prefixes = strip_filler_prefixes

# Ordered list of (executor_name, [regex patterns]).
# First matching pattern wins, in declaration order.
_PATTERNS: list[tuple[str, list[str]]] = [
    (
        "app_executor",
        [
            r"^(open|launch|start|run)\s+"
            r"(?!.*\b(downloads|desktop|documents|pictures|music|videos|folder|this pc|my computer)\b)"
            r"(?!.*\b(disk|drive)\s+[a-z]\b).+",
        ],
    ),
    (
        "file_executor",
        [
            r"^(open|go to|navigate to)\s+.*\b(downloads|desktop|documents|pictures|music|videos|folder)\b",
            r"^(find|search for|locate)\s+(my\s+)?.+",
            r"\bthis pc\b",
            r"\bmy computer\b",
            r"\b(disk|drive)\s+[a-z]\b",
            r"\b(go back|go up|previous (directory|folder)|parent (directory|folder))\b",
            r"\b(list|show|tell)\b.*\bfiles?\b",
            r"\bwhat files\b",
        ],
    ),
    (
        "volume_executor",
        [
            r"\bvolume\b",
            r"^(mute|unmute)$",
            r"\b(turn it up|turn it down|louder|quieter)\b",
            r"\b(volume up|volume down)\b",
        ],
    ),
    (
        "brightness_executor",
        [
            r"\bbrightness\b",
            r"\b(brighter|dimmer|dim the screen)\b",
        ],
    ),
    (
        "wifi_executor",
        [
            r"\bwi[\s-]?fi\b",
            r"connect to\s+.+",
            r"disconnect.*wifi",
        ],
    ),
    (
        "bluetooth_executor",
        [
            r"\bbluetooth\b",
            r"\b(connect|pair)\s+(my\s+)?(headphones|speaker|earbuds|earphones|headset)\b",
        ],
    ),
    (
        "printer_executor",
        [
            r"\bprint\b",
            r"\bprinters?\b",
        ],
    ),
    (
        "model_executor",
        [
            # Fully anchored so it decisively outscores window_executor's
            # "^switch to .+" catch-all on phrases like "switch to the
            # smartest model" (verified in the self-test below).
            r"^switch to (a |the )?(smartest|smarter|bigger|biggest|better|best|maximum|basic|smaller|simpler|default|normal|fastest) (model|brain)$",
            r"\b(smartest|smarter|bigger|biggest|better|best|maximum|basic|smaller|simpler|default|normal|fastest) (model|brain)\b",
            r"\bget (smarter|smartest|simpler|dumber)\b",
            r"\bthink harder\b",
            r"\b(which|what) (model|brain)\b",
            r"\bhow smart are you\b",
            r"\bsave (some )?(memory|ram|resources)\b",
        ],
    ),
    (
        "window_executor",
        [
            r"\b(minimi[sz]e|maximi[sz]e)\b",
            r"^switch to\s+.+",
            r"\bclose (this|the) window\b",
            r"\block (the )?screen\b",
            r"^(go to )?sleep$",
            r"^(close|quit|exit)\s+(?!.*\b(yourself|mimir)\b).+",
        ],
    ),
    (
        "media_executor",
        [
            r"^(play|pause|resume|stop)$",
            r"\b(next|previous|skip)\b.*\b(song|track)?\b",
            r"^(next|previous|skip|back)$",
        ],
    ),
    (
        "sysinfo_executor",
        [
            r"\b(cpu|processor)\b.*\busage\b",
            r"\bhow much (battery|ram|memory|disk|storage|space)\b",
            r"\b(battery|cpu usage|ram usage|disk space)\b",
        ],
    ),
    (
        "typing_executor",
        [
            r"^(type|write|dictate)\b",
            r"^(press\s+)?(backspace|delete|enter|return|tab|escape|new line)\b",
        ],
    ),
    (
        "system_executor",
        [
            r"\bwhat can you do\b",
            r"\b(list|show|tell)\b.*\b(commands?|capabilities)\b",
            r"\bwhat commands\b",
            r"^help$",
            # "Can you hear me?" reaches the router as "hear me" ("can you"
            # is stripped as a filler prefix by normalize_command).
            _system_executor._HEAR_ME_RE.pattern,
            _system_executor._QUIT_RE.pattern,
        ],
    ),
]

_LLM_CATEGORIES = [name for name, _ in _PATTERNS] + ["browser_executor", "unknown"]

# Below this score, the regex tier's best guess isn't trusted enough to
# act on - fall back to the LLM tier instead. Tuned down from an initial
# 0.3 after regression testing: 0.3 caused legitimate short keyword
# matches buried in a longer sentence ("turn off wifi" scored 0.29, "i
# want to print this pdf" scored 0.26) to be wrongly penalized by their
# low span-coverage and incorrectly fall back to "unknown".
_LLM_FALLBACK_FLOOR = 0.25

# When the runner-up executor's score is within this margin of the
# winner's, the match is ambiguous - logged for visibility (and available
# to a future "did you mean X or Y" recovery flow), without changing
# which executor is actually dispatched.
_AMBIGUITY_MARGIN = 0.1

_ANCHOR_WEIGHT = 0.4
_COVERAGE_WEIGHT = 0.3
_SPECIFICITY_WEIGHT = 0.3

# Matches regex syntax characters/escapes, so they can be stripped out
# when counting a pattern's "literal" (non-syntax) character count for
# the specificity score below.
_REGEX_SYNTAX_RE = re.compile(r"\\[bsSdDwW]|[\^$.*+?{}()\[\]|]")


def _strip_lookarounds(pattern: str) -> str:
    """Remove (?!...)/(?=...)/(?<!...)/(?<=...) groups from a pattern,
    including nested parens - via balanced-paren scanning, since a naive
    regex substitution would mis-handle nested groups inside a lookaround
    (and app_executor's own pattern has exactly such a nested group)."""
    result = []
    i = 0
    n = len(pattern)
    while i < n:
        if pattern[i] == "\\" and i + 1 < n:
            result.append(pattern[i : i + 2])
            i += 2
            continue
        if pattern[i : i + 3] in ("(?!", "(?="):
            depth = 1
            j = i + 3
            while j < n and depth > 0:
                if pattern[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if pattern[j] == "(":
                    depth += 1
                elif pattern[j] == ")":
                    depth -= 1
                j += 1
            i = j
            continue
        if pattern[i : i + 4] in ("(?<!", "(?<="):
            depth = 1
            j = i + 4
            while j < n and depth > 0:
                if pattern[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if pattern[j] == "(":
                    depth += 1
                elif pattern[j] == ")":
                    depth -= 1
                j += 1
            i = j
            continue
        result.append(pattern[i])
        i += 1
    return "".join(result)


def _anchor_score(pattern: str) -> float:
    """Fully anchored (^...$) patterns are the most specific commitment a
    pattern can make; prefix-anchored (^...) is a weaker but still
    meaningful commitment; unanchored (\\b...\\b) patterns can match
    anywhere in the text and so score lowest."""
    starts_anchored = pattern.startswith("^")
    ends_anchored = pattern.endswith("$")
    if starts_anchored and ends_anchored:
        return 1.0
    if starts_anchored:
        return 0.7
    return 0.4


def _specificity_score(pattern: str) -> float:
    """Penalizes patterns built around a wildcard (`.+`/`.*`, like
    app_executor's open-ended trigger) relative to patterns built from
    literal word alternations, since coverage alone rewards a wildcard
    that matches the whole remaining string by construction.

    Honesty note: for app_executor's specific "open this pc" collision,
    this factor alone does NOT flip (or even meaningfully narrow) the
    ordering versus file_executor's short `\\bthis pc\\b` pattern - the
    catch-all's alternation ("open|launch|start|run") has enough literal
    characters of its own that the wildcard halving isn't sufficient, and
    coverage/anchor still dominate. That collision is actually prevented
    by app_executor's negative lookahead excluding "this pc" outright
    (see _PATTERNS), which the plan deliberately keeps rather than relying
    on scoring alone. This factor still matters for the general class of
    problem - a wildcard match with partial (not full-string) coverage
    against a comparably-sized literal match - just not this specific
    100%-coverage case."""
    core = _strip_lookarounds(pattern)
    literal_chars = len(_REGEX_SYNTAX_RE.sub("", core))
    has_wildcard = ".+" in core or ".*" in core
    score = min(1.0, literal_chars / 40.0)
    return score * (0.5 if has_wildcard else 1.0)


def _pattern_score(pattern: str, match: re.Match, text: str) -> float:
    coverage = (match.end() - match.start()) / max(1, len(text))
    return (
        _ANCHOR_WEIGHT * _anchor_score(pattern)
        + _COVERAGE_WEIGHT * coverage
        + _SPECIFICITY_WEIGHT * _specificity_score(pattern)
    )


def _regex_classify_scored(text: str) -> tuple[str | None, float, str | None, float]:
    """Score every executor whose patterns match `text` and return
    (best_name, best_score, runner_up_name, runner_up_score). An
    executor's score is the MAX over its own patterns (not a sum), so an
    executor with many patterns doesn't win purely by having more of them."""
    scores: dict[str, float] = {}
    for executor_name, patterns in _PATTERNS:
        best_for_executor = 0.0
        matched_any = False
        for pattern in patterns:
            match = re.search(pattern, text)
            if match is None:
                continue
            matched_any = True
            best_for_executor = max(best_for_executor, _pattern_score(pattern, match, text))
        if matched_any:
            scores[executor_name] = best_for_executor

    if not scores:
        return None, 0.0, None, 0.0

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_name, best_score = ranked[0]
    if len(ranked) > 1:
        runner_up_name, runner_up_score = ranked[1]
    else:
        runner_up_name, runner_up_score = None, 0.0

    if len(ranked) > 1:
        logger.info(
            "Multiple executors matched %r: %s (using %s, score=%.2f)",
            text,
            [f"{name}={score:.2f}" for name, score in ranked],
            best_name,
            best_score,
        )
    if runner_up_name is not None and (best_score - runner_up_score) <= _AMBIGUITY_MARGIN:
        logger.info(
            "Ambiguous classification for %r: %s=%.2f vs runner-up %s=%.2f",
            text,
            best_name,
            best_score,
            runner_up_name,
            runner_up_score,
        )

    return best_name, best_score, runner_up_name, runner_up_score


def _llm_classify(text: str) -> str:
    """Ask the local tier-1 model to classify the command."""
    from core.llm_runtime import generate

    categories_str = ", ".join(_LLM_CATEGORIES)
    prompt = (
        f"Classify this voice command into exactly one category: "
        f"[{categories_str}]. Reply with ONLY the category name.\n\n"
        f"Command: {text}"
    )
    response = generate(prompt, tier=1)
    if response is None:
        logger.warning("LLM classification unavailable (no usable tier)")
        return "unknown"

    category = response.strip().lower()
    for valid in _LLM_CATEGORIES:
        if valid in category:
            return valid
    return "unknown"


def classify(text: str) -> str:
    """Classify a transcript into an executor module name.

    Returns the executor module name (e.g. "volume_executor"), or
    "unknown" if neither the regex tier nor the LLM fallback can
    classify the command.
    """
    text = normalize_command(text)
    if not text:
        return "unknown"

    executor_name, score, _runner_up_name, _runner_up_score = _regex_classify_scored(text)
    if executor_name is not None and score >= _LLM_FALLBACK_FLOOR:
        logger.debug("Regex classified %r -> %s (score=%.2f)", text, executor_name, score)
        return executor_name

    executor_name = _llm_classify(text)
    logger.debug("LLM classified %r -> %s", text, executor_name)
    return executor_name


if __name__ == "__main__":
    # TESTING.md's 34-phrase manual checklist, each with its expected
    # executor - this is the regression check for any future _PATTERNS edit.
    _baseline_phrases: list[tuple[str, str]] = [
        ("open chrome", "app_executor"),
        ("launch spotify", "app_executor"),
        ("start notepad", "app_executor"),
        ("open downloads folder", "file_executor"),
        ("find my resume", "file_executor"),
        ("open desktop", "file_executor"),
        ("volume 40", "volume_executor"),
        ("mute", "volume_executor"),
        ("unmute", "volume_executor"),
        ("turn it up", "volume_executor"),
        ("connect to homewifi", "wifi_executor"),
        ("turn off wifi", "wifi_executor"),
        ("disconnect wifi", "wifi_executor"),
        ("connect my headphones", "bluetooth_executor"),
        ("turn on bluetooth", "bluetooth_executor"),
        ("pair my speaker", "bluetooth_executor"),
        ("print this", "printer_executor"),
        ("i want to print this pdf", "printer_executor"),
        ("show printers", "printer_executor"),
        ("minimise this", "window_executor"),
        ("switch to chrome", "window_executor"),
        ("close this window", "window_executor"),
        ("lock the screen", "window_executor"),
        ("play", "media_executor"),
        ("pause", "media_executor"),
        ("next song", "media_executor"),
        ("skip", "media_executor"),
        ("previous track", "media_executor"),
        ("how much battery", "sysinfo_executor"),
        ("cpu usage", "sysinfo_executor"),
        ("how much ram", "sysinfo_executor"),
        ("disk space", "sysinfo_executor"),
        ("type hello world", "typing_executor"),
        ("write dear sir", "typing_executor"),
    ]

    # Real routing bugs from UX_LOG.md, each fixed by a specific _PATTERNS
    # change - kept here so a future edit can't silently reintroduce one.
    _regression_phrases: list[tuple[str, str]] = [
        ("open this pc", "file_executor"),  # entry: app_executor's catch-all must not win via coverage alone
        ("open codex folder", "file_executor"),  # entry #8
        ("open codecs subfolder in documents folder", "file_executor"),  # entry #9, STT mishearing "codex"->"codecs"
        ("close text editor", "window_executor"),  # entry #11
        ("switch to visual studio code", "window_executor"),  # entry #13
        ("navigate to downloads folder", "file_executor"),  # entry #11, "navigate to" wasn't routed at all
        ("shut yourself down", "system_executor"),  # entry #13, shutdown phrasing variant
        ("go to documents codex folder", "file_executor"),  # entry #8, nested folder phrasing
        ("quit mimir", "system_executor"),  # window_executor's close/quit/exit pattern must not swallow this
        # Live-testing failures observed 2026-07-06:
        ("ok, open documents folder", "file_executor"),  # "ok," prefix broke every ^verb anchor
        ("can you hear me", "system_executor"),  # arrives as "hear me" after filler stripping
        ("can you hear me right now", "system_executor"),
        ("what commands can you accept", "system_executor"),
        # User-controlled model switching - must beat window_executor's
        # "^switch to .+" catch-all:
        ("switch to the smartest model", "model_executor"),
        ("switch to a smarter model", "model_executor"),
        ("switch to the basic model", "model_executor"),
        ("get smarter", "model_executor"),
        ("which model are you using", "model_executor"),
    ]

    mismatches = 0
    for phrase, expected in _baseline_phrases + _regression_phrases:
        name, score, runner_up_name, runner_up_score = _regex_classify_scored(normalize_command(phrase))
        actual = name if (name is not None and score >= _LLM_FALLBACK_FLOOR) else classify(phrase)
        status = "OK " if actual == expected else "MISMATCH"
        ambiguous = " (ambiguous)" if runner_up_name and (score - runner_up_score) <= _AMBIGUITY_MARGIN else ""
        if actual != expected:
            mismatches += 1
        print(
            f"{status} {phrase!r:48} expected={expected:20} actual={actual:20} "
            f"score={score:.2f} runner_up={runner_up_name}({runner_up_score:.2f}){ambiguous}"
        )

    print(f"\n{len(_baseline_phrases) + len(_regression_phrases)} phrases checked, {mismatches} mismatches")
