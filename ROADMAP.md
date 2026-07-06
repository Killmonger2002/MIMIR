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
- [ ] First-run wizard: mic check → voice enrollment → wake-word
      training → guided model pull sized to the machine's RAM → three
      practice commands
- [ ] Settings UI covers 100% of config; YAML never required
- [ ] Start on login, crash watchdog, update checker
- [ ] Privacy proof: tray network indicator showing zero outbound
      traffic during normal use (searches visibly excepted)
- [ ] Later: embedded llama-cpp-python replaces bundled Ollama → one
      process, one install

**Exit:** a non-technical person installs and completes three commands
in under 10 minutes, unassisted.

## Phase 4 — Screen interaction (mouse replacement)

- [ ] UIA "click [button name]" on the foreground window
- [ ] UIA dialog/screen reading aloud
- [ ] Numbered-grid overlay fallback for apps with poor accessibility
      trees
- [ ] Form filling by field name
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
