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

from core.text_utils import normalize_command
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

_TRAILING_EXPLORER_RE = re.compile(
    r"\s*(?:in|on)\s+(?:the\s+)?(?:windows\s+)?file\s+explorer\s*\.?$", re.IGNORECASE
)

_LIST_FILES_RE = re.compile(r"\b(list|show|tell)\b.*\bfiles?\b|\bwhat files\b", re.IGNORECASE)

_SPECIAL_FOLDERS = {
    "this pc": ("shell:MyComputerFolder", "This PC"),
    "my computer": ("shell:MyComputerFolder", "This PC"),
}

_DRIVE_RE = re.compile(r"\b(?:disk|drive)\s+([a-z])\b", re.IGNORECASE)

_GO_BACK_RE = re.compile(
    r"\b(go back|go up|up a (level|directory|folder)|"
    r"previous (directory|folder)|parent (directory|folder))\b",
    re.IGNORECASE,
)

# "open X folder in (the) (folder) Y" - an arbitrary parent folder name (not
# one of the 6 hardcoded roots), e.g. "open the soft folder in the folder
# codex" should look inside Codex, not just match "codex" as a loose word.
_NESTED_RE = re.compile(
    r"^(?:open\s+)?(?:the\s+)?(.+?)\s+(?:folder|sub-?folder)\s+"
    r"(?:in|inside)\s+(?:the\s+)?(?:folder\s+)?(.+?)\s*$",
    re.IGNORECASE,
)

_SEARCH_DIRS = [
    os.path.expanduser(r"~\Desktop"),
    os.path.expanduser(r"~\Downloads"),
    os.path.expanduser(r"~\Documents"),
]

_STOPWORDS = {
    "open", "go", "to", "the", "a", "folder", "navigate", "please",
    "my", "in", "into", "subfolder", "sub-folder", "directory",
    "file", "files", "explorer", "windows",
}

_MAX_SUBFOLDER_DEPTH = 3


_FUZZY_SUBFOLDER_THRESHOLD = 70


