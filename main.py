"""MIMIR entry point.

Wires together wake-word detection, STT, intent routing, executors, TTS,
the system tray, global hotkeys, and the lifecycle manager.
"""

from __future__ import annotations

import importlib
import logging
import logging.handlers
import os
import sys
import threading
import time

import win32api
import win32event
import winerror

from config import config
from core import confirmer, sound_cues, stt, tts, wake_word
from core.intent_router import classify
from executors.base import ExecutorResult
from state import AppState
from system.hotkey import HotkeyManager
from system.lifecycle import LifecycleManager
from system.tray_icon import TrayIcon
from ui.settings_window import open_settings_window
from ui.transcript_bar import is_showing as _transcript_bar_showing
from ui.transcript_bar import set_enabled as _set_transcript_bar_enabled
from ui.transcript_window import open_transcript_window

LOG_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "MIMIR", "logs")
LOG_FILE = os.path.join(LOG_DIR, "mimir.log")

_SINGLE_INSTANCE_MUTEX_NAME = "Global\\MIMIR_SingleInstanceMutex"


def _acquire_single_instance_lock():
    """Return a mutex handle if this is the only running instance, else None."""
    mutex = win32event.CreateMutex(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
    if winerror.ERROR_ALREADY_EXISTS == win32api.GetLastError():
        return None
    return mutex


def _setup_logging() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)

    level_name = config.logging.level.upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE,
        maxBytes=config.logging.max_bytes,
        backupCount=config.logging.backup_count,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(handler)


logger = logging.getLogger("mimir.main")


