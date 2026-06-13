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

from thefuzz import process

from core.intent_router import _strip_filler_prefixes
from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.app_executor")

_APP_INDEX: dict[str, str] | None = None

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

_TRIGGER_RE = re.compile(r"^(?:open|launch|start|run)\s+(.+)$", re.IGNORECASE)


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
    """Return the cached app index, building it on first call."""
    global _APP_INDEX
    if _APP_INDEX is None:
        _APP_INDEX = _build_index()
    return _APP_INDEX


def _extract_app_name(command_text: str) -> str:
    """Strip leading verbs like 'open'/'launch'/'start' and trailing punctuation."""
    text = _strip_filler_prefixes(command_text.strip().lower())
    match = _TRIGGER_RE.match(text)
    if match:
        text = match.group(1).strip()

    # Drop a repeated trigger phrase, e.g. "paint. open paint" -> "paint".
    repeat_match = re.search(r"[.!?,]\s*(?:open|launch|start|run)\s+", text)
    if repeat_match:
        text = text[: repeat_match.start()]

    return text.strip(" .!?,")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Open an application matching the spoken name."""
    try:
        app_name = _extract_app_name(command_text)
        if not app_name:
            return ExecutorResult(success=False, speak="I didn't catch which app to open.")

        protocol = _PROTOCOL_ALIASES.get(app_name.lower())
        if protocol:
            os.startfile(protocol)
            return ExecutorResult(success=True, speak=f"Opening {app_name}")

        index = _get_index()
        if not index:
            return ExecutorResult(success=False, speak="I couldn't find any installed apps to search.")

        search_name = _ALIASES.get(app_name.lower(), app_name.lower())

        match = process.extractOne(search_name, index.keys())
        if match is None or match[1] <= _MATCH_THRESHOLD:
            return ExecutorResult(success=False, speak=f"I couldn't find an app called {app_name}")

        matched_name, score = match[0], match[1]

        # Reject matches that don't even share a first letter - fuzzy scorers
        # can give short, unrelated names (e.g. "pr", "mpnotify") a high
        # score against a longer query via partial-string matching.
        if matched_name[:1] != search_name[:1]:
            return ExecutorResult(success=False, speak=f"I couldn't find an app called {app_name}")
        path = index[matched_name]
        logger.debug("Matched app %r -> %r (score=%d)", app_name, path, score)

        subprocess.Popen(path, shell=True)
        return ExecutorResult(success=True, speak=f"Opening {app_name}")
    except Exception:
        logger.exception("app_executor failed")
        return ExecutorResult(success=False, speak="I couldn't open that app.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["open notepad", "launch chrome", "start some nonexistent app xyz123"]:
        print(cmd, "->", execute(cmd, _state))
