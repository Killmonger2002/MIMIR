# MIMIR Product Roadmap

North star (the founder's vision, verbatim in spirit): let a person be
*around* their PC — sitting, lying down, pacing, working out — and still
perform complex tasks by voice: open Netflix and play a specific movie,
open email and draft a reply, summarize a webpage, fix spelling and tone,
organize folders, clean the disk, set reminders, catch the app eating all
the RAM, and hold a fact-grounded conversation — all with AI that runs on
the user's own machine. Web access for *information* is fine; the
intelligence itself never lives in a datacenter.

Product bar: **the user installs one thing and begins.** No git, no
Python, no YAML, no separate model apps.

---

## Locked architecture decisions

- **Big Brother Protocol (BUILT, 2026-07-06):** three tiers of local
  model in `core/llm_runtime.py`, all one family — Tier 1 `qwen2.5:1.5b`
  (routing, kept warm; benchmarked ABOVE the larger phi3:mini on routing
  accuracy at half the RAM — see benchmark_llm_tier1.py), Tier 2
  `qwen2.5:7b` (drafting/slots/tool-calling workhorse; tool calling
  verified live decomposing a compound command), Tier 3 `qwen2.5:14b`
  (long documents, debates, planning; loads on demand, never resident). RAM-gated per machine (Tier 2 ≥11GB,
  Tier 3 ≥15GB), always falls back down the ladder, works regex-only
  with no LLM at all. Qwen2.5 chosen for reliable tool calling at 7B/14B,
  Apache-2.0 license, and multilingual headroom. **Escalation is
  user-controlled, never automatic:** every session starts on Tier 1
  (least resources); the user raises it by voice ("get smarter" /
  "switch to the smartest model" — executors/model_executor.py) or the
  Settings selector, and the choice is never persisted across restarts.
- **Inference backend strategy:** Ollama now (it owns model lifecycle +
  tool-call templating for us) → bundled *silently* inside the Phase 3
  installer (MIT license permits; user never sees it; MIMIR already
  auto-starts and manages it) → eventually embedded `llama-cpp-python`
  so MIMIR is a single process. All LLM access already goes through
  `core/llm_runtime.py` alone, so the backend swap touches one file.
- **Vision layer:** deferred. UIA (Windows accessibility tree) covers
  most "act on what's on screen" needs at near-zero compute; a local VLM
  comes only where UIA can't see, and only after everything else works.

## Phase 0 — Reliability floor

Live-measured task success was ~75% (2026-07-06); the practical
abandonment threshold is ~90-95%.

- [x] Ollama lifecycle: detect / auto-start / plain-spoken degradation
- [x] Filler-prefix + presence-check fixes from live-transcript failures
- [ ] Status queries for everything settable ("what's the volume /
      brightness / wifi status")
- [ ] Helpful rejection: "I heard 'X' — did you mean brightness?" (use
      the router's runner-up signal)
- [ ] Run benchmark_stt.py; move to base.en if latency allows
- [ ] End-to-end latency logging (wake→action) per command

**Exit:** ≥90% success on a scripted 50-command checklist.

## Phase 1 — The brain (agent loop + knowledge)

Turns MIMIR from a command system into an agent: goal → plan → tool
calls → verify. Prerequisite for almost everything in the vision.

- [ ] Agent loop on `llm_runtime.chat()`: executors exposed as tools,
      max-steps cap, existing confirmation gates on destructive tools
- [ ] Conversation mode with history ("hey mimir, let's talk") — Tier
      2/3, streaming sentence-pipelined TTS (already built), barge-in
      (already built)
- [ ] Web RAG: `ddgs` search + `trafilatura` extraction → cited context
      (searches go out; the model stays local)
- [ ] "Summarize this page": active browser tab URL → fetch → Tier 2
- [ ] Escalation rules: category → tier mapping (never model-judged)

**Exit:** "find out who won the match yesterday and tell me" works,
cited, fully on-device inference.

## Phase 2 — Daily-driver capabilities (the vision list)

Each is an executor/tool once Phase 1 lands. Rough order of frequency
of use:

- [ ] Dictation mode: "start dictation" → keystrokes until "stop", with
      punctuation commands; spell/tone fix on selected text via Tier 2
- [ ] Email (Outlook COM): read inbox aloud, draft/reply by voice
- [ ] Reminders + calendar: local store + toasts; Outlook calendar COM
- [ ] Word COM: draft documents, formatting operations (the resume case)
- [ ] PowerPoint COM: LLM outline → slides
- [ ] Wallpaper change (trivial ctypes)
- [ ] Disk cleanup: temp/cache scan → confirm-gated delete
- [ ] Folder organizer: LLM classifies filenames → confirm-gated moves
- [ ] RAM watchdog: psutil baseline → "chrome is eating 6GB, kill it?"
- [ ] Browser recipes (Playwright): "open netflix and play X",
      YouTube search-and-play

**Exit:** a full workday where email triage, one document, and all
media control happen by voice.

## Phase 3 — Packaging ("install the AI and just begin")

- [ ] One installer (PyInstaller + Inno Setup): Python runtime, Piper
      voice, wake-word models, **and Ollama bundled silently**
- [x] Calibration wizard (2026-07-16, ui/audio_calibration_window.py):
      mic level + noise floor metering → VAD threshold capture → wake-word
      training → voice enrollment, one flow, dark-themed to match the rest
      of the UI (ui/theme.py). Launched from Settings, not yet auto-run on
      first launch. Still missing to fully close this roadmap item: an
      actual first-run trigger, guided model pull sized to RAM, and three
      practice commands at the end.
- [x] Settings UI: real input-device dropdown (core/audio_device.py
      list_input_devices(), switches live via reset_cache() - no more
      typing a device name substring by hand) and model selection (LLM
      tier + STT model size, both apply immediately) (2026-07-16).
      Non-config-editor rows (hotkeys, TTS speed, etc.) still exist -
      "100% of config, YAML never required" isn't fully closed yet.
- [x] Live transcript bar (2026-07-16, ui/transcript_bar.py): small
      always-on-top HUD showing the current mode and latest exchange,
      toggled from the tray or Settings, off by default, choice persists.
- [ ] Start on login, crash watchdog, update checker
- [ ] Privacy proof: tray network indicator showing zero outbound
      traffic during normal use (searches visibly excepted)
- [ ] Later: embedded llama-cpp-python replaces bundled Ollama → one
      process, one install

**Exit:** a non-technical person installs and completes three commands
in under 10 minutes, unassisted.

## Phase 4 — Screen interaction (mouse replacement)

- [x] UIA "click [button name]" on the foreground window
      (core/ui_scanner + element_matcher + action_executor +
      executors/ui_executor; verified live clicking/typing on real
      Windows dialogs, 2026-07-14). Windows Voice Access parity for the
      core "click <name>" / "type <text> in <field>" case, driven by
      MIMIR's own STT/routing and the tiered LLM for hard matches.
- [x] Form filling by field name ("type X in the username field",
      "check remember me", "select X from the dropdown")
- [x] Numbered overlay ("show numbers" → "click 5") — the signature
      Voice Access feature (core/ui_overlay.py: transparent, topmost,
      click-through; verified end-to-end, 2026-07-15). Doubles as an
      audio-understanding win: recognizing a digit beats recognizing an
      arbitrary label. Number-word parsing included ("five", "twenty
      three"). Whole-screen + multi-monitor scan (2026-07-16): originally
      scoped to only the foreground window, so it missed desktop icons,
      other open windows, and anything on a second monitor - "click 6"
      failing was a direct downstream symptom (the element just didn't
      exist in a 4-element single-window scan). core/ui_scanner.py now
      walks every visible top-level window (confirmed live to already
      span all monitors via UIA) with a foreground-priority + per-window
      element budget so one huge window can't starve everything else, and
      core/ui_overlay.py now sizes itself to the full virtual desktop
      (GetSystemMetrics SM_*VIRTUALSCREEN) instead of the primary monitor
      only. Known trade-off, not fixed: latency scales with what's open
      (measured 1-3s live, dominated by the foreground window's own UIA
      tree size) and mixed-DPI multi-monitor alignment isn't solved (needs
      process-wide per-monitor DPI awareness, a bigger cross-cutting
      change - see core/ui_overlay.py's docstring).
- [x] Context-biased STT for UI control: on-screen element names lead
      Whisper's initial_prompt after a scan (core/vocabulary.py), so the
      NEXT command's labels transcribe more reliably.
- [ ] UIA dialog/screen reading aloud ("what's on screen", "read this
      dialog") — scanner already extracts the text; needs a read-out
      executor path
- [ ] Grid overlay (pixel regions) for elements with no UIA node at all
      (rect data already captured on each UIElement; the numbered overlay
      covers the has-a-node case)
- [ ] Playwright browser path as an optional enhancement (UIA already
      covers browsers; only add if a site's UIA tree proves inadequate)
- [ ] Only then: local VLM for what UIA can't see

**Exit:** complete an unfamiliar installer dialog flow entirely by voice.

## Phase 5 — Ecosystem (the local-LLM impact)

- [ ] Plugin API: third-party executors as drop-in modules, documented
      contract, discovered at startup
- [ ] Multi-backend: Ollama / LM Studio / llama.cpp server behind
      `llm_runtime`
- [ ] Model pack manager in Settings (swap Whisper/Piper/LLM tiers)
- [ ] Published latency/accuracy benchmarks on real consumer hardware
- [ ] Multilingual STT (Hindi/Hinglish code-switching) — a differentiator
      no incumbent serves
- [ ] Architecture docs good enough that a stranger ships an executor in
      an afternoon

**Exit:** first community-contributed executor merged.

---

## Honest risks

- **Microsoft** will ship some of this natively (Copilot+ on-device).
  Defense: open source, verifiable privacy, extensibility, accessibility
  depth.
- **The 90% wall:** recognition accuracy is the product; Phase 0's exit
  criterion gates everything else.
- **Tier 2/3 latency on CPU-only machines:** drafting an email in 20s is
  acceptable; sluggish conversation is not. Mitigation: GPU offload when
  present, honest tier gating, streaming TTS to hide latency.
- **One-maintainer risk:** Phase 5 exists to make contribution cheap
  early.
