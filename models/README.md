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
model specified by `stt.model_size` in `config.yaml` (default `base.en`)
on first run. The cache lives under `%USERPROFILE%\.cache\huggingface`.
`base.en` is the recommended default (best accuracy-per-latency on CPU);
`tiny.en` is faster but noticeably less accurate, `small.en` is more
accurate but ~3x slower on CPU. Switchable live from Settings.

## 2. Piper (text-to-speech)

Download a Piper voice (`.onnx` model + matching `.onnx.json` config) from
the Piper voices repository (https://huggingface.co/rhasspy/piper-voices)
and place both files under `models/piper/`. MIMIR defaults to
`en_US-lessac-high` - one of only three English voices with a "high"
quality tier (the others are `en_US-libritts-high`, a multi-speaker model
needing a speaker ID, and `en_GB-cori-high`, British):

```
curl -L -o models/piper/en_US-lessac-high.onnx \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx
curl -L -o models/piper/en_US-lessac-high.onnx.json \
  https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/high/en_US-lessac-high.onnx.json
```

Update `tts.voice_model_path` and `tts.voice_config_path` in `config.yaml`
if you use a different voice or place the files elsewhere. Note: not every
speaker has a `high` tier - check the repo before assuming one exists
(e.g. the previously-used `en_US-amy` only goes up to `medium`).

## 3. openWakeWord (wake word detection)

Download the pre-trained openWakeWord model file(s) (and, if using a
custom wake phrase, a custom-trained model) and place them under:

```
models/wake_word/
```

Update `wake_word.model_path` in `config.yaml` if you place the files
elsewhere or use a custom model filename.

## 4. Ollama + the tiered models ("big brother protocol")

Install Ollama from https://ollama.com/download (MIMIR auto-starts its
server at launch, but cannot install it for you). Then pull the tier
models:

```
ollama pull qwen2.5:1.5b   # Tier 1 (~1GB):  command routing (kept warm)
ollama pull qwen2.5:7b     # Tier 2 (~4.7GB): drafting, slot extraction, tool calling
ollama pull qwen2.5:14b    # Tier 3 (~9GB):  long documents, complex reasoning
```

Tier 1 was chosen by measurement, not size: see `benchmark_llm_tier1.py`
to re-evaluate candidates if you want to try a different routing model.

Pull only the tiers your machine can handle - MIMIR checks total RAM and
what's actually pulled, and automatically uses the best available tier
(falling back down the ladder, or to regex-only with nothing at all):

- 8GB RAM: tier 1 only (skip the other pulls)
- 12GB RAM: tiers 1-2
- 16GB+ RAM: all three (tier 3 loads on demand and unloads after use)

Model names are configurable in `config.yaml` under `llm:`.
