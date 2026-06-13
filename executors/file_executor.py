"""Opens common folders and searches for files by name.

Voice command patterns handled:
    - "open downloads folder"
    - "open desktop"
    - "find my resume"
"""

from __future__ import annotations

import logging
import os
import re

from thefuzz import fuzz

from core.intent_router import _strip_filler_prefixes
from executors.base import ExecutorResult
from state import AppState

logger = logging.getLogger("mimir.file_executor")

_FOLDER_MAP = {
    "downloads": os.path.expanduser(r"~\Downloads"),
    "desktop": os.path.expanduser(r"~\Desktop"),
    "documents": os.path.expanduser(r"~\Documents"),
    "pictures": os.path.expanduser(r"~\Pictures"),
    "music": os.path.expanduser(r"~\Music"),
    "videos": os.path.expanduser(r"~\Videos"),
}

_FOLDER_RE = re.compile(
    r"\b(downloads|desktop|documents|pictures|music|videos)\b", re.IGNORECASE
)

_FIND_RE = re.compile(r"^(?:find|search for|locate)\s+(?:my\s+)?(.+)$", re.IGNORECASE)

_SEARCH_DIRS = [
    os.path.expanduser(r"~\Desktop"),
    os.path.expanduser(r"~\Downloads"),
    os.path.expanduser(r"~\Documents"),
]

_STOPWORDS = {
    "open", "go", "to", "the", "a", "folder", "navigate", "please",
    "my", "in", "into", "subfolder", "sub-folder", "directory",
}

_MAX_SUBFOLDER_DEPTH = 3


_FUZZY_SUBFOLDER_THRESHOLD = 70


def _find_subfolder(name: str, base_dirs: list[str], max_depth: int = _MAX_SUBFOLDER_DEPTH) -> str | None:
    """Search base_dirs for a subdirectory whose name matches `name`.

    Tries exact match, then substring match, then fuzzy match (to tolerate
    STT mis-transcriptions like "codecs" for "CODEX").
    """
    name_lower = name.lower()
    candidates: list[tuple[str, str]] = []  # (dir_name_lower, full_path)

    for base_dir in base_dirs:
        if not os.path.isdir(base_dir):
            continue
        base_depth = base_dir.rstrip(os.sep).count(os.sep)
        for root, dirs, _files in os.walk(base_dir):
            depth = root.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= max_depth:
                dirs[:] = []
                continue
            for d in dirs:
                candidates.append((d.lower(), os.path.join(root, d)))

    for d_lower, path in candidates:
        if d_lower == name_lower:
            return path

    for d_lower, path in candidates:
        if name_lower in d_lower:
            return path

    best_path = None
    best_score = 0
    for d_lower, path in candidates:
        score = fuzz.ratio(name_lower, d_lower)
        if score > best_score:
            best_score = score
            best_path = path
    if best_score >= _FUZZY_SUBFOLDER_THRESHOLD:
        return best_path

    return None


def _open_folder(command_text: str) -> ExecutorResult | None:
    """Handle 'open X folder' / 'open X' / 'open X Y folder' commands."""
    command_text = _strip_filler_prefixes(command_text.strip().lower())
    match = _FOLDER_RE.search(command_text)

    words = [w for w in re.findall(r"[a-zA-Z0-9']+", command_text.lower()) if w not in _STOPWORDS]

    if match:
        key = match.group(1).lower()
        parent_path = _FOLDER_MAP[key]
        remaining = [w for w in words if w != key]

        if remaining:
            subfolder = _find_subfolder(" ".join(remaining), [parent_path])
            if subfolder is None and len(remaining) > 1:
                for word in remaining:
                    subfolder = _find_subfolder(word, [parent_path])
                    if subfolder is not None:
                        break
            if subfolder is not None:
                try:
                    os.startfile(subfolder)
                    return ExecutorResult(success=True, speak=f"Opening {os.path.basename(subfolder)}")
                except Exception:
                    logger.exception("Failed to open folder %s", subfolder)
                    return ExecutorResult(success=False, speak="I couldn't open that folder")

        try:
            os.startfile(parent_path)
            return ExecutorResult(success=True, speak=f"Opening {key}")
        except Exception:
            logger.exception("Failed to open folder %s", parent_path)
            return ExecutorResult(success=False, speak=f"I couldn't open the {key} folder")

    if words:
        subfolder = _find_subfolder(" ".join(words), list(_FOLDER_MAP.values()))
        if subfolder is None and len(words) > 1:
            for word in words:
                subfolder = _find_subfolder(word, list(_FOLDER_MAP.values()))
                if subfolder is not None:
                    break
        if subfolder is not None:
            try:
                os.startfile(subfolder)
                return ExecutorResult(success=True, speak=f"Opening {os.path.basename(subfolder)}")
            except Exception:
                logger.exception("Failed to open folder %s", subfolder)
                return ExecutorResult(success=False, speak="I couldn't open that folder")

    return None


def _search_everything(query: str) -> str | None:
    """Attempt a search via the Everything SDK. Returns a path or None."""
    try:
        import pyeverything  # type: ignore

        results = pyeverything.search(query)
        for result in results:
            return result
    except Exception:
        logger.debug("Everything search unavailable, falling back", exc_info=True)
    return None


def _search_fallback(query: str) -> str | None:
    """Fall back to a simple substring search over common user folders."""
    query_lower = query.lower()
    for folder in _SEARCH_DIRS:
        if not os.path.isdir(folder):
            continue
        try:
            for fname in os.listdir(folder):
                if query_lower in fname.lower():
                    return os.path.join(folder, fname)
        except OSError:
            continue
    return None


def _find_file(command_text: str) -> ExecutorResult | None:
    """Handle 'find my X' / 'search for X' commands."""
    match = _FIND_RE.match(command_text.strip())
    if not match:
        return None
    query = match.group(1).strip()
    if not query:
        return ExecutorResult(success=False, speak="I didn't catch what to look for.")

    path = _search_everything(query)
    if path is None:
        path = _search_fallback(query)

    if path is None:
        return ExecutorResult(success=False, speak=f"I couldn't find a file matching {query}")

    try:
        os.startfile(path)
        return ExecutorResult(success=True, speak=f"Opening {os.path.basename(path)}")
    except Exception:
        logger.exception("Failed to open file %s", path)
        return ExecutorResult(success=False, speak="I found the file but couldn't open it.")


def execute(command_text: str, state: AppState) -> ExecutorResult:
    """Open a known folder or find and open a file by name."""
    try:
        text = command_text.strip()

        result = _find_file(text)
        if result is not None:
            return result

        result = _open_folder(text)
        if result is not None:
            return result

        return ExecutorResult(success=False, speak="I'm not sure which file or folder you mean.")
    except Exception:
        logger.exception("file_executor failed")
        return ExecutorResult(success=False, speak="Something went wrong with that file command.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["open downloads folder", "open desktop", "find my resume"]:
        print(cmd, "->", execute(cmd, _state))