class Mimir:
    """Top-level orchestrator wiring all MIMIR subsystems together."""

    def __init__(self) -> None:
        self.state = AppState()
        self.lifecycle = LifecycleManager(self.state)
        self.hotkeys = HotkeyManager(self.state, on_quit=self.shutdown)
        self.wake_word_listener = wake_word.WakeWordListener(self.state, on_detected=self._on_wake_word)
        self.tray: TrayIcon | None = None
        # Guards against the wake-word listener and the manual "Listen Now"
        # tray button both starting a command cycle at once - they run on
        # different threads (wake-word's own thread vs. pystray's), unlike
        # a normal wake-word cycle where the listener thread is simply
        # blocked for the duration and can't re-trigger itself.
        self._command_cycle_lock = threading.Lock()

    def _on_wake_word(self) -> None:
        """Callback fired by the wake-word listener; runs the command loop."""
        if not self._command_cycle_lock.acquire(blocking=False):
            logger.info("Wake word detected while already handling a command; ignoring")
            return
        try:
            self._handle_command_cycle()
        except Exception:
            logger.exception("Unexpected error in command handling")
            tts.speak("Sorry, something went wrong with that.", self.state)
            self.state.set_mode("idle")
        finally:
            self._command_cycle_lock.release()

    def _on_toggle_transcript_bar(self) -> None:
        """Callback fired by the tray's Show/Hide Live Transcript item."""
        _set_transcript_bar_enabled(self.state, not _transcript_bar_showing())

    def _on_listen_now(self) -> None:
        """Callback fired by the tray's "Listen Now" button - a manual
        fallback for when the wake word doesn't fire. Runs the exact same
        command cycle a spoken wake word would, on its own thread so the
        tray/pystray callback isn't blocked for the whole interaction."""
        if not self._command_cycle_lock.acquire(blocking=False):
            logger.info("Listen Now clicked while already handling a command; ignoring")
            return

        def _run() -> None:
            try:
                self._handle_command_cycle()
            except Exception:
                logger.exception("Unexpected error in command handling")
                tts.speak("Sorry, something went wrong with that.", self.state)
                self.state.set_mode("idle")
            finally:
                self._command_cycle_lock.release()

        threading.Thread(target=_run, name="listen-now", daemon=True).start()

    def _handle_command_cycle(self) -> None:
        """Listen, transcribe, route, execute, and respond - looping on
        followups and, once a command has been answered, a brief bounded
        window where the user can speak again without repeating the wake
        word."""
        first_loop = True
        # None = wait indefinitely for speech (the normal case). Set to a
        # bounded value only when entering the soft follow-up window below.
        listen_timeout: float | None = None

        while True:
            if first_loop:
                if config.audio.listening_cue_style == "voice":
                    # Slower than the chime, but unmistakable - for users
                    # who find a tone too easy to miss.
                    tts.speak("Yes?", self.state, allow_interrupt=False)
                else:
                    sound_cues.play_listening_cue()
                first_loop = False

            self.state.set_mode("listening")
            audio = stt.record_until_silence(max_wait_sec=listen_timeout)
            was_followup_window = listen_timeout is not None
            listen_timeout = None

            if was_followup_window and not stt.contains_speech(audio):
                # Follow-up window expired with nothing said - go idle
                # quietly, no re-prompt needed.
                self.state.set_mode("idle")
                return

            self.state.set_mode("thinking")
            transcript, confidence = stt.transcribe_with_confidence(audio)

            if not transcript:
                self.state.set_mode("idle")
                return

            if confidence < config.confirmation.stt_logprob_threshold:
                logger.info("Low STT confidence (%.2f) for %r, confirming", confidence, transcript)
                verdict, reply = confirmer.confirm_with_reply(
                    f"I'm not sure I heard that right. Did you say: {transcript}?", self.state
                )
                if verdict is False or (verdict is None and not reply.strip()):
                    tts.speak("Okay, tell me again.", self.state)
                    continue  # re-listen for the corrected command
                if verdict is None:
                    # Reply wasn't yes/no but was a real phrase - the user
                    # most likely just restated what they meant rather
                    # than literally answering the question. Use it as
                    # the actual command instead of discarding it and
                    # asking a third time (this exact pattern caused a
                    # real user-frustration cascade that ended in an
                    # unwanted shutdown - see UX_LOG for the trace).
                    logger.info("Confirmation reply %r wasn't yes/no; using it as the command instead", reply)
                    transcript = reply
                # else verdict is True: proceed with the original transcript, now trusted

            executor_name = classify(transcript)

            if executor_name == "unknown":
                result_speak = "I didn't understand that command."
                self.state.add_log_entry(transcript, result_speak, executor_name)
                if tts.speak(result_speak, self.state):
                    listen_timeout = None  # interrupted - go straight into listening again
                    continue
                self.state.set_mode("idle")
                return

            try:
                executor_module = importlib.import_module(f"executors.{executor_name}")
                result = executor_module.execute(transcript, self.state)
                result = self._resolve_confirmation(result)
            except Exception:
                logger.exception("Executor %s raised an exception", executor_name)
                result = type(
                    "Result",
                    (),
                    {
                        "success": False,
                        "speak": "Sorry, something went wrong with that.",
                        "needs_followup": False,
                        "shutdown": False,
                    },
                )()

            self.state.add_log_entry(transcript, result.speak, executor_name)

            was_interrupted = tts.speak(result.speak, self.state)

            if getattr(result, "shutdown", False):
                self.shutdown()
                return

            if was_interrupted:
                listen_timeout = None  # unbounded - the user is already talking
                continue

            if result.needs_followup:
                continue  # unbounded - the executor expects a definite reply

            if config.followup.enabled:
                listen_timeout = config.followup.window_sec
                continue  # bounded window - loop back to listen briefly

            self.state.set_mode("idle")
            return

    def _resolve_confirmation(self, result) -> ExecutorResult:
        """Ask any pending yes/no question on an executor result and run the
        deferred action on yes; cancel on no/silence. Loops in case an
        on_confirm action itself asks a follow-up question (capped so a
        buggy executor can't trap the user in an endless interrogation)."""
        for _ in range(3):
            question = getattr(result, "confirm", None)
            if not question:
                return result
            if not confirmer.confirm(question, self.state):
                return ExecutorResult(success=True, speak="Okay, cancelled.")
            on_confirm = getattr(result, "on_confirm", None)
            if on_confirm is None:
                return result
            result = on_confirm()
        return result

    def shutdown(self) -> None:
        """Run the full shutdown sequence: announce, stop threads, exit."""
        logger.info("Shutdown requested")
        self.state.set_mode("shutting_down")
        tts.speak("MIMIR shutting down", self.state, allow_interrupt=False)

        # Run the actual stop/join sequence on a dedicated thread. shutdown()
        # can be invoked from the wake-word listener's own thread (voice
        # "shut down" command), and WakeWordListener.stop()/thread.join()
        # raises "cannot join current thread" if called from that thread.
        threading.Thread(target=self._shutdown_worker, name="shutdown-worker", daemon=True).start()

    def _shutdown_worker(self) -> None:
        self.wake_word_listener.stop()
        self.hotkeys.stop()
        self.lifecycle.shutdown()

        if self.tray is not None:
            self.tray.stop()

        time.sleep(0.5)
        os._exit(0)

    def _prewarm(self) -> None:
        """Load slow-to-initialize resources on background threads at
        startup - the Whisper model and the installed-app index - so the
        first spoken command doesn't stall on a multi-second model load
        or a Program Files walk that would otherwise happen lazily on
        first use. Runs concurrently with the startup TTS greeting below,
        which loads Piper as a side effect on the main thread."""
        threading.Thread(target=stt.get_model, name="prewarm-stt", daemon=True).start()

        def _prewarm_app_index() -> None:
            from executors.app_executor import _get_index

            _get_index()

        threading.Thread(target=_prewarm_app_index, name="prewarm-appindex", daemon=True).start()

        def _check_llm() -> None:
            # Detect/start Ollama once at startup, and say so plainly when
            # the LLM tiers are unavailable - the silent per-request
            # timeouts previously hid a never-installed Ollama for weeks.
            from core.llm_runtime import get_status, warm_up

            status = get_status()
            if not status["server"]:
                logger.warning(
                    "LLM tiers unavailable (Ollama not installed or not startable) - "
                    "running regex-only. See models/README.md."
                )
                return
            for tier, info in status["tiers"].items():
                usable = info["ram_ok"] and info["pulled"]
                logger.info(
                    "LLM tier %s (%s): %s",
                    tier,
                    info["model"],
                    "ready" if usable else ("not pulled" if info["ram_ok"] else "RAM-gated off"),
                )
            # Load tier 1 into RAM now (it stays warm via keep_alive) so
            # the first misrouted command doesn't pay the disk-load cost.
            warm_up(tier=1)

        threading.Thread(target=_check_llm, name="prewarm-llm", daemon=True).start()

    def run(self) -> None:
        """Start all subsystems and run the tray icon on the main thread."""
        _setup_logging()
        logger.info("MIMIR starting up")

        self.state.set_mode("idle")
        self._prewarm()
        tts.speak("MIMIR at your service", self.state, allow_interrupt=False)

        if config.ui.transcript_bar_enabled:
            _set_transcript_bar_enabled(self.state, True, persist=False)

        self.wake_word_listener.start()
        self.hotkeys.start()
        self.lifecycle.start()

        self.tray = TrayIcon(
            self.state,
            on_open_transcript=lambda: open_transcript_window(self.state),
            on_open_settings=lambda: open_settings_window(),
            on_listen_now=self._on_listen_now,
            on_quit=self.shutdown,
            on_toggle_transcript_bar=self._on_toggle_transcript_bar,
        )
        self.tray.run()


if __name__ == "__main__":
    _lock = _acquire_single_instance_lock()
    if _lock is None:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, "MIMIR is already running.", "MIMIR", 0x40)
        sys.exit(1)
    Mimir().run()
