"""Tiered local LLM runtime - the "big brother protocol".

Three stages of local model, escalating by task complexity:

    Tier 1 (config.llm.model, e.g. phi3:mini):   routing, slot extraction.
    Tier 2 (config.llm.tier2_model, ~7B):        drafting, summarizing,
                                                 tool-calling agent work.
    Tier 3 (config.llm.tier3_model, ~14B):       long documents, debates,
                                                 multi-step planning.

Built for "average PC" hardware, which drives four design rules:

1. Escalation is USER-controlled, never automatic: MIMIR starts every
   session on tier 1 (least resources) and only moves up when the user
   says so ("get smarter" by voice, or the Settings selector). The
   assistant never decides on its own to spend the machine's RAM/CPU.
2. RAM-gated availability: tier 3 (~9GB resident) is auto-disabled on
   machines under config.llm.tier3_min_ram_gb total RAM (default 15.0 -
   nominal-16GB machines report ~15.7GB usable, so the gate must sit
   below the nominal size). Tier 2 is gated similarly but lower.
3. Fallback DOWN the ladder, never hard failure: asking for more than
   the machine (or the user's ceiling) allows silently runs on the best
   permitted tier. MIMIR stays useful (if degraded) even with no LLM at
   all - every caller treats None as "LLM unavailable" and keeps its
   regex-only behavior. Falling DOWN is not "automatic switching": it
   only ever uses less than what was asked for.
4. Tier 3 is never resident: keep_alive=0 means Ollama unloads it right
   after each use. Tier 1 stays warm (it's small and on the latency-
   sensitive routing path).

Also owns Ollama lifecycle: detection ("is the server reachable"),
autostart (find ollama.exe and launch `ollama serve` if installed but not
running), and a clear one-time degradation notice instead of the silent
per-request timeouts that hid a never-installed Ollama for weeks.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time

import psutil

from config import config

logger = logging.getLogger("mimir.llm_runtime")

# Where the Ollama Windows installer puts ollama.exe when it isn't on PATH.
_OLLAMA_CANDIDATE_PATHS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
    r"C:\Program Files\Ollama\ollama.exe",
]

# ~4.7GB model + Windows' own ~4GB baseline: on an 8GB machine tier 2
# swap-thrashes, so the floor is ~11GB (a nominal-12GB machine reports
# ~11.x usable) - matching the guidance in models/README.md.
_TIER2_MIN_RAM_GB = 11.0

_status_lock = threading.Lock()
_server_checked = False
_server_available = False
_pulled_models: set[str] | None = None

# Models that have completed at least one generation this session (so
# they're resident in RAM, within their keep_alive window). A model's
# FIRST call must load gigabytes from disk - far beyond the per-tier
# timeouts, which are tuned for warm inference - so cold calls get
# _COLD_LOAD_TIMEOUT_SEC instead. Discovered live: tier 1's 1.5s timeout
# structurally cannot survive phi3:mini's initial load.
_warmed_models: set[str] = set()
_COLD_LOAD_TIMEOUT_SEC = 120.0

# User-controlled ceiling on which tier may run ("big brother protocol"
# escalation is explicit, never automatic): MIMIR always starts on the
# least-resource tier, and only the user raises it - by voice ("get
# smarter" / "switch to the smartest model", see executors/model_executor)
# or from the Settings window. Deliberately NOT persisted to config:
# every startup returns to tier 1, per the product rule that the
# assistant never spends the machine's resources without being asked
# this session.
_active_tier = 1
_active_tier_lock = threading.Lock()

TIER_LABELS = {1: "basic", 2: "smarter", 3: "smartest"}


def get_active_tier() -> int:
    with _active_tier_lock:
        return _active_tier


def set_active_tier(tier: int) -> int:
    """Set the user-selected tier ceiling (clamped to 1-3) and return the
    tier that requests at that ceiling will ACTUALLY run on right now
    (after RAM/pulled-model gating) - so callers can tell the user
    honestly when the machine can't deliver what they asked for."""
    global _active_tier
    tier = max(1, min(3, int(tier)))
    with _active_tier_lock:
        _active_tier = tier
    logger.info("Active LLM tier set to %d (%s)", tier, TIER_LABELS[tier])
    resolved = resolve_tier(tier)
    return resolved if resolved is not None else 0


