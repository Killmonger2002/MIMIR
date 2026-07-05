"""Interactive STT model size benchmark: tiny.en vs base.en vs small.en.

Run manually in a normal terminal (`python benchmark_stt.py`). Requires you
to read each test phrase aloud once; the same captured audio is then run
through all three model sizes for a fair, controlled comparison - this
isolates the model as the only variable, since recording fresh per model
would let take-to-take variation in your pronunciation/pacing/background
noise contaminate the comparison.

Review the printed summary table before changing config.yaml's
stt.model_size. Bigger models are more accurate but slower - there's no
universally correct choice, only what's worth it for your voice/mic/room.
"""

from __future__ import annotations

import gc
import time

from thefuzz import fuzz

from core import stt

# Pulled selectively from TESTING.md's checklist plus the specific
# STT-mishearing-driven entries in UX_LOG.md (#16 "Dell"/"then" homophone,
# #18 "codecs"/"Codex", #19 "file"/"pile") - not every log entry, since
# most are routing/logic bugs unrelated to transcription accuracy.
_TEST_PHRASES = [
    "open chrome",
    "open downloads folder",
    "open dell",
    "open codex folder",
    "close file explorer",
    "go back to the previous directory",
    "open the soft folder in the folder codex",
    "switch to visual studio code",
    "what files are in documents",
    "shut down",
    "volume forty",
    "how much battery",
]

_MODEL_SIZES = ["tiny.en", "base.en", "small.en"]


def _word_match_count(expected: str, transcript: str) -> tuple[int, int]:
    """Return (matching_words, total_expected_words) by position."""
    expected_words = expected.lower().split()
    transcript_words = transcript.lower().split()
    matches = sum(
        1 for i, word in enumerate(expected_words) if i < len(transcript_words) and transcript_words[i] == word
    )
    return matches, len(expected_words)


def record_phrase(phrase: str):
    input(f"\nPress Enter, then say: {phrase!r}")
    print("Recording...")
    audio = stt.record_until_silence()
    print("Captured.")
    return audio


def benchmark_model(model_size: str, recordings: dict[str, "object"]) -> list[dict]:
    from faster_whisper import WhisperModel

    print(f"\nLoading {model_size}...")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    results = []
    for phrase, audio in recordings.items():
        start = time.perf_counter()
        segments, _info = model.transcribe(audio, language="en")
        transcript = " ".join(s.text.strip() for s in segments).strip()
        latency = time.perf_counter() - start

        matches, total = _word_match_count(phrase, transcript)
        results.append(
            {
                "phrase": phrase,
                "transcript": transcript,
                "latency": latency,
                "fuzz_ratio": fuzz.ratio(phrase, transcript.lower()),
                "word_match": f"{matches}/{total}",
            }
        )

    del model
    gc.collect()
    return results


def _print_summary_table(all_results: dict[str, list[dict]]) -> None:
    print("\n" + "=" * 100)
    print("RESULTS")
    print("=" * 100)

    for model_size, results in all_results.items():
        avg_latency = sum(r["latency"] for r in results) / len(results)
        avg_fuzz = sum(r["fuzz_ratio"] for r in results) / len(results)
        print(f"\n--- {model_size} (avg latency={avg_latency:.2f}s, avg fuzz ratio={avg_fuzz:.1f}) ---")
        for r in results:
            print(
                f"  {r['phrase']!r:45} -> {r['transcript']!r:45} "
                f"(fuzz={r['fuzz_ratio']:3d}, words={r['word_match']:5}, {r['latency']:.2f}s)"
            )


if __name__ == "__main__":
    print(f"Will record {len(_TEST_PHRASES)} phrases, then benchmark {', '.join(_MODEL_SIZES)}.")
    recordings = {phrase: record_phrase(phrase) for phrase in _TEST_PHRASES}

    all_results = {size: benchmark_model(size, recordings) for size in _MODEL_SIZES}

    _print_summary_table(all_results)
