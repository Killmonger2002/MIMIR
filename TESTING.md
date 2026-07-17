# MIMIR Phase 1 manual test checklist

Before running through this list, complete the setup steps in
`models/README.md` and run `ollama pull phi3:mini`.

Start MIMIR (`launcher.bat` or `python main.py`), wait for "MIMIR at your
service", then say "hey mimir" before each phrase below and confirm the
spoken response and resulting action match expectations.

## app_executor
- [ ] "open chrome"
- [ ] "launch spotify"
- [ ] "start notepad"
- [ ] "open vs code" / "open vscode" (explicit alias - Code.exe's real name
      doesn't fuzzy-match "vs code" at all)
- [ ] A garbled/typo'd common app name (e.g. "open microsoft excent")
      resolves to the right app via the alias-phrase fuzzy stage, not a
      random Program Files internal - see
      executors/app_executor.py::_resolve_app (2026-07-17 fix: switched
      from thefuzz's default WRatio to length-aware fuzz.ratio after it
      was observed live scoring 2-3 letter junk executables ~90 against
      unrelated queries, and once launched an obscure MSBuild internal
      tool from "open microsoft xn")

## file_executor
- [ ] "open downloads folder"
- [ ] "find my resume"
- [ ] "open desktop"

## volume_executor
- [ ] "volume 40"
- [ ] "mute"
- [ ] "unmute"
- [ ] "turn it up"

## wifi_executor
- [ ] "connect to homewifi"
- [ ] "turn off wifi"
- [ ] "disconnect wifi"
- [ ] A partial/garbled network name (e.g. saying only half an SSID) now
      asks "Did you mean X?" before connecting instead of silently
      connecting to a weak match (2026-07-17: was the only fuzzy-match
      executor with real side effects and no confirm tier at all)

## bluetooth_executor
- [ ] "connect my headphones"
- [ ] "turn on bluetooth"
- [ ] "pair my speaker"

## printer_executor
- [ ] "print this"
- [ ] "i want to print this pdf"
- [ ] "show printers"

## window_executor
- [ ] "minimise this"
- [ ] "switch to chrome"
- [ ] "close this window"
- [ ] "lock the screen"

## media_executor
- [ ] "play"
- [ ] "pause"
- [ ] "next song"
- [ ] "skip"
- [ ] "previous track"

## sysinfo_executor
- [ ] "how much battery"
- [ ] "cpu usage"
- [ ] "how much ram"
- [ ] "disk space"

## typing_executor
- [ ] "type hello world"
- [ ] "write dear sir"
- [ ] "dictate hello world" (one-shot - must NOT enter dictation mode)

## dictation_executor (continuous dictation mode - v1, 2026-07-17)
Focus a text field first (Notepad, a browser textbox, Word), then:
- [ ] "start dictation" (also: "take dictation", "dictation mode") - MIMIR
      says "Dictation on..." and the tray icon / transcript bar show
      "Dictating" (orange)
- [ ] Speak normally - words appear in the focused field with Whisper's
      own punctuation and capitalization; no wake word needed between
      sentences
- [ ] Consecutive sentences are space-joined (say "Hello there." pause
      "How are you?" -> "Hello there. How are you?")
- [ ] "new line" inserts one line break; "new paragraph" inserts two
- [ ] "scratch that" / "delete that" removes the last dictated chunk
- [ ] "undo that" / "undo" sends Ctrl+Z (leans on the app's own undo)
- [ ] "stop dictation" / "end dictation" / "I'm done dictating" ends the
      session (MIMIR says "Dictation off")
- [ ] Pause hotkey (Ctrl+Shift+M) OR tray "Stop Dictation" ends it even if
      the spoken stop phrase isn't recognized (the escape hatch)
- [ ] Content words that look like commands stay literal: "the Jurassic
      period", "stop the car" are TYPED, not treated as commands (only a
      whole-utterance exact match like just "stop dictation" is a command)
- [ ] Known v1 limitation: pausing mid-sentence can leave an extra
      capital/period at the boundary (Whisper treats each chunk as a full
      sentence) - the v2 LLM-cleanup pass is meant to smooth this
- [ ] Known v1 limitation: types into whatever is focused - if focus is
      wrong, text goes to the wrong place (position the cursor first)

## ui_executor (semantic on-screen control)
Open a real app/dialog first (e.g. a browser, Settings, or a Save dialog),
then say the wake word before each:
- [ ] "click <a visible button name>" (e.g. "click Cancel")
- [ ] "type hello in the <a visible field name>" (e.g. "...in the address bar")
- [ ] "check <a visible checkbox name>"
- [ ] "select <option> from <a visible dropdown name>"
- [ ] "search for python tutorials" (LLM multi-step: type then click Search)
- [ ] Ambiguity: on a page with a Search field AND a Search button,
      "click search" hits the button, "type X in search" hits the field
- [ ] On an app with no readable UI: MIMIR says it can't read the screen
      (rather than crashing)

## Number overlay (Voice Access "show numbers" parity)
- [ ] "show numbers" - numbered badges appear over on-screen elements,
      MIMIR listens for a number without needing the wake word again
- [ ] Say a number (e.g. "5" or "click 5") - clicks that element, overlay
      disappears
- [ ] Number words work too ("five", "twenty three")
- [ ] "hide numbers" dismisses the overlay
- [ ] Badges roughly align with their elements (report if off - likely a
      display-scaling issue to tune)
- [ ] After using UI control, a follow-up "click <that app's label>"
      transcribes the label more reliably (context-biased STT)
- [ ] "click on 9" (with "on") selects the numbered element, same as
      "click 9" - 2026-07-17 fix: _parse_number() only stripped "click "
      before, so "click on 9" fell through to name-based matching (a
      search for an element literally named "9") and always failed
- [ ] "put labels on the screen" / "put numbers on the screen" triggers
      the overlay, same as "show ..."
- [ ] "height numbers" also hides the overlay (STT mishearing of "hide")
- [ ] "what on-screen commands can you perform" gives a spoken summary of
      click/type/check/select/show-numbers, instead of "I couldn't find
      that on the screen" (was being treated as a failed action attempt)

### Whole-screen / multi-monitor scan (2026-07-16)
"show numbers" scans every visible top-level window, not just the
foreground one - confirm on a real multi-window, multi-monitor desktop:
- [ ] Badges appear on desktop icons, not just the focused app's window
- [ ] Badges appear on a SECOND monitor, correctly positioned (not
      clipped/missing, not offset onto the wrong monitor)
- [ ] With several windows + a full desktop open, some desktop icons are
      still numbered (guaranteed a slice of the element budget - don't
      expect literally everything with 30+ desktop icons and several
      heavy app windows open at once, there's a hard cap)
- [ ] "show numbers" latency is noticeable but reasonable (roughly 1-3s
      depending on what's open, dominated by whatever the foreground
      window's own UIA tree size is - a heavy Electron/browser window in
      the foreground is the main driver, not the number of other windows)
- [ ] "click <N>" for an N that's actually numbered works even if it
      belongs to a non-foreground window or the desktop

## model_executor (LLM tier switching)
- [ ] "which model are you using" (reports the basic model at startup)
- [ ] "get smarter" (switches up one tier)
- [ ] "switch to the basic model" (returns to tier 1)

## Settings window
- [ ] Input device dropdown lists real devices (test_devices.py output should
      match) and switching devices takes effect immediately, no restart
- [ ] LLM tier and speech-recognition model dropdowns apply immediately
      (STT change: say a command right after switching, confirm no crash
      and no stale-model behavior)
- [ ] "Run Audio Calibration" opens the wizard at step 1
- [ ] "Show live transcript bar" checkbox shows/hides the bar immediately

## Audio calibration wizard (Settings -> Run Audio Calibration)
- [ ] Step 1 (Mic level): input level bar rises when you speak, noise
      floor bar reflects ambient sound, device dropdown switches devices
      live, status lines react to level/noise thresholds
- [ ] Step 2 (Noise): "Start Capture" records 3s of silence and reports a
      VAD threshold; Continue is disabled until captured at least once
- [ ] Step 3 (Wake word): records the full sample set, box grid fills in
      with checkmarks, trains automatically after the last sample, "Test
      Live" and "Activate" work; Continue disabled until training completes
- [ ] Step 4 (Voice profile): records each enrollment phrase, saves the
      profile, "Test My Voice" reports a similarity score; Save & finish
      disabled until enrollment completes
- [ ] Back/Continue navigate correctly and don't crash if you leave a step
      mid-recording

## Live transcript bar (2026-07-17 rebuild, thinned 2026-07-17)
- [ ] Tray menu "Show Live Transcript" / "Hide Live Transcript" toggles a
      bar docked at the top of the screen
- [ ] It's thin - about half the height of the taskbar (26px vs the
      taskbar's 48px), a single row, not the earlier 3-row layout.
      Confirm nothing is clipped/unreadable at this height on your
      actual display scaling
- [ ] Real docking, not just floating: maximize any other window while
      the bar is showing - it should NOT render into the bar's strip
      (confirmed programmatically: SPI_GETWORKAREA's top goes from 0 to
      ~26 while docked, back to 0 when hidden - re-verified after the
      height change). If a window still overlaps it, app-bar
      registration silently failed on this machine and it fell back to
      a plain always-on-top window - check the log for
      "SHAppBarMessage(ABM_NEW) failed"
- [ ] Switching focus between other windows never covers the bar (it
      re-asserts topmost on a timer)
- [ ] Every spoken/heard line appears live, one at a time, as the
      conversation happens - the "Yes?" listening prompt, confirmation
      questions ("Did you mean X?"), your spoken yes/no reply, and any
      utterance MIMIR failed to understand, not just one entry per
      finished command. NOTE: at this height only the SINGLE most recent
      line is shown (replaced by the next one), not a scrollback feed -
      full history is still in the Transcript window/state.get_captions()
- [ ] The mode word (Idle/Listening/Thinking/Speaking) updates live
- [ ] Icon-only toolbar (▶ Listen Now, ⚙ Settings, 🎤 mic pause/resume -
      turns red when paused, 🎧 device picker menu, plus the level meter)
      all work with no text labels - hover/click each to confirm they're
      discoverable enough without labels
- [ ] Its "✕" closes it and unchecks the tray toggle; the on/off choice
      persists across restarts
- [ ] Quitting MIMIR (Ctrl+Shift+Q or "quit mimir") releases the docked
      space - check SPI_GETWORKAREA is back to normal after MIMIR exits,
      not just that the bar disappears

## Vocabulary hint / STT accuracy (2026-07-17 fix)
- [ ] Say "open Microsoft Excel" a few times - should no longer mishear
      as "XN"/"XL"/"Boba Nixon"/"Ready?" (real examples from
      %LOCALAPPDATA%\MIMIR\logs\mimir.log before this fix). Root cause
      was core/vocabulary.py feeding Whisper's initial_prompt an
      unfiltered slice of the ~2000-entry app index (dominated by
      Program Files internals, not real app names - "excel" itself
      never made it into the hint at all); fixed to use app_executor's
      curated alias list instead. Verified with a controlled synthesized-
      audio A/B test at multiple noise levels before shipping.
- [ ] Other common apps (word, powerpoint, outlook, edge, notepad,
      calculator, explorer, vs code, etc.) should also transcribe more
      reliably now that they're actually in the vocabulary hint

## Speaker verification / confirmation replies (2026-07-17 fix)
- [ ] With a voice profile enrolled, say "goodbye" / "shut down mimir",
      then answer "yes" to "That will shut down MIMIR. Are you sure?" -
      it must actually shut down, NOT say "Okay, cancelled". (Root cause:
      speaker verification was dropping the "yes" clip as not-matching-
      the-profile, so the reply parsed as empty -> no. Confirmation
      replies now bypass speaker verification entirely.)
- [ ] General: with a profile enrolled, normal commands should not
      silently fail more often than without one. If they do, the profile
      is too strict for your mic/room - the filter now fails OPEN (keeps
      the clip and warns in the log: "keeping the clip anyway (fail-
      open)") instead of dropping it, but you may still want to re-enroll
      or lower speaker_verification.similarity_threshold (0.75 default was
      observed rejecting the enrolled user's own voice repeatedly)
- [ ] Check the log for "keeping the clip anyway (fail-open)" frequency -
      if it's on nearly every command, speaker verification needs
      re-enrollment or a lower threshold on this machine

## System checks
- [ ] Tray icon changes color for idle/listening/thinking/speaking/paused
- [ ] `Ctrl+Shift+M` toggles pause (icon turns gray, wake word ignored)
- [ ] `Ctrl+Shift+Q` speaks "MIMIR shutting down" and exits cleanly
- [ ] Tray "Open Transcript" shows recent conversation log
- [ ] Tray "Settings" lets you edit and save config.yaml values
- [ ] After `idle_unload_minutes` of inactivity, log shows Whisper/Piper unloaded
