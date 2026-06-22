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

## 13. Couldn't self-shutdown by voice, couldn't toggle Wi-Fi, couldn't switch windows; requested live transcript + folder context memory
- **Observed**:
  - "Shut down" / "Shut yourself down" / "Turn yourself off" -> "I didn't
    understand that command." (only "close/quit/exit yourself/mimir"
    phrasing was recognized).
  - "Turn off wifi" -> "I couldn't change the Wi-Fi state" with no
    explanation.
  - "Switch to [app]" reliably crashed window_executor with a raised
    exception, caught and reported as "I couldn't do that with the window."
  - Request: make the transcript window auto-update instead of needing a
    manual Refresh click.
  - Request: remember the last folder opened in conversation, so "open
    documents" then "open dell folder" opens Dell inside Documents instead
    of searching from scratch / failing.
- **Status**: Fixed (shutdown, transcript, folder memory); Wi-Fi and window
  switching root causes diagnosed below - one is a hard OS limitation.
- **Root cause / Fix**:
  - **Shutdown**: `system_executor._QUIT_RE` (shared by `intent_router` for
    routing) only matched "<verb> yourself/mimir" with the verb immediately
    adjacent. Broadened to also match bare "shut down"/"power off", "turn
    yourself/mimir off", and "shut yourself/mimir down" (verb...object split
    by the name). `intent_router.py` now imports and reuses
    `system_executor._QUIT_RE.pattern` directly instead of a separately
    hand-maintained copy, so the two can't drift out of sync again.
  - **Wi-Fi toggle**: confirmed via direct test that `netsh interface set
    interface Wi-Fi admin=disabled` returns "The requested operation
    requires elevation (Run as administrator)" - MIMIR isn't running
    elevated. This is a real OS permission requirement, not a bug in our
    code. `wifi_executor._set_radio` now detects "elevation" in the netsh
    output and speaks a clear message ("I need administrator permissions to
    change Wi-Fi...") instead of a generic failure. **Still open**: turning
    Wi-Fi on/off by voice requires running MIMIR as Administrator, or a
    future rewrite using the WinRT Radio API which doesn't need elevation.
  - **Window switching**: reproduced a real `pywintypes.error: (126, '
    SetForegroundWindow', 'The specified module could not be found.')` when
    calling `win32gui.SetForegroundWindow` on this machine (likely a pywin32
    DLL-loading quirk). Replaced with `_force_foreground()`, which restores
    minimized windows and uses `ctypes.windll.user32.SetForegroundWindow`
    with `AttachThreadInput` around the call (the standard workaround for
    Windows' foreground-switch lock) - confirmed working via direct test.
    Also added `_best_window_match()` (substring match before fuzzy) so
    short queries like "switch to code" don't lose to an unrelated window
    title under the default fuzzy scorer, the same class of bug fixed for
    app launching in entry #11.
  - **Live transcript**: `ui/transcript_window.py` now schedules a
    `window.after(750, ...)` self-rescheduling poll that repopulates the
    text widget whenever the conversation log grows, and auto-scrolls to
    the bottom (`text_widget.see(tk.END)`) - no more manual Refresh button.
  - **Folder context memory**: added `AppState.set_last_folder` /
    `get_last_folder`. `file_executor._open_folder` now records the path
    every time it opens a folder, and - when a command has no recognized
    top-level folder keyword (e.g. "open dell folder") - searches inside the
    last-opened folder first before falling back to the full
    Desktop/Downloads/Documents/Pictures/Music/Videos search.
- **Verified**: "shut down", "shut yourself down", "turn yourself off",
  "power off" all now trigger `shutdown=True`; "switch to code"/"switch to
  visual studio code"/"switch to settings" all succeed without raising;
  "open documents" then "open dell folder" resolved to
  `Documents\Dell...` via direct execution test.

## 14. "Open X in File Explorer" opened a random unrelated subfolder; no way to list a folder's contents by voice
- **Observed**:
  - "Open Documents in File Explorer" / "Open the folder documents in File
    Explorer." -> opened an unrelated, deeply-nested subfolder instead of
    Documents itself.
  - "In the folder documents, list all the files." / "Let's tell the files
    in the folder document." -> "I didn't understand that command." (no
    "list files" capability existed at all).
- **Status**: Fixed
- **Root cause**: `_open_folder`'s `_STOPWORDS` set didn't include
  "file"/"explorer", so "in file explorer" survived into the subfolder
  search words. The lone leftover word "file" then substring-matched a
  real, unrelated folder 3 levels deep in Documents (whose name happened to
  contain "file...") because `_find_subfolder`'s substring check
  (`name_lower in d_lower`) has no word boundary - any short word can
  falsely match inside a longer folder name.
- **Fix**:
  - `file_executor.py`: added a `_TRAILING_EXPLORER_RE` that strips a
    trailing "in/on (the) (windows) file explorer" phrase before extracting
    search words, and added "file"/"files"/"explorer"/"windows" to
    `_STOPWORDS` as a backstop.
  - Added a new "list files" capability: `_LIST_FILES_RE` (`list/show/tell
    ... files` or "what files") triggers `_list_files()`, which resolves
    the target folder (explicit folder name, else the last-opened folder
    via `state.get_last_folder()`), lists its contents with
    `os.listdir`, and speaks up to 10 entries plus a "+N more" count.
    Wired into `core/intent_router.py`'s `file_executor` patterns.
- **Verified**: "Open Documents in File Explorer", "Open the folder
  documents in File Explorer.", "In the folder documents, list all the
  files.", "Let's tell the files in the folder document.", and "list files
  in documents" all now produce the correct result.
- **Scope note**: this covers opening folders/subfolders and listing
  contents - the user asked for "all File Explorer operations". Renaming,
  moving, copying, and deleting files/folders by voice are not implemented
  yet; those are destructive/higher-risk and would need an explicit
  confirmation step before being added.

## 15. Voice "shut down" crashed with a generic error instead of shutting down; every folder open spawned a new File Explorer window
- **Observed**:
  - Saying "shut down"/"goodbye" etc. produced "Sorry, something went
    wrong with that." instead of actually shutting MIMIR down.
  - Every "open X" folder command opened a brand-new File Explorer window,
    even if one was already open.
- **Status**: Fixed
- **Root cause**:
  - `Mimir.shutdown()` (in `main.py`) called
    `self.wake_word_listener.stop()`, which does `self._thread.join()`.
    Voice-triggered shutdown runs inside the wake-word listener's own
    `on_detected` callback - i.e. on that listener's own thread - so
    `thread.join()` was joining the current thread, which Python raises as
    `RuntimeError: cannot join current thread`. That exception propagated
    up and was caught by `main.py`'s generic per-command exception handler,
    which spoke the fallback "Sorry, something went wrong" message instead
    of ever reaching the real shutdown sequence.
  - `file_executor._open_path` always called `os.startfile(path)`, which
    unconditionally launches a new `explorer.exe` window - there was no
    reuse of an already-open window.
- **Fix**:
  - `main.py`: `shutdown()` now only announces and then hands the actual
    stop/join/exit sequence to a brand-new dedicated thread
    (`_shutdown_worker`), so the join calls are never made from the thread
    being joined, regardless of whether shutdown was triggered by voice,
    hotkey, or the tray menu. The worker finishes with `os._exit(0)` for a
    guaranteed full-process exit (tray/hotkey/lifecycle threads are all
    daemon threads, but `os._exit` makes this unconditional).
  - `executors/file_executor.py`: added `_navigate_existing_explorer()`,
    which uses `win32com.client.Dispatch("Shell.Application")` to find an
    already-open Explorer window and call `.Navigate2(path)` on it; falls
    back to `os.startfile(path)` (new window) only if none is open.
- **Verified**: direct test confirmed opening Documents then Downloads
  reuses the same Explorer window (`Shell.Application` window count stayed
  at 1); shutdown logic reviewed to confirm the worker thread is distinct
  from the wake-word/hotkey/tray callback threads in every call path.

## 16. "Open Dell" after "Open Documents" launched a Dell utility exe instead of the Dell subfolder
- **Observed**: "Open documents." then "Open Dell." -> "Opening dell", but
  no Dell folder actually opened.
- **Status**: Fixed
- **Root cause**: "Open Dell" doesn't mention a known folder keyword
  (downloads/desktop/documents/etc.), so `intent_router` routed it to
  `app_executor`, not `file_executor` - meaning the folder-context memory
  added in entry #13 (last-opened-folder lookup) never ran. `app_executor`
  then did a legitimate fuzzy search and found real, installed Dell
  utilities (`Dell.TechHub.exe`, `DellSupportAssistControlPanel.exe`, etc.
  - this machine has over a dozen Dell-branded background services), which
  share a first letter with "dell" and passed the existing false-positive
  guard, so it "successfully" launched one of those instead.
- **Fix**: `app_executor.execute()` now checks, before searching the app
  index, whether `app_name` is a known alias (`_ALIASES`); if not, it first
  tries `file_executor._find_subfolder()` against
  `state.get_last_folder()` (the folder the user last navigated into). If a
  matching subfolder is found there, it's opened instead of falling
  through to the app search. Known aliases ("notepad", "calculator",
  "chrome", etc.) skip this check entirely, so core app-launching behavior
  is unaffected.
- **Verified**: "Open documents." then "Open Dell." now opens
  `Documents\Dell` (confirmed via direct execution test) instead of an
  unrelated Dell background utility.

## 17. Couldn't open "This PC" or a drive letter, no way to go up a directory, had to repeat "close file explorer" for each window
- **Observed**:
  - "Open this PC." / "Open disk D." -> routed to `app_executor`, which
    found no real match and either failed outright or falsely reported
    success without opening anything useful.
  - "Go back in the directory." -> "I didn't understand that command." (no
    parent-directory/back navigation existed at all).
  - With several File Explorer windows open, "Close File Explorer" only
    closed one - had to repeat the command once per window, since every
    Explorer window shares the literal title "File Explorer" and
    `_best_window_match` just picks one arbitrarily.
- **Status**: Fixed
- **Fix**:
  - `file_executor.py`: added `_SPECIAL_FOLDERS` ("this pc"/"my computer"
    -> `shell:MyComputerFolder`), `_DRIVE_RE` ("disk/drive X" -> `X:\`,
    verified to exist via `os.path.isdir` before opening), and
    `_GO_BACK_RE` ("go back"/"go up"/"previous directory"/"parent folder")
    which resolves `os.path.dirname()` of `state.get_last_folder()`. All
    three go through the existing `_open_path` (so they get File Explorer
    window reuse and update the folder-context memory too).
  - `core/intent_router.py`: routed these phrases to `file_executor`, and
    narrowed `app_executor`'s catch-all "open ..." pattern with extra
    negative lookaheads for `this pc`/`my computer`/`disk|drive <letter>`
    so it stops intercepting them before file_executor's patterns get a
    chance (legit app names like "disk cleanup" are unaffected since the
    lookahead only excludes "disk/drive" followed by a single letter).
  - `executors/window_executor.py`: `_close_target` now closes **every**
    matching window (not just the closest-scoring one) whenever the
    resolved target is File Explorer specifically, or when the user says
    "close all <target>" explicitly - other single-window apps keep the
    previous closest-match behavior.
- **Verified**: "Open this PC.", "Open disk D.", and "Open documents." then
  "Go back in the directory." (-> parent folder) all resolved correctly via
  direct execution tests; opening 3 separate Explorer windows then "Close
  File Explorer" closed all of them in one call (confirmed via
  `Shell.Application` window count dropping from 4 to 0).

## 18. STT-inserted commas after verbs broke commands entirely; no way to open a nested folder under an arbitrary (non-root) parent name
- **Observed**:
  - "Open, Dell." / "Open, then." -> "I didn't understand that command."
    even though "Open Dell." (no comma) worked fine.
  - "Open the soft folder in the folder cortex." -> opened Codex (the
    closest fuzzy match to "cortex" found by treating "soft"/"cortex" as
    flat, unordered search words) instead of recognizing "cortex" as the
    parent folder and "soft" as the subfolder to look for inside it.
  - (Noted, not a bug: "Go back to that creek." still correctly went up a
    directory despite the garbled tail - the transcript log faithfully
    shows what STT actually heard, which is expected/useful for debugging.)
- **Status**: Fixed
- **Root cause**: every verb-prefix regex across the codebase
  (`^(?:open|launch|start|run)\s+...`, `^(?:close|quit|exit)\s+...`, the
  `_QUIT_RE` shutdown patterns, etc.) requires literal whitespace
  immediately after the verb. When STT inserts a comma ("Open, Dell."),
  none of these regexes match, so commands silently fell through to
  "unknown" - and each executor was independently doing its own ad hoc
  `_strip_filler_prefixes(...).strip(...)` call that never handled internal
  punctuation either.
- **Fix**:
  - Added `core/text_utils.py` - a shared, leaf module (no dependency on
    `intent_router` or any executor, avoiding import cycles) exporting
    `normalize_command()`, which lowercases, strips filler prefixes, and
    collapses internal commas to spaces before trimming leading/trailing
    punctuation. `core/intent_router.py`, `system_executor.py`,
    `app_executor.py`, `window_executor.py`, and `file_executor.py` were
    all switched from their previous one-off stripping logic to this one
    shared function, so a comma-after-verb fix applies everywhere at once.
  - `file_executor.py`: added `_NESTED_RE` ("X folder in/inside (the)
    (folder) Y") plus `_resolve_named_folder()`/`_open_nested_folder()`,
    which resolves an arbitrary parent folder name Y anywhere under the
    known roots first, then looks for X as a subfolder of Y specifically -
    instead of treating every word as an unordered, flat search term.
- **Verified**: "Open, Dell.", "Open, then." (now a clear "couldn't find an
  app called then" instead of "unknown"), "Shut down, Mimir." (now
  shuts down), and "Open the soft folder in the folder cortex." (now
  resolves "cortex" to Codex first, then looks for "soft" inside it) all
  produce correct results via direct execution tests.

## 19. Window close/switch confirmations echoed the raw (mis-heard) phrase instead of what was actually resolved
- **Observed**: "close all windows of pile explorer." correctly closed the
  File Explorer windows, but MIMIR replied "Closing all windows of pile
  explorer" - parroting back the STT mis-hearing ("pile" for "file")
  instead of confirming what it actually understood and closed.
- **Status**: Fixed
- **Root cause**: `_close_target`'s and `_switch_to`'s success messages were
  built from the raw spoken `target` string, not the window title that was
  actually resolved and acted on. The matching logic was correct throughout
  - only the spoken confirmation was wrong.
- **Fix**: `executors/window_executor.py` now builds the spoken confirmation
  from the resolved match: `_switch_to` and the single-window branch of
  `_close_target` use `best_title` (the actual window title); the
  close-all branch uses the distinct matched title name when every closed
  window shares the same title (the common case, e.g. several "X - File
  Explorer" windows), falling back to the spoken target only if multiple
  different window names were closed at once.
- **Verified**: direct test of "close all windows of pile explorer." now
  replies "Closing Downloads - File Explorer" (the real, resolved window
  title) instead of echoing "pile explorer".

## 3. No system notifications for MIMIR lifecycle / error states
- **Observed**: No Windows notification when MIMIR starts/stops, or when it
  hits a state that silently fails (e.g. system volume at 0 so TTS can't be
  heard, audio device unavailable, etc.)
- **Status**: Open
- **Fix plan**: Add toast notifications (e.g. via `win10toast` or `pystray`
  icon notify) for: MIMIR started, MIMIR stopped, and detected silent-failure
  conditions (system volume == 0 when about to speak, TTS/audio device
  errors).
