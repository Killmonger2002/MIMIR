# MIMIR user experience log

Running log of issues found during manual testing, used to drive fixes.
Each entry: description, status, fix/notes.

## 1. Multiple instances can run simultaneously
- **Observed**: Launching `launcher.bat`/`python main.py` while an instance is
  already running starts a second instance (duplicate tray icons, duplicate
  hotkeys, duplicate wake-word listeners).
- **Status**: Fixed
- **Fix**: `main.py` now acquires a global named mutex
  (`Global\MIMIR_SingleInstanceMutex`) via `win32event.CreateMutex` before
  starting. If it already exists, prints "MIMIR is already running." and
  exits with code 1.

## 2. No voice response to "hey jarvis"
- **Observed**: Saying "hey jarvis" produces no reaction (tray icon stays
  idle/blue, no listening/thinking/speaking transition).
- **Status**: Fixed (root cause found via `diagnose.py` + `test_devices.py`)
- **Root cause**: The laptop's built-in mic array hardware doesn't capture
  audio at all (confirmed near-zero levels across every host API). Only the
  Bluetooth headset mic (CMF Buds Pro 2, device index varies, WASAPI @
  16kHz) actually captures speech (mean_abs ~1100 vs ~3-9 for the built-in
  mic).
- **Fix**: Added `core/audio_device.py::get_input_device()`, which resolves
  `config.audio.input_device_name` (substring match, preferring WASAPI) to a
  device index. `core/stt.py` and `core/wake_word.py` now pass this device
  to `sd.InputStream`/`sd.rec`. `config.yaml` sets
  `audio.input_device_name: "CMF Buds Pro 2"`.
- **Caveat**: sounddevice device indices can shift when Bluetooth
  reconnects, but the name-substring match re-resolves correctly as long as
  "CMF Buds Pro 2" still appears in the device name.
- **Verified**: `diagnose.py` all 4 layers PASS (mic mean_abs=940.7,
  hey_jarvis score=0.46, whisper transcribed "Hello."). Lowered
  `wake_word.sensitivity` to 0.4 since 0.46 was just under the old 0.5
  threshold.
- **Bigger task**: evaluate multiple STT models (faster-whisper sizes,
  alternatives) for accuracy vs. speed before locking in `tiny.en`.

## 7. Missing command coverage found in real usage transcript
- **Observed** (from a real test session):
  - "Please set the brightness to 50%" -> unknown (no brightness control)
  - "Please list out all the commands that you can follow" / "What can you
    do?" -> unknown (no help/capabilities response)
  - "Please close yourself" -> unknown (no voice-driven shutdown)
- **Status**: Fixed
- **Fix**:
  - Added `executors/brightness_executor.py` (WMI
    `WmiMonitorBrightnessMethods.WmiSetBrightness`), routed via new
    `brightness_executor` regex patterns (`\bbrightness\b`,
    brighter/dimmer).
  - Added `executors/system_executor.py` for "what can you do" / "list
    commands" (speaks a capability summary) and "quit/close/shut down
    mimir" / "close yourself" (speaks "Goodbye." and triggers shutdown).
  - Added `ExecutorResult.shutdown: bool = False`; `main.py`'s command loop
    now calls `self.shutdown()` when an executor sets it.
- **Note**: volume command variations ("Volume up.", "set volume to 15%",
  "set the volume at 50%") all worked correctly already.

## 5. volume_executor crashed on every call
- **Observed**: Any volume command ("turn it up", etc.) logged
  `AttributeError: 'AudioDevice' object has no attribute 'Activate'` and
  replied "I couldn't change the volume."
- **Status**: Fixed
- **Root cause**: The installed `pycaw` version's `AudioUtilities.GetSpeakers()`
  returns an `AudioDevice` wrapper (not a raw COM device) which has no
  `.Activate`, but already exposes `.EndpointVolume`.
- **Fix**: `_get_volume_interface()` now returns `device.EndpointVolume`
  directly. Verified: "volume 30" -> "Volume set to 30 percent".

