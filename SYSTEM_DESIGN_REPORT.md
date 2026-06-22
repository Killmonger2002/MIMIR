# MIMIR — Capability & System Design Report

Prepared for strategic planning discussion. Reflects the system as of the
current build (Windows 11, local-only, no cloud dependency except an
optional local Ollama LLM).

## 1. Stated goal

Replace keyboard and mouse for everyday desktop control — app launching,
file/folder navigation, window management, system settings, media, typed
text — **except** gaming and in-app browsing, which stay manual. This is a
meaningfully large scope: not "a voice command shortcut tool" but "a
primary input method." That framing matters for every decision below,
because the current architecture was built incrementally to fix concrete
bugs, not designed up front against that scope.

## 2. Current architecture

```
mic -> wake word -> STT -> intent router -> executor -> TTS
```

- **Wake word**: openWakeWord ("hey jarvis"), runs continuously on its own
  daemon thread, ONNX inference, sensitivity 0.4.
- **STT**: faster-whisper, `tiny.en`, int8/CPU. Lazy-loaded, unloaded after
  5 idle minutes to free RAM.
- **Intent routing**: `core/intent_router.py`. Two tiers:
  1. **Regex tier (primary)** — an ordered table of ~12 executors, each
     with a handful of hand-written regex patterns. First match wins.
  2. **LLM tier (fallback only)** — if no regex matches, asks a local
     Ollama `phi3:mini` (2s timeout) to pick a category name. It only
     *classifies which executor*, it does not extract arguments/slots.
- **Execution**: each executor is a stateless `execute(text, state) ->
  ExecutorResult` function. 12 executors today: app, file, volume,
  brightness, wifi, bluetooth, printer, window, media, sysinfo, typing,
  system(self).
- **TTS**: Piper (`en_US-amy-medium`), lazy-loaded/idle-unloaded like STT.
- **Shared state**: one `AppState` object (mode, conversation log,
  `last_folder`). No per-domain state beyond that.
- **Supporting systems**: global hotkeys (pause/resume, quit), idle-unload
  lifecycle manager, system tray, a live-updating Tk transcript window.

This is a **rule-based, hand-maintained command grammar** with a narrow LLM
escape hatch — not a general natural-language agent. It scales by adding
more regexes and more `_ALIASES`/`_PROTOCOL_ALIASES` entries, one at a
time, per app/folder/phrasing discovered through testing.

## 3. What works today (capability matrix)

| Domain | Status | Notes |
|---|---|---|
| App launching | Strong | Alias map + fuzzy search over Start Menu/Program Files/System32, with a first-letter false-positive guard and protocol-launch support for UWP apps (Settings, Paint, Calendar, Store). |
| Folder navigation | Strong | 6 root folders, recursive subfolder search (depth 3, exact→substring→fuzzy), drive letters, "This PC", "go back"/parent directory, nested "X folder in Y folder" resolution, reuses one File Explorer window instead of spawning new ones. |
| Folder *context memory* | Present, narrow | `state.last_folder` lets "open documents" → "open dell" resolve inside Documents. This is the **only** cross-command memory in the system — no equivalent for windows, apps, or anything else. |
| Listing files | Basic | "list files in X" speaks up to 10 names + a count. No file *operations* (rename/move/copy/delete) — none implemented, intentionally, pending a confirmation-flow design (see §5). |
| Window management | Moderate | Minimize/maximize/lock/sleep work. Switch/close by name use substring-then-fuzzy title matching, with `AttachThreadInput`-based foreground forcing (worked around a real Windows API quirk). Close supports "close all X" and auto-closes-all when X is File Explorer. No tiling/snapping/resizing. |
| Volume / brightness | Strong | Direct hardware APIs (pycaw / WMI), no ambiguity to resolve. |
| Wi-Fi | Partial — **OS-level blocker** | Connect/disconnect work. Radio on/off requires Administrator (`netsh interface set ... admin=`) — MIMIR isn't elevated, and now reports this clearly instead of failing silently. Fixing this for real means either running elevated (UX cost: UAC prompts) or rewriting on the WinRT Radio API. |
| Bluetooth / printer / media / sysinfo | Basic | Implemented, lightly tested relative to app/file/window. |
| Typing / key presses | Strong | Literal text typing + named key presses (backspace, enter, tab, etc.) with repeat counts ("backspace twice"). |
| Self-control | Strong | "shut down"/"goodbye"/"power off" all work; fixed a real thread-self-join deadlock that silently broke voice shutdown. |
| In-app control (the actual keyboard/mouse replacement case) | **Absent** | There is no mechanism to click a button, fill a form field, scroll, or otherwise drive the UI *inside* an arbitrary application. Everything today is OS-level (open/close/switch/type-as-keystrokes), not "do X inside app Y." |

## 4. Recurring architectural patterns (useful context for future decisions)

These show up repeatedly across the bug history (`UX_LOG.md`, 19 entries)
and are worth knowing as *patterns*, not just fixes:

