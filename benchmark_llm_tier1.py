"""Tier-1 model benchmark: which local model should do command routing
and slot extraction?

Runs each candidate against the same prompt templates MIMIR actually
uses (core/intent_router._llm_classify and core/slot_extractor), on
phrases the regex tier can't catch - i.e. tier 1's real workload - and
reports accuracy + warm latency per model.

Run manually whenever considering a tier-1 swap:

    python benchmark_llm_tier1.py [--models phi3:mini qwen2.5:0.5b ...]

Smaller isn't automatically better: a model that misroutes commands
costs more user trust than it saves in RAM. Decide from this table, not
from model-card vibes.
"""

from __future__ import annotations

import argparse
import time

import ollama

# Must mirror core/intent_router._LLM_CATEGORIES.
_CATEGORIES = [
    "app_executor", "file_executor", "volume_executor", "brightness_executor",
    "wifi_executor", "bluetooth_executor", "printer_executor", "window_executor",
    "media_executor", "sysinfo_executor", "typing_executor", "system_executor",
    "browser_executor", "unknown",
]

# Phrases deliberately phrased so the regex tier misses them - this is
# exactly the traffic tier 1 sees in production.
_CLASSIFY_CASES = [
    ("get rid of all this noise from the speakers", "volume_executor"),
    ("the screen is hurting my eyes", "brightness_executor"),
    ("make the display easier to read at night", "brightness_executor"),
    ("i need to get on the internet at my friends house", "wifi_executor"),
    ("make the sound come through my earbuds", "bluetooth_executor"),
    ("i need a hard copy of this document", "printer_executor"),
    ("how much juice does the laptop have left", "sysinfo_executor"),
    ("take down a note of what i say", "typing_executor"),
    ("get me my vacation photos", "file_executor"),
    ("i'm done with this program get it off my screen", "window_executor"),
]

# (text, slot description, expected substring in the extraction)
_SLOT_CASES = [
    ("umm could you get me that spreadsheet program from microsoft", "the name of the application to open", "excel"),
    ("open up that photo editing thing from adobe", "the name of the application to open", "photoshop"),
    ("go to the place where all my downloaded stuff ends up", "the name of the folder to open", "download"),
]


def _classify_prompt(text: str) -> str:
    categories_str = ", ".join(_CATEGORIES)
    return (
        f"Classify this voice command into exactly one category: "
        f"[{categories_str}]. Reply with ONLY the category name.\n\n"
        f"Command: {text}"
    )


def _slot_prompt(text: str, slot_description: str) -> str:
    return (
        f"Extract {slot_description} from this voice command. "
        f'Reply with ONLY the extracted value, no explanation. '
        f'If nothing relevant is present, reply with exactly "NONE".\n\n'
        f"Command: {text}"
    )


def _parse_category(response: str) -> str:
    lowered = response.strip().lower()
    for valid in _CATEGORIES:
        if valid in lowered:
            return valid
    return "unknown"


def benchmark_model(model: str) -> dict:
    client = ollama.Client(timeout=120.0)

    # Warm up (load into RAM) - untimed.
    client.generate(model=model, prompt="OK", keep_alive="2m", options={"num_predict": 1})

    latencies: list[float] = []
    classify_hits = 0
    for text, expected in _CLASSIFY_CASES:
        start = time.perf_counter()
        response = client.generate(
            model=model, prompt=_classify_prompt(text), keep_alive="2m",
            options={"num_predict": 5, "temperature": 0},
        )
        latencies.append(time.perf_counter() - start)
        got = _parse_category(response.get("response", ""))
        hit = got == expected
        classify_hits += hit
        print(f"  {'OK ' if hit else 'MISS'} {text!r:55} -> {got}")

    slot_hits = 0
    for text, description, expected_substr in _SLOT_CASES:
        start = time.perf_counter()
        response = client.generate(
            model=model, prompt=_slot_prompt(text, description), keep_alive="2m",
            options={"num_predict": 10, "temperature": 0},
        )
        latencies.append(time.perf_counter() - start)
        got = response.get("response", "").strip()
        hit = expected_substr.lower() in got.lower()
        slot_hits += hit
        print(f"  {'OK ' if hit else 'MISS'} slot: {text!r:48} -> {got!r}")

    return {
        "model": model,
        "classify_acc": classify_hits / len(_CLASSIFY_CASES),
        "slot_acc": slot_hits / len(_SLOT_CASES),
        "mean_latency": sum(latencies) / len(latencies),
        "max_latency": max(latencies),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models", nargs="+", default=["phi3:mini", "qwen2.5:1.5b", "qwen2.5:0.5b"],
    )
    args = parser.parse_args()

    results = []
    for model in args.models:
        print(f"\n=== {model} ===")
        try:
            results.append(benchmark_model(model))
        except Exception as exc:
            print(f"  FAILED: {exc}")

    print("\n" + "=" * 78)
    print(f"{'model':<16} {'classify':<10} {'slots':<8} {'mean lat':<10} {'max lat':<10}")
    print("-" * 78)
    for r in results:
        print(
            f"{r['model']:<16} {r['classify_acc']:<10.0%} {r['slot_acc']:<8.0%} "
            f"{r['mean_latency']:<10.2f} {r['max_latency']:<10.2f}"
        )