def _find_ollama_exe() -> str | None:
    exe = shutil.which("ollama")
    if exe:
        return exe
    for candidate in _OLLAMA_CANDIDATE_PATHS:
        if os.path.isfile(candidate):
            return candidate
    return None


def _ping_server(timeout: float = 2.0) -> bool:
    try:
        import ollama

        ollama.Client(timeout=timeout).list()
        return True
    except Exception:
        return False


def ensure_ollama_running() -> bool:
    """Return True if the Ollama server is reachable, starting it if the
    binary is installed but the server isn't running. Caches the result
    for the process lifetime (call reset_status() to force a recheck).

    Never raises - a machine without Ollama installed just means every
    tier is unavailable and MIMIR runs regex-only.
    """
    global _server_checked, _server_available
    with _status_lock:
        if _server_checked:
            return _server_available
        _server_checked = True

        if _ping_server():
            _server_available = True
            logger.info("Ollama server reachable")
            return True

        exe = _find_ollama_exe()
        if exe is None:
            logger.warning(
                "Ollama is not installed - all LLM tiers unavailable. MIMIR runs "
                "regex-only until it's installed (https://ollama.com/download) and "
                "the models in models/README.md are pulled."
            )
            _server_available = False
            return False

        if not config.llm.autostart_ollama:
            logger.warning("Ollama installed but not running, and autostart is disabled in config")
            _server_available = False
            return False

        logger.info("Starting Ollama server (%s serve)...", exe)
        try:
            subprocess.Popen(
                [exe, "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception:
            logger.exception("Failed to launch Ollama")
            _server_available = False
            return False

        # Poll briefly for the server to come up rather than assuming.
        for _ in range(20):
            time.sleep(0.5)
            if _ping_server(timeout=1.0):
                logger.info("Ollama server started")
                _server_available = True
                return True

        logger.warning("Ollama was launched but didn't become reachable within 10s")
        _server_available = False
        return False


def reset_status() -> None:
    """Force the next call to recheck server reachability and pulled models
    (e.g. after the user installs Ollama or pulls a model mid-session)."""
    global _server_checked, _server_available, _pulled_models
    with _status_lock:
        _server_checked = False
        _server_available = False
        _pulled_models = None


def _get_pulled_models() -> set[str]:
    """Names of locally-pulled models, cached. Empty set if server is down."""
    global _pulled_models
    if _pulled_models is not None:
        return _pulled_models
    try:
        import ollama

        response = ollama.Client(timeout=3.0).list()
        models = getattr(response, "models", None) or response.get("models", [])
        names = set()
        for m in models:
            name = getattr(m, "model", None) or m.get("model", "") or m.get("name", "")
            if name:
                names.add(name)
        _pulled_models = names
    except Exception:
        logger.debug("Couldn't list pulled models", exc_info=True)
        _pulled_models = set()
    return _pulled_models


def _model_is_pulled(model: str) -> bool:
    """Exact-name match, with ':latest' tag normalization only. Tags are
    NOT interchangeable: having qwen2.5:7b pulled must not make
    qwen2.5:14b (a different 9GB download) look available - so no
    base-name aliasing here."""
    pulled = _get_pulled_models()
    if model in pulled:
        return True
    if ":" not in model and f"{model}:latest" in pulled:
        return True
    if model.endswith(":latest") and model[: -len(":latest")] in pulled:
        return True
    return False


def _tier_config(tier: int) -> tuple[str, str | int, float]:
    """Return (model, keep_alive, timeout_sec) for a tier."""
    if tier >= 3:
        return config.llm.tier3_model, 0, config.llm.tier3_timeout_sec
    if tier == 2:
        return config.llm.tier2_model, config.llm.tier2_keep_alive, config.llm.tier2_timeout_sec
    return config.llm.model, config.llm.tier1_keep_alive, config.llm.timeout_sec


def _ram_allows(tier: int) -> bool:
    total_gb = psutil.virtual_memory().total / 1024**3
    if tier >= 3:
        return total_gb >= config.llm.tier3_min_ram_gb
    if tier == 2:
        return total_gb >= _TIER2_MIN_RAM_GB
    return True


def resolve_tier(requested: int) -> int | None:
    """Map a requested tier to the best actually-available tier at or
    below it, or None if no tier is usable at all (server down / nothing
    pulled). "Available" means: within the user-selected active-tier
    ceiling (escalation is user-controlled, never automatic), RAM gate
    passed, and the model actually pulled."""
    if not ensure_ollama_running():
        return None

    requested = min(requested, get_active_tier())

    for tier in range(min(requested, 3), 0, -1):
        model, _keep_alive, _timeout = _tier_config(tier)
        if not _ram_allows(tier):
            logger.debug("Tier %d gated off by RAM", tier)
            continue
        if not _model_is_pulled(model):
            logger.debug("Tier %d model %r not pulled", tier, model)
            continue
        return tier
    return None


def generate(prompt: str, tier: int = 1, num_predict: int | None = None, temperature: float = 0) -> str | None:
    """Run a single-prompt generation on the requested tier, falling back
    down the ladder if that tier isn't available on this machine.

    Returns the response text, or None if no LLM tier is usable - callers
    must already have a non-LLM fallback path (they all do: this is the
    same contract the old direct-ollama callers used).
    """
    actual_tier = resolve_tier(tier)
    if actual_tier is None:
        return None
    if actual_tier != tier:
        logger.info("Tier %d requested; running on tier %d (best available)", tier, actual_tier)

    model, keep_alive, timeout = _tier_config(actual_tier)
    if model not in _warmed_models:
        timeout = max(timeout, _COLD_LOAD_TIMEOUT_SEC)
    try:
        import ollama

        client = ollama.Client(timeout=timeout)
        response = client.generate(
            model=model,
            prompt=prompt,
            keep_alive=keep_alive,
            options={
                "num_predict": num_predict if num_predict is not None else config.llm.num_predict,
                "temperature": temperature,
            },
        )
        _warmed_models.add(model)
        return response.get("response", "")
    except Exception as exc:
        logger.warning("Tier %d generation failed: %s", actual_tier, exc)
        return None


def chat(messages: list[dict], tier: int = 2, tools: list | None = None, temperature: float = 0.3) -> dict | None:
    """Multi-turn chat (optionally with tool definitions) on the requested
    tier, with the same fallback-down behavior as generate(). Returns the
    raw response message dict ({'role', 'content', optional 'tool_calls'}),
    or None if no tier is usable.

    This is the entry point the upcoming agent loop builds on - tool
    calling is why the tier 2/3 default models are Qwen2.5 (reliable
    function-calling at 7B/14B).
    """
    actual_tier = resolve_tier(tier)
    if actual_tier is None:
        return None
    if actual_tier != tier:
        logger.info("Tier %d requested; running on tier %d (best available)", tier, actual_tier)

    model, keep_alive, timeout = _tier_config(actual_tier)
    if model not in _warmed_models:
        timeout = max(timeout, _COLD_LOAD_TIMEOUT_SEC)
    try:
        import ollama

        client = ollama.Client(timeout=timeout)
        kwargs = {"model": model, "messages": messages, "keep_alive": keep_alive, "options": {"temperature": temperature}}
        if tools:
            kwargs["tools"] = tools
        response = client.chat(**kwargs)
        _warmed_models.add(model)
        message = response.get("message", {})
        return dict(message) if message else None
    except Exception as exc:
        logger.warning("Tier %d chat failed: %s", actual_tier, exc)
        return None


def warm_up(tier: int = 1) -> bool:
    """Load a tier's model into RAM by generating a single token, so the
    first real request doesn't pay the multi-second disk-load cost.
    Called from main.py's startup prewarm for tier 1 (the latency-
    sensitive routing path). Returns True if the model is now warm."""
    start = time.time()
    result = generate("OK", tier=tier, num_predict=1)
    if result is not None:
        logger.info("Tier %d warmed up in %.1fs", tier, time.time() - start)
        return True
    return False


def get_status() -> dict:
    """Snapshot for logging/tray: server reachability and per-tier availability."""
    server = ensure_ollama_running()
    status = {"server": server, "tiers": {}}
    for tier in (1, 2, 3):
        model, _ka, _t = _tier_config(tier)
        status["tiers"][tier] = {
            "model": model,
            "ram_ok": _ram_allows(tier),
            "pulled": _model_is_pulled(model) if server else False,
        }
    return status


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json

    print(json.dumps(get_status(), indent=2))
    print("resolve_tier(3) ->", resolve_tier(3))
    print("generate('Say OK', tier=1) ->", generate("Reply with exactly: OK", tier=1))