1. **Fuzzy-matching false positives.** A short or generic spoken word can
   score deceptively high against an unrelated long string (e.g. "pr" vs.
   "amazon prime", "file" vs. a folder named
   "files-mentioned-by-the-user"). Mitigated case-by-case with
   first-letter guards, substring-before-fuzzy ordering, and stopword
   lists — never solved structurally.
2. **STT noise tolerance is reactive.** Every "X wasn't understood" bug
   this session traced back to a literal phrasing assumption (a comma
   after a verb, a trailing period, "please" mid-sentence, "file"
   misheard as "pile"). Each was patched individually
   (`core/text_utils.normalize_command` now centralizes punctuation
   normalization, but word-level mis-hearings are still handled ad hoc
   per command).
3. **Routing is a flat regex table, order-dependent.** Every new pattern
   risks shadowing or being shadowed by another (e.g. app_executor's
   catch-all had to grow negative lookaheads three separate times to stop
   intercepting "this pc"/"disk d"/folder keywords meant for
   file_executor). This works at ~12 executors; it gets harder to reason
   about as it grows.
4. **Confirmations must reflect what was *resolved*, not what was *heard*.**
   Several bugs were the system doing the right thing but speaking back
   the raw mis-transcribed phrase, eroding trust ("Closing all windows of
   pile explorer"). Fixed in window_executor; worth auditing other
   executors for the same pattern.
5. **Context memory is single-purpose.** `last_folder` was added because
   one specific complaint demanded it. There's no general concept of "the
   thing we were just talking about" that other domains could reuse.

## 5. Known gaps / open items

- **No in-app UI automation.** This is the gap that matters most against
  the stated goal. Today MIMIR can open/close/switch apps and type
  keystrokes, but cannot inspect or act on what's *inside* a window
  (click a specific button, read a specific field, navigate a menu).
  Closing this requires a fundamentally different mechanism: Windows UI
  Automation (accessibility tree) for known apps, and/or a vision-based
  "computer use"-style model for arbitrary apps.
- **No compound/multi-step commands.** "Open Chrome and search for X" is
  two intents; the router only ever dispatches to one executor.
- **No destructive file operations**, by design, pending a confirmation
  flow ("Did you mean delete X? Say yes to confirm") — deferred rather
  than rejected.
- **Wi-Fi radio toggle needs Administrator.** Real OS constraint, not a
  code bug.
- **No toast notifications** for MIMIR start/stop or silent-failure states
  (e.g. system volume at 0 when about to speak) — `UX_LOG.md` item #3,
  open since the start of testing.
- **STT model choice (`tiny.en`) has never been benchmarked** against
  larger faster-whisper sizes for accuracy — flagged early, never
  revisited, and likely the single highest-leverage change for reducing
  the mis-hearing-driven bugs in §4.2.
- **No mechanism to detect "user is gaming / in a browser tab" and back
  off.** The stated exception (gaming, in-app browsing) currently has no
  implementation — MIMIR would attempt to act on commands during those
  too; there's no focus-aware suppression.

## 6. Questions worth deciding before the next phase

These are the actual fork points — not yet decided, listed so they can be
discussed rather than defaulted into:

1. **Stay regex-first, or move to LLM-first intent parsing?** Regex is
   fast, free, fully offline, and auditable, but every new phrasing is a
   manual patch. An LLM-first router (even a small local one) could
   generalize phrasing and extract slots (app name, folder name, target)
   in one pass instead of the current extract-then-fuzzy-match pipeline —
   at the cost of latency, and needing guardrails against
   hallucinated actions.
2. **How to reach "in-app control"?** Realistically two non-exclusive
   paths: (a) deep, per-app integration via UI Automation/accessibility
   APIs for a short list of high-value apps (browser, Office, Explorer),
   or (b) a general vision/accessibility-tree "agent" that can act on
   whatever's on screen. (a) is more reliable but doesn't generalize; (b)
   generalizes but is slower and riskier to get right reliably.
3. **Generalize context memory, or keep it scoped?** Worth deciding
   whether "remember the last X" should become a general pattern (last
   app, last window, last contact, last file) with one mechanism, rather
   than bolting on a new `state.last_*` field every time a domain needs
   it.
4. **How should focus/activity detection work for the gaming/browsing
   carve-out?** E.g. suppress or change behavior when a fullscreen
   exclusive app has focus, or a browser tab is focused and the spoken
   text looks like in-page content rather than a command.
5. **Local-only vs. selectively cloud-assisted?** Everything today is
   local (Whisper/Piper/openWakeWord/Ollama). If accuracy or capability
   becomes the bottleneck, is occasionally calling a cloud model
   acceptable, and under what privacy/latency constraints?

## 7. Bug-fix track record (for calibration)

19 issues found and fixed through live voice testing this cycle, spanning:
audio device routing, fuzzy-match false positives, STT punctuation/filler
handling, app/folder/window resolution, a thread-self-join shutdown
deadlock, File Explorer window reuse, and confirmation-message accuracy.
Full detail in `UX_LOG.md`. One item open (toast notifications); one
larger task never started (STT model benchmarking).
