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

## System checks
- [ ] Tray icon changes color for idle/listening/thinking/speaking/paused
- [ ] `Ctrl+Shift+M` toggles pause (icon turns gray, wake word ignored)
- [ ] `Ctrl+Shift+Q` speaks "MIMIR shutting down" and exits cleanly
- [ ] Tray "Open Transcript" shows recent conversation log
- [ ] Tray "Settings" lets you edit and save config.yaml values
- [ ] After `idle_unload_minutes` of inactivity, log shows Whisper/Piper unloaded