## 6. Ollama LLM fallback hangs/logs full traceback when Ollama isn't running
- **Observed**: Every command that misses the regex tier (e.g. "what is the
  weather") took ~10+s and logged a full `ConnectionError` traceback before
  falling back to "unknown".
- **Status**: Fixed
- **Fix**: `_llm_classify` now uses `ollama.Client(timeout=2)` and logs a
  one-line `logger.warning` instead of `logger.exception`. Ollama is still
  optional - install + `ollama pull phi3:mini` to enable the LLM fallback
  tier.

## 4. Transcript/Settings windows unresponsive (had to End Task)
- **Observed**: Opening "Open Transcript" or "Settings" from the tray
  created a window that couldn't be closed via the X button, didn't come to
  the front when clicked, and required Task Manager to kill the whole
  process.
- **Status**: Fixed
- **Root cause**: Both windows called `tk.Tk()` + `window.mainloop()`
  directly from the tray icon's callback thread. Tkinter is not thread-safe
  and a second `mainloop()` competing with the tray's message loop left the
  window unable to process events properly.
- **Fix**: Added `ui/ui_root.py` - a single hidden `Tk` root running its own
  `mainloop()` on one dedicated UI thread (started lazily on first use).
  `open_transcript_window`/`open_settings_window` now call
  `run_on_ui_thread(...)` to create/show a `Toplevel` on that thread, reuse
  the same window on repeat opens (raising/focusing it instead of duplicating),
  and bind the close button (`WM_DELETE_WINDOW`) to `withdraw` so closing
  hides the window cleanly instead of destroying the shared root.

## 8. Couldn't open apps (notepad/text editor) or navigate to subfolders
- **Observed**:
  - "open notepad" / "open text editor" launched `te.exe` (Windows Kits TAEF
    test runner) instead of Notepad, printing TAEF help text to the console.
  - "open codex folder" -> "I'm not sure which file or folder you mean."
    (only the 6 hardcoded top-level folders were recognized)
  - "go to documents codex folder" opened Documents itself, silently
    ignoring the "codex" subfolder.
- **Status**: Fixed
- **Root cause**: `app_executor`'s search index never included
  `C:\Windows\System32` (where `notepad.exe`/`calc.exe`/etc. live), and
  `_MATCH_THRESHOLD = 60` let "notepad"/"text editor" fuzzy-match `te.exe`.
  `file_executor` only matched the 6 hardcoded folder names and never looked
  for subfolders.
- **Fix**:
  - `app_executor.py`: added `C:\Windows\System32`/`%WINDIR%` (flat, non-recursive
    listing) to the app index, added an `_ALIASES` dict ("text editor" ->
    "notepad", "calculator" -> "calc", "task manager" -> "taskmgr", etc.)
    checked before fuzzy matching, and raised `_MATCH_THRESHOLD` to 70.
  - `file_executor.py`: `_open_folder` now also extracts any leftover words
    (e.g. "codex") after the known parent folder name (or, if no known
    parent matched, the whole phrase) and searches up to 3 levels deep
    inside the user's folders (`_find_subfolder`) for a matching
    subdirectory to open.
- **Verified**: "open notepad", "open chrome", "open text editor", "open
  calculator", "open codex folder", and "go to documents codex folder" all
  launched the correct app/folder.

## 9. "Please open..." commands unrecognized; text editor/calculator still failing; subfolders still not found
- **Observed**:
  - "Please open text editor." -> "I didn't understand that command." (the
    leading "Please" broke the `^(open|launch|...)` regex in both
    `intent_router` and `app_executor`).
  - "Open text editor." / "Open codecs subfolder in documents folder." -> a
    trailing period was included in the extracted app/folder name, and
    "codecs" (an STT mis-hearing of "Codex") didn't match the actual
    `Codex` folder.
- **Status**: Fixed
- **Fix**:
  - `core/intent_router.py`: added `_strip_filler_prefixes()`, which
    repeatedly strips leading "please" / "hey mimir" / "can/could/would you"
    etc. before regex classification, and exported it for executors to reuse.
  - `executors/app_executor.py`: `_extract_app_name` now also runs
    `_strip_filler_prefixes` and strips trailing punctuation (`.,!?`) before
    alias lookup / fuzzy matching.
  - `executors/file_executor.py`: `_open_folder` runs the same prefix strip,
    added "subfolder"/"directory" to `_STOPWORDS`, and `_find_subfolder` now
    falls back to fuzzy matching (`thefuzz.fuzz.ratio`, threshold 70) across
    all discovered subdirectories so STT mis-transcriptions like "codecs"
    still resolve to "Codex".
- **Verified**: "Please open text editor.", "Open text editor.", "Open
  calculator.", "Open codex folder", and "Open codecs subfolder in documents
  folder." all now produce the correct result.

## 10. Can't close apps by name or use editing keys (backspace/enter) while typing
- **Observed**:
  - "Close calculator" / "Close text editor." -> "I didn't understand that
    command." (`window_executor`'s "close" patterns only matched "close
    this/the window").
  - "Backspace, full time." -> unknown (no key-press support in
    `typing_executor`).
  - In-app calculator math ("calculate 45 times 55") not supported -
    acknowledged as not urgent, not addressed here.
- **Status**: Fixed (close-by-name + key presses)
- **Fix**:
  - `core/intent_router.py`: added
    `^(close|quit|exit)\s+(?!.*\b(yourself|mimir)\b).+` to
    `window_executor` patterns (placed before `system_executor` so "close
    yourself"/"quit mimir" still route there), and added
    `^(press\s+)?(backspace|delete|enter|return|tab|escape|new line)\b` to
    `typing_executor` patterns.
  - `executors/window_executor.py`: new `_close_target()` fuzzy-matches the
    spoken name against visible window titles and posts `WM_CLOSE` to the
    best match; `execute()` now strips filler prefixes/punctuation and
    handles "close/quit/exit <app>" (falling back to closing the foreground
    window for "close this/this window/it").
  - `executors/typing_executor.py`: now recognizes standalone key-press
    commands (backspace/delete/enter/return/tab/escape/new line), optionally
    repeated N times via a trailing number or number word ("twice", "three
    times"), via `pyautogui.press(key, presses=count)`.
- **Verified**: classification confirmed for "Close calculator", "Close text
  editor.", "Backspace, full time.", "Backspace twice", "press enter" (all
  route correctly); window enumeration/closing logic exercised against live
  windows (VS Code, File Explorer).

## 11. False-positive app launches for "spotify"/"amazon prime"; "close text editor" not found; "navigate to" not routed
- **Observed**:
  - "Open Spotify" / "Can you open Amazon Prime?" reported success ("Opening
    spotify"/"Opening amazon prime") but launched nothing useful - neither
    app is installed as a desktop exe, yet `thefuzz`'s default scorer gave
    short, unrelated System32 binaries ("mpnotify", "pr") scores of 80-90
    against these longer queries via partial-string matching.
  - "close text editor" / "Close text editor." -> "I couldn't find a window
    for text editor" (Notepad's window title is "Untitled - Notepad"; "text
    editor" doesn't fuzzy-match that title directly).
  - "Navigate to ..." commands were not routed to `file_executor` at all
    (only "open"/"go to" were recognized).
- **Status**: Fixed
- **Fix**:
  - `executors/app_executor.py`: after a fuzzy match, reject it if the
    matched index key and the search name don't share a first letter - this
    rejects "pr"/"mpnotify"-style false positives while leaving real matches
    (which virtually always share a first letter) untouched. Verified
    "spotify"/"amazon prime"/"youtube" now all correctly say "I couldn't find
    an app called ...".
  - `executors/window_executor.py`: `_switch_to`/`_close_target` now resolve
    the target through `app_executor._ALIASES` first (so "text editor" ->
    "notepad", which fuzzy-matches "Untitled - Notepad" at score 90).
  - `core/intent_router.py`: added "navigate to" alongside "open"/"go to" in
    the `file_executor` trigger pattern.
- **Note**: Spotify/Amazon Prime are not installed as desktop apps on this
  machine, so "I couldn't find an app called ..." is the correct (honest)
  response - install the desktop app or add a URL/protocol-launch alias if
  voice-launching them is wanted later.

## 12. "Microsoft Word/Excel/PowerPoint", Settings/Calendar/Paint, and double-sentence commands failed to open
- **Observed**:
  - "Open Microsoft Word" -> not found, while "Open Word" worked (alias only
    covered "word", not the "microsoft word" phrasing).
  - "Open Microsoft Excel" -> not found (no alias for Excel at all; "Open
    PowerPoint" worked by luck via fuzzy match to `powerpnt`).
  - "open settings" / "open calendar" / "open paint" -> not found - these
    are UWP/Store apps with no discoverable `.exe` in the search paths
    (`mspaint.exe` doesn't even exist on this Windows 11 build).
  - "Open paint. Open paint." (STT repeating the command) -> not found,
    because the whole string "paint. open paint" was used as the search
    term.
- **Status**: Fixed
- **Fix**:
  - `executors/app_executor.py`: extended `_ALIASES` with "microsoft
    word"/"word" -> `winword`, "microsoft excel"/"excel" -> `excel`,
    "microsoft powerpoint"/"powerpoint" -> `powerpnt`, plus outlook/edge.
  - Added `_PROTOCOL_ALIASES` (settings, calendar, camera, photos, store,
    mail, paint -> `ms-settings:`/`outlookcal:`/.../`ms-paint:`), launched
    via `os.startfile(protocol)` for apps that aren't `.exe`/`.lnk` files.
  - `_extract_app_name` now truncates at a repeated trigger phrase (e.g.
    "paint. open paint" -> "paint") so STT echoes/repeats don't break
    matching.
- **Verified**: "Open Microsoft Word", "Open Microsoft Excel", "Open
  PowerPoint", "open settings", "open calendar", "open paint", and "Open
  paint. Open paint." all now succeed.

## 3. No system notifications for MIMIR lifecycle / error states
- **Observed**: No Windows notification when MIMIR starts/stops, or when it
  hits a state that silently fails (e.g. system volume at 0 so TTS can't be
  heard, audio device unavailable, etc.)
- **Status**: Open
- **Fix plan**: Add toast notifications (e.g. via `win10toast` or `pystray`
  icon notify) for: MIMIR started, MIMIR stopped, and detected silent-failure
  conditions (system volume == 0 when about to speak, TTS/audio device
  errors).
