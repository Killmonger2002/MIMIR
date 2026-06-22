# MIMIR model setup

MIMIR runs fully offline, but several model files must be downloaded
manually (once, with internet access) before first run.

## 0. Configuration

Copy `config.example.yaml` (at the repo root) to `config.yaml` and edit it
for your machine - in particular `audio.input_device_name` (run
`test_devices.py` to list your available input devices; leave it empty to
use the system default microphone).

## 1. Whisper (speech-to-text)

No action needed. `faster-whisper` automatically downloads and caches the
model specified by `stt.model_size` in `config.yaml` (default `tiny.en`)
on first run. The cache lives under `%USERPROFILE%\.cache\huggingface`.

## 2. Piper (text-to-speech)

Download a Piper voice (`.onnx` model + matching `.onnx.json` config) from
the Piper voices repository and place both files somewhere on disk, e.g.:

```
models/piper/voice.onnx
models/piper/voice.onnx.json
```

Update `tts.voice_model_path` and `tts.voice_config_path` in `config.yaml`
to point at these files.

## 3. openWakeWord (wake word detection)

Download the pre-trained openWakeWord model file(s) (and, if using a
custom wake phrase, a custom-trained model) and place them under:

```
models/wake_word/
```

Update `wake_word.model_path` in `config.yaml` if you place the files
elsewhere or use a custom model filename.

## 4. Ollama + phi3:mini (intent classification fallback)

Ollama must be installed separately (not managed by MIMIR). Once
installed, pull the model with internet access:

```
ollama pull phi3:mini
```

MIMIR assumes the Ollama service is reachable at its default local
address and will call it only as a fallback when the regex intent
classifier doesn't match.
