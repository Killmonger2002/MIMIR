"""Launches installed applications by fuzzy name match.

Voice command patterns handled:
    - "open chrome"
    - "launch spotify"
    - "start notepad"
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading

from thefuzz import fuzz, process

from core.text_utils import normalize_command
from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.app_executor")

_APP_INDEX: dict[str, str] | None = None
_APP_INDEX_LOCK = threading.Lock()

_SEARCH_DIRS = [
    os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu"),
    os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu"),
    r"C:\Program Files",
    r"C:\Program Files (x86)",
]

# Top-level only (not walked recursively) - common built-in Windows apps.
_FLAT_SEARCH_DIRS = [
    os.path.expandvars(r"%WINDIR%\System32"),
    os.path.expandvars(r"%WINDIR%"),
]

# Common spoken names that don't match their executable name closely enough
# for fuzzy matching to find reliably.
_ALIASES = {
    "text editor": "notepad",
    "notepad": "notepad",
    "calculator": "calc",
    "file explorer": "explorer",
    "explorer": "explorer",
    "task manager": "taskmgr",
    "command prompt": "cmd",
    "terminal": "cmd",
    "control panel": "control",
    "paint": "mspaint",
    "wordpad": "write",
    "microsoft word": "winword",
    "word": "winword",
    "microsoft excel": "excel",
    "excel": "excel",
    "microsoft powerpoint": "powerpnt",
    "powerpoint": "powerpnt",
    "microsoft outlook": "outlook",
    "outlook": "outlook",
    "microsoft edge": "msedge",
    "edge": "msedge",
    # VS Code's own executable is literally "Code.exe", which doesn't
    # fuzzy-match "vs code" at all (tested live: scores ~40, loses to
    # unrelated index entries) - the common spoken abbreviation needs an
    # explicit alias, not fuzzy matching, to resolve reliably.
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "visual studio code": "visual studio code",
}

# Windows protocol/URI launchers for built-in apps that aren't plain .exe/.lnk
# files discoverable by _build_index (UWP/Settings apps).
_PROTOCOL_ALIASES = {
    "settings": "ms-settings:",
    "windows settings": "ms-settings:",
    "calendar": "outlookcal:",
    "camera": "microsoft.windows.camera:",
    "photos": "ms-photos:",
    "store": "ms-windows-store:",
    "microsoft store": "ms-windows-store:",
    "mail": "outlookmail:",
    "paint": "ms-paint:",
}

_MATCH_THRESHOLD = 70
# Fuzzy scores between _MATCH_THRESHOLD and this launch a best-guess
# executable - confirm with the user first rather than opening a random
# similarly-named program.
_CONFIDENT_SCORE = 85
# Threshold for the alias-phrase fuzzy stage (see _resolve_app) - higher
# than _MATCH_THRESHOLD because a shared "microsoft " prefix alone
# inflates the ratio score even for a near-nonsense suffix (measured
# live: "microsoft xn" scores 81 against "microsoft excel" - a real but
# garbled query - vs. 90 for "microsoft excent" - an actual near-miss
# typo of "excel"). 85 lets the genuine near-miss through while still
# sending the more garbled query to the full-index stage instead of a
# false-confident alias resolution.
_ALIAS_MATCH_THRESHOLD = 85
# A fuzzy-matched app name shorter than this is almost never a real
# answer to a longer spoken query - it's index noise (2-3 letter
# executables like "ex"/"od" that happen to be short substrings of
# almost anything). Guards the full-index stage now that a length-aware
# scorer (see _resolve_app) makes tiny fragments rare but not impossible.
_MIN_MATCH_NAME_LEN = 3

_TRIGGER_RE = re.compile(r"^(?:open|launch|start|run)\s+(.+)$", re.IGNORECASE)


def _resolve_app(app_name: str, index: dict[str, str]) -> tuple[str, int] | None:
    """Resolve a spoken app name to (index_key, score), or None.

    Two stages, both using plain length-normalized fuzz.ratio rather than
    thefuzz's default WRatio - WRatio's aggressive partial-match mode was
    observed live scoring tiny unrelated executable fragments ("ex", "od")
    around 90 against completely different queries ("microsoft excent",
    "vs code"), because a short candidate trivially "fits inside" almost
    any longer query under partial matching. Plain ratio penalizes that
    length mismatch instead, and empirically kept every common app's
    exact-name match at 100 while sinking junk fragments below 70.

    1. Fuzzy match against the curated _ALIASES phrases - a small,
       human-vetted pool, so a typo/mishearing of a common app name
       ("microsoft excent") resolves correctly instead of competing
       against ~2000 index entries dominated by non-app internals
       (build-tool helpers, uninstallers, etc.) that a raw index walk of
       Program Files inevitably contains.
    2. Fuzzy match against the full index, trying several candidates (not
       just the single best score) since a nonsense top match shouldn't
       block trying the next reasonable one - this is exactly what broke
       "vs code" and "microsoft excent" before: the top (bad) candidate
       failed a sanity check and the code gave up instead of trying the
       next candidate down.
    """
    alias_match = process.extractOne(app_name, list(_ALIASES.keys()), scorer=fuzz.ratio)
    if alias_match and alias_match[1] >= _ALIAS_MATCH_THRESHOLD:
        search_name = _ALIASES[alias_match[0]]
        if search_name in index:
            return search_name, 100

    candidates = process.extract(app_name, index.keys(), scorer=fuzz.ratio, limit=5)
    for name, score in candidates:
        if score < _MATCH_THRESHOLD:
            break  # extract() is sorted descending - nothing further clears the bar either
        if len(name) < _MIN_MATCH_NAME_LEN:
            continue
        return name, score
    return None


def _build_index() -> dict[str, str]:
    """Walk known directories and build a name -> path index of .exe/.lnk files."""
    index: dict[str, str] = {}
    for base_dir in _SEARCH_DIRS:
        if not os.path.isdir(base_dir):
            continue
        for root, _dirs, files in os.walk(base_dir):
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in (".exe", ".lnk"):
                    continue
                name = os.path.splitext(fname)[0].lower()
                index[name] = os.path.join(root, fname)

    for base_dir in _FLAT_SEARCH_DIRS:
        if not os.path.isdir(base_dir):
            continue
        try:
            for fname in os.listdir(base_dir):
                ext = os.path.splitext(fname)[1].lower()
                if ext != ".exe":
                    continue
                name = os.path.splitext(fname)[0].lower()
                path = os.path.join(base_dir, fname)
                if not os.path.isfile(path):
                    continue
                index.setdefault(name, path)
        except OSError:
            continue

    logger.debug("Built app index with %d entries", len(index))
    return index


def _get_index() -> dict[str, str]:
    """Return the cached app index, building it on first call.

    Double-checked locking: main.py prewarms this on a background thread
    at startup, so a command arriving before that finishes could otherwise
    race with it here and trigger two redundant directory walks.
    """
    global _APP_INDEX
    if _APP_INDEX is None:
        with _APP_INDEX_LOCK:
            if _APP_INDEX is None:
                _APP_INDEX = _build_index()
                # The vocabulary hint may have already cached a prompt
                # without app names (built before this index existed) -
                # let it pick them up now.
                from core.vocabulary import reset_cache

                reset_cache()
    return _APP_INDEX


def _extract_app_name(command_text: str) -> str:
    """Strip leading verbs like 'open'/'launch'/'start' and trailing punctuation."""
    text = normalize_command(command_text)
    match = _TRIGGER_RE.match(text)
    if match:
        text = match.group(1).strip()

    # Drop a repeated trigger phrase, e.g. "paint. open paint" -> "paint".
    repeat_match = re.search(r"[.!?,]\s*(?:open|launch|start|run)\s+", text)
    if repeat_match:
        text = text[: repeat_match.start()]

    return text.strip(" .!?,")


_BARE_TRIGGER_VERBS = ("open", "launch", "start", "run")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Open an application matching the spoken name."""
    try:
        app_name = _extract_app_name(command_text)
        # _extract_app_name() never returns truly empty for a recognized
        # trigger verb alone (e.g. "open" -> "open", not "") - so the bare
        # verb itself is the real "nothing usable" signal here, not just
        # an empty string.
        if not app_name or app_name.lower() in _BARE_TRIGGER_VERBS:
            from core.slot_extractor import extract_slot

            refined = extract_slot(command_text, "the name of the application to open")
            if refined:
                app_name = refined
            if not app_name or app_name.lower() in _BARE_TRIGGER_VERBS:
                return ExecutorResult(success=False, speak="I didn't catch which app to open.")

        protocol = _PROTOCOL_ALIASES.get(app_name.lower())
        if protocol:
            os.startfile(protocol)
            return ExecutorResult(success=True, speak=f"Opening {app_name}")

        # If this isn't a known app alias, prefer a matching subfolder in the
        # last folder the user navigated into (e.g. "open documents" then
        # "open dell" should open Documents\Dell, not some unrelated
        # Dell-branded background utility that happens to fuzzy-match).
        if app_name.lower() not in _ALIASES:
            last_folder = state.get_last_folder()
            if last_folder:
                from executors.file_executor import _find_subfolder, _open_resolved

                subfolder, confident = _find_subfolder(app_name, [last_folder])
                if subfolder is not None:
                    return _open_resolved(subfolder, confident, state)

        index = _get_index()
        if not index:
            return ExecutorResult(success=False, speak="I couldn't find any installed apps to search.")

        resolved = _resolve_app(app_name.lower(), index)
        if resolved is None:
            return ExecutorResult(success=False, speak=f"I couldn't find an app called {app_name}")

        matched_name, score = resolved
        path = index[matched_name]
        logger.debug("Matched app %r -> %r (score=%d)", app_name, path, score)

        def _launch() -> ExecutorResult:
            subprocess.Popen(path, shell=True)
            # Announce what actually MATCHED, not the raw transcript - a
            # mishearing like "Open Microsoft Excent" that fuzzy-resolves
            # to the Excel alias should say "Opening excel", both because
            # it's the truth about what's happening and because it's the
            # user's only feedback that the mishearing was recovered
            # rather than parroted back.
            return ExecutorResult(success=True, speak=f"Opening {matched_name}")

        if score < _CONFIDENT_SCORE:
            return ExecutorResult(
                success=True,
                speak="",
                confirm=f"Did you mean {matched_name}?",
                on_confirm=_launch,
            )
        return _launch()
    except Exception:
        logger.exception("app_executor failed")
        return ExecutorResult(success=False, speak="I couldn't open that app.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["open notepad", "launch chrome", "start some nonexistent app xyz123", "open"]:
        print(cmd, "->", execute(cmd, _state))
