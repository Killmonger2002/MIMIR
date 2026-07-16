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

## Live transcript bar
- [ ] Tray menu "Show Live Transcript" / "Hide Live Transcript" toggles a
      small always-on-top bar docked near the top of the screen
- [ ] The bar's status word changes with MIMIR's mode (Listening/Thinking/
      Speaking) and the latest exchange replaces the previous one as the
      conversation progresses
- [ ] Its "✕" closes it and unchecks the tray toggle; the on/off choice is
      remembered on the next launch

## System checks
- [ ] Tray icon changes color for idle/listening/thinking/speaking/paused
- [ ] `Ctrl+Shift+M` toggles pause (icon turns gray, wake word ignored)
- [ ] `Ctrl+Shift+Q` speaks "MIMIR shutting down" and exits cleanly
- [ ] Tray "Open Transcript" shows recent conversation log
- [ ] Tray "Settings" lets you edit and save config.yaml values
- [ ] After `idle_unload_minutes` of inactivity, log shows Whisper/Piper unloaded