def _find_subfolder(
    name: str, base_dirs: list[str], max_depth: int = _MAX_SUBFOLDER_DEPTH
) -> tuple[str | None, bool]:
    """Search base_dirs for a subdirectory whose name matches `name`.

    Returns (path, confident). Tries exact match, then substring match
    (both confident), then fuzzy match (NOT confident - it exists to
    tolerate STT mis-transcriptions like "codecs" for "CODEX", so a hit
    is a guess the caller should confirm before opening).
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
            return path, True

    for d_lower, path in candidates:
        if name_lower in d_lower:
            return path, True

    best_path = None
    best_score = 0
    for d_lower, path in candidates:
        score = fuzz.ratio(name_lower, d_lower)
        if score > best_score:
            best_score = score
            best_path = path
    if best_score >= _FUZZY_SUBFOLDER_THRESHOLD:
        return best_path, False

    return None, True


def _navigate_existing_explorer(path: str) -> bool:
    """Navigate an already-open File Explorer window to path instead of
    spawning a new one. Returns True if an existing window was reused."""
    try:
        import win32com.client

        shell = win32com.client.Dispatch("Shell.Application")
        for window in shell.Windows():
            try:
                if window.FullName.lower().endswith("explorer.exe"):
                    window.Navigate2(path)
                    # Bring the reused window to the front - without this
                    # the navigation happens silently behind whatever the
                    # user is looking at, which reads as "nothing
                    # happened" even though MIMIR announced success
                    # (observed exactly this way in live testing).
                    try:
                        from executors.window_executor import _force_foreground

                        _force_foreground(window.HWND)
                    except Exception:
                        logger.debug("Couldn't bring the Explorer window to the front", exc_info=True)
                    return True
            except Exception:
                continue
    except Exception:
        logger.debug("Shell.Application window reuse unavailable", exc_info=True)
    return False


def _open_path(path: str, state: AppState, label: str | None = None) -> ExecutorResult:
    try:
        if not _navigate_existing_explorer(path):
            os.startfile(path)
        state.set_last_folder(path)
        speak_label = label or os.path.basename(path.rstrip(os.sep)) or path
        return ExecutorResult(success=True, speak=f"Opening {speak_label}")
    except Exception:
        logger.exception("Failed to open folder %s", path)
        return ExecutorResult(success=False, speak="I couldn't open that folder")


def _resolve_named_folder(name: str) -> tuple[str | None, bool]:
    """Find a folder named `name` among the known roots or their subfolders.

    Returns (path, confident), same contract as _find_subfolder."""
    name = re.sub(r"\bfolder\b", "", name, flags=re.IGNORECASE).strip()
    if not name:
        return None, True
    key = name.lower()
    if key in _FOLDER_MAP:
        return _FOLDER_MAP[key], True
    return _find_subfolder(name, list(_FOLDER_MAP.values()))


def _open_nested_folder(command_text: str, state: AppState) -> ExecutorResult | None:
    """Handle 'open X folder in (the) folder Y', where Y is an arbitrary
    folder name discovered elsewhere (not one of the 6 hardcoded roots)."""
    text = normalize_command(command_text)
    text = _TRAILING_EXPLORER_RE.sub("", text).strip()

    match = _NESTED_RE.match(text)
    if not match:
        return None

    child_name, parent_name = match.group(1).strip(), match.group(2).strip()
    parent_path, parent_confident = _resolve_named_folder(parent_name)
    if parent_path is None:
        return ExecutorResult(success=False, speak=f"I couldn't find a folder called {parent_name}")
    parent_base = os.path.basename(parent_path.rstrip(os.sep))

    child_path, child_confident = _find_subfolder(child_name, [parent_path])
    if child_path is not None:
        if parent_confident and child_confident:
            return _open_path(child_path, state)
        # Multi-hop resolution where at least one hop was a fuzzy guess -
        # say what was actually resolved and check before opening.
        child_base = os.path.basename(child_path.rstrip(os.sep))
        return ExecutorResult(
            success=True,
            speak="",
            confirm=f"Opening {child_base} inside {parent_base}. Is that right?",
            on_confirm=lambda p=child_path: _open_path(p, state),
        )

    # Child not found at all: offer the parent rather than silently
    # opening it (the old behavior), so a mis-resolved chain is caught.
    return ExecutorResult(
        success=True,
        speak="",
        confirm=f"I couldn't find {child_name} inside {parent_base}. Open {parent_base} instead?",
        on_confirm=lambda p=parent_path: _open_path(p, state),
    )


def _open_special(command_text: str, state: AppState) -> ExecutorResult | None:
    """Handle 'this PC', drive letters, and 'go back'/parent-folder commands."""
    text = normalize_command(command_text)
    text = _TRAILING_EXPLORER_RE.sub("", text).strip()

    if _GO_BACK_RE.search(text):
        last_folder = state.get_last_folder()
        if not last_folder:
            return ExecutorResult(success=False, speak="I don't have a previous folder to go back to.")
        parent = os.path.dirname(last_folder.rstrip(os.sep))
        if not parent or parent == last_folder.rstrip(os.sep):
            return ExecutorResult(success=False, speak="There's no folder above this one.")
        return _open_path(parent, state)

    for phrase, (uri, label) in _SPECIAL_FOLDERS.items():
        if phrase in text:
            return _open_path(uri, state, label=label)

    drive_match = _DRIVE_RE.search(text)
    if drive_match:
        letter = drive_match.group(1).upper()
        drive_path = f"{letter}:{os.sep}"
        if not os.path.isdir(drive_path):
            return ExecutorResult(success=False, speak=f"I couldn't find drive {letter}")
        return _open_path(drive_path, state, label=f"{letter} drive")

    return None


def _open_resolved(path: str, confident: bool, state: AppState) -> ExecutorResult:
    """Open a resolved folder directly, or via confirmation when the
    resolution was a fuzzy guess (e.g. "dell" heard as "then")."""
    if confident:
        return _open_path(path, state)
    base = os.path.basename(path.rstrip(os.sep))
    return ExecutorResult(
        success=True,
        speak="",
        confirm=f"Did you mean the folder {base}?",
        on_confirm=lambda: _open_path(path, state),
    )


def _open_folder(command_text: str, state: AppState) -> ExecutorResult | None:
    """Handle 'open X folder' / 'open X' / 'open X Y folder' commands."""
    command_text = normalize_command(command_text)
    command_text = _TRAILING_EXPLORER_RE.sub("", command_text).strip()
    match = _FOLDER_RE.search(command_text)

    words = [w for w in re.findall(r"[a-zA-Z0-9']+", command_text.lower()) if w not in _STOPWORDS]

    if match:
        key = match.group(1).lower()
        parent_path = _FOLDER_MAP[key]
        remaining = [w for w in words if w != key]

        if remaining:
            subfolder, confident = _find_subfolder(" ".join(remaining), [parent_path])
            if subfolder is None and len(remaining) > 1:
                for word in remaining:
                    subfolder, confident = _find_subfolder(word, [parent_path])
                    if subfolder is not None:
                        break
            if subfolder is not None:
                return _open_resolved(subfolder, confident, state)

        return _open_path(parent_path, state)

    if words:
        # Prefer the last folder the user navigated into, so "open documents"
        # then "open dell" resolves "dell" inside Documents first.
        last_folder = state.get_last_folder()
        if last_folder:
            subfolder, confident = _find_subfolder(" ".join(words), [last_folder])
            if subfolder is not None:
                return _open_resolved(subfolder, confident, state)

        subfolder, confident = _find_subfolder(" ".join(words), list(_FOLDER_MAP.values()))
        if subfolder is None and len(words) > 1:
            for word in words:
                subfolder, confident = _find_subfolder(word, list(_FOLDER_MAP.values()))
                if subfolder is not None:
                    break
        if subfolder is not None:
            return _open_resolved(subfolder, confident, state)

    return None


def _list_files(command_text: str, state: AppState) -> ExecutorResult | None:
    """Handle 'list files in X' / 'what files are in X' commands."""
    if not _LIST_FILES_RE.search(command_text):
        return None

    text = normalize_command(command_text)
    text = _TRAILING_EXPLORER_RE.sub("", text).strip()

    folder_match = _FOLDER_RE.search(text)
    if folder_match:
        key = folder_match.group(1).lower()
        folder_path = _FOLDER_MAP[key]
        words = [w for w in re.findall(r"[a-zA-Z0-9']+", text) if w not in _STOPWORDS and w != key]
        if words:
            # Listing files is read-only, so a fuzzy guess is harmless -
            # no confirmation needed here, unlike the open-folder paths.
            subfolder, _confident = _find_subfolder(" ".join(words), [folder_path])
            if subfolder is not None:
                folder_path = subfolder
    else:
        folder_path = state.get_last_folder()

    if not folder_path or not os.path.isdir(folder_path):
        return ExecutorResult(success=False, speak="I'm not sure which folder you mean.")

    try:
        entries = sorted(os.listdir(folder_path))
    except OSError:
        logger.exception("Failed to list folder %s", folder_path)
        return ExecutorResult(success=False, speak="I couldn't read that folder.")

    folder_name = os.path.basename(folder_path)
    if not entries:
        return ExecutorResult(success=True, speak=f"{folder_name} is empty.")

    preview = entries[:10]
    summary = ", ".join(preview)
    if len(entries) > 10:
        summary += f", and {len(entries) - 10} more"
    return ExecutorResult(success=True, speak=f"{folder_name} contains: {summary}")


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

        result = _list_files(text, state)
        if result is not None:
            return result

        result = _find_file(text)
        if result is not None:
            return result

        result = _open_nested_folder(text, state)
        if result is not None:
            return result

        result = _open_special(text, state)
        if result is not None:
            return result

        result = _open_folder(text, state)
        if result is not None:
            return result

        from core.slot_extractor import extract_slot

        refined = extract_slot(text, "the name of the folder to open")
        if refined:
            result = _open_folder(f"open {refined}", state)
            if result is not None:
                return result

        return ExecutorResult(success=False, speak="I'm not sure which file or folder you mean.")
    except Exception:
        logger.exception("file_executor failed")
        return ExecutorResult(success=False, speak="Something went wrong with that file command.")


if __name__ == "__main__":
    _state = AppState()
    for cmd in ["open downloads folder", "open desktop", "find my resume", "open the"]:
        print(cmd, "->", execute(cmd, _state))
