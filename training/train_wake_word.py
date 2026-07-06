"""Offline training pipeline for a custom "hey mimir" openWakeWord model,
personalized with real recordings of your own voice.

NOT part of the running app - never imported at runtime by core/wake_word.py
(which only ever uses inference_framework="onnx", a torch-free path).
Driven from the Settings window (ui/wake_word_training_window.py), which
collects your real "hey mimir" recordings and calls run_full_pipeline()
below on a background thread. Also runnable standalone:

    python -m training.train_wake_word

(standalone mode skips the real-recording step and trains on synthetic
positives only - see ui/wake_word_training_window.py for the personalized,
real-voice-recording path, which is the one actually worth using).

Produces models/wake_word/hey_mimir.onnx. config.yaml's wake_word.phrase/
model_path are deliberately left pointing at the existing "hey jarvis"
pretrained model until this one's offline negative-set evaluation AND a
live soak test both look at least as good as the current setup - the UI
wizard's "Activate" step is the explicit cutover gate.

Honesty note: this pipeline's synthetic positives (Piper TTS) and
synthetic hard negatives (phonetic adversarial text) are a rough,
optimistic signal for iterating locally - not a true FP-per-hour
guarantee, and nowhere near the scale of real negative audio the
upstream openWakeWord reference training notebook uses. Real recordings
of your own voice (heavily augmented - see augment_real_clips) are what
actually make this personalized; without them this just reproduces the
same generic-voice problem the bundled pretrained model already has.

Why the hard-negative/classifier logic below is vendored instead of
imported from openwakeword.train/openwakeword.data: those two modules
unconditionally import a chain of audio-augmentation libraries
(audiomentations, torch_audiomentations, speechbrain, torchaudio,
mutagen, acoustics) at module level for *other* functions this pipeline
never calls. One of those (acoustics) is incompatible with the scipy
version already in use here (it calls scipy.special.sph_harm, removed
upstream). Importing openwakeword.train would also pull in
openwakeword.data's OOV phonemizer path for "mimir" (not in CMUdict),
which downloads a DeepPhonemizer model from S3. None of that machinery
is needed for what this script actually does - phoneme-based text
permutation (pronouncing only) and a small DNN classifier (plain torch)
- so it's reimplemented directly below instead. Only `torch` and `onnx`
are required (both already permanent runtime dependencies, via
resemblyzer's speaker verification and this pipeline's ONNX export).

Pipeline stages:
    Stage A (no torch needed - uses the same onnxruntime path the live
             app uses): synthesize/augment positive clips, extract
             embeddings.
    Stage B (torch needed, training-time only): generate hard negatives,
             train the classifier, export to ONNX, evaluate.
"""

from __future__ import annotations

import copy
import itertools
import logging
import re
from pathlib import Path
from typing import Callable

import numpy as np
import pronouncing

logger = logging.getLogger("mimir.train_wake_word")

WAKE_PHRASE = "hey mimir"
CLIP_SECONDS = 2.0
SAMPLE_RATE = 16000
CLIP_SAMPLES = int(CLIP_SECONDS * SAMPLE_RATE)

OUTPUT_MODEL_PATH = Path("models/wake_word/hey_mimir.onnx")

# How many real "hey mimir" recordings the UI wizard asks for. Asking for
# hundreds (like the synthetic positive count) isn't reasonable for a
# live person to say repeatedly; this many, heavily augmented (see
# augment_real_clips), gives a real-voice-anchored positive set without
# an unreasonable ask.
RECOMMENDED_REAL_SAMPLES = 15

# pronouncing/CMUdict has no entry for "mimir" (a Norse mythology name).
# ARPAbet phones for the common anglicized pronunciation /'mi:mIr/ ("MEE-mihr").
_OOV_PHONEME_OVERRIDES = {
    "mimir": "M IY1 M IH0 R",
}

# Generic short commands/fillers, used alongside phonetic adversarial
# texts as additional (non-phonetically-targeted) hard negatives - things
# a user might plausibly say that should NOT trigger the wake word.
_GENERIC_NEGATIVE_PHRASES = [
    "open chrome", "close this window", "what time is it", "turn up the volume",
    "hey jarvis", "hey google", "ok google", "alexa", "play some music",
    "thank you very much", "good morning", "how are you today", "stop",
    "go back", "switch to notepad", "lock the screen", "shut down",
]


def _resample_to_16k(audio: np.ndarray, source_rate: int) -> np.ndarray:
    from scipy import signal

    if source_rate == SAMPLE_RATE:
        return audio
    n_target = int(len(audio) * SAMPLE_RATE / source_rate)
    return signal.resample(audio, n_target).astype(np.int16)


def fit_to_clip_size(audio: np.ndarray, clip_samples: int = CLIP_SAMPLES) -> np.ndarray:
    """Pad with zeros or truncate to exactly clip_samples - the wake-word
    classifier expects a fixed-size input, unlike the VAD-based variable-
    length recording MIMIR's regular command path uses."""
    if len(audio) < clip_samples:
        return np.pad(audio, (0, clip_samples - len(audio)))
    return audio[:clip_samples]


def synthesize_clip(text: str, length_scale: float, noise_scale: float, noise_w_scale: float) -> np.ndarray:
    """Synthesize one clip via Piper TTS, resampled/fitted to a fixed clip size."""
    from piper.config import SynthesisConfig

    from core.tts import get_voice

    voice = get_voice()
    syn_config = SynthesisConfig(length_scale=length_scale, noise_scale=noise_scale, noise_w_scale=noise_w_scale)

    chunks: list[np.ndarray] = []
    source_rate = SAMPLE_RATE
    for chunk in voice.synthesize(text, syn_config=syn_config):
        chunks.append(chunk.audio_int16_array)
        source_rate = chunk.sample_rate

    audio = np.concatenate(chunks)
    audio = _resample_to_16k(audio, source_rate)
    return fit_to_clip_size(audio)


def generate_clips(texts: list[str], n_per_text: int, seed: int = 0) -> np.ndarray:
    """Synthesize n_per_text variants of each text in `texts` via Piper,
    varying synthesis params for acoustic diversity (a known failure mode
    is overfitting to artifacts specific to one synthesis pass).

    Returns an (N, CLIP_SAMPLES) int16 array.
    """
    rng = np.random.default_rng(seed)
    clips = []
    for text in texts:
        for _ in range(n_per_text):
            length_scale = float(rng.uniform(0.85, 1.25))
            noise_scale = float(rng.uniform(0.5, 0.9))
            noise_w_scale = float(rng.uniform(0.5, 0.9))
            clips.append(synthesize_clip(text, length_scale, noise_scale, noise_w_scale))
    return np.stack(clips)


def generate_synthetic_positive_clips(n_clips: int = 100, seed: int = 0) -> np.ndarray:
    """Synthesize `n_clips` positive "hey mimir" samples via TTS - generic-
    voice diversity to complement (not replace) your real recordings."""
    logger.info("Generating %d synthetic positive clips for %r...", n_clips, WAKE_PHRASE)
    return generate_clips([WAKE_PHRASE], n_per_text=n_clips, seed=seed)


def _augment_one_clip(clip_int16: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Randomly vary volume, add light noise, and time-stretch a single
    real recording - dependency-free (plain numpy/scipy), unlike
    audiomentations/torch_audiomentations which drag in the huge
    speechbrain/torchaudio/acoustics chain documented in the module
    docstring above for functionality this pipeline doesn't need."""
    from scipy import signal

    audio = clip_int16.astype(np.float32)

    # Volume scaling.
    audio = audio * rng.uniform(0.7, 1.3)

    # Light Gaussian noise at a randomized SNR-ish level.
    noise_level = rng.uniform(0.0, 0.03) * (np.abs(audio).max() + 1e-6)
    audio = audio + rng.normal(0, noise_level, size=audio.shape)

    # Speed variation via resampling, then refit to the fixed clip size.
    speed_factor = rng.uniform(0.92, 1.08)
    n_target = max(1, int(len(audio) / speed_factor))
    audio = signal.resample(audio, n_target)

    audio = np.clip(audio, -32768, 32767).astype(np.int16)
    return fit_to_clip_size(audio)


def augment_real_clips(real_clips: list[np.ndarray], n_augmented_per_clip: int = 8, seed: int = 0) -> np.ndarray:
    """Turn a modest number of real "hey mimir" recordings (say 10-20)
    into a much larger, more varied positive set via augmentation -
    asking for hundreds of real repetitions isn't reasonable, but a
    small real set plus heavy augmentation captures your actual voice/
    mic/room far better than synthetic TTS alone.

    Each input clip is expected as float32 in [-1, 1] at SAMPLE_RATE
    (matching core/stt.py's recording convention); returned clips are
    int16 at CLIP_SAMPLES length, matching generate_clips()'s output.
    """
    if not real_clips:
        return np.empty((0, CLIP_SAMPLES), dtype=np.int16)

    rng = np.random.default_rng(seed)
    all_clips = []
    for clip in real_clips:
        clip_int16 = np.clip(clip * 32768.0, -32768, 32767).astype(np.int16)
        all_clips.append(fit_to_clip_size(clip_int16))  # the original, unaugmented
        for _ in range(n_augmented_per_clip):
            all_clips.append(_augment_one_clip(clip_int16, rng))
    return np.stack(all_clips)


def _phones_for_word(word: str) -> list[str]:
    phones = pronouncing.phones_for_word(word)
    if phones:
        return phones
    override = _OOV_PHONEME_OVERRIDES.get(word.lower())
    if override:
        return [override]
    raise ValueError(f"No CMUdict entry or override for word {word!r}; add one to _OOV_PHONEME_OVERRIDES")


def _phoneme_replacement(input_chars: list[str], max_replace: int, replace_char: str = "(.){1,3}") -> list[str]:
    """Vendored from openwakeword.data.phoneme_replacement (unchanged logic)."""
    results = []
    chars = list(input_chars)
    for r in range(1, max_replace + 1):
        for indices in itertools.combinations(range(len(chars)), r):
            chars_copy = chars.copy()
            for i in indices:
                chars_copy[i] = replace_char
            results.append(" ".join(chars_copy))
    return results


def _generate_adversarial_texts(
    input_text: str, n: int, include_partial_phrase: float = 0.0, include_input_words: float = 0.0
) -> list[str]:
    """Vendored from openwakeword.data.generate_adversarial_texts (phoneme-overlap
    permutation logic only) - see the module docstring for why this isn't imported
    directly. Uses _phones_for_word() instead of the original's DeepPhonemizer-based
    OOV fallback."""
    vowel_phones = ["AA", "AE", "AH", "AO", "AW", "AX", "AXR", "AY", "EH", "ER", "EY", "IH", "IX", "IY", "OW", "OY", "UH", "UW", "UX"]

    words = input_text.split()
    word_phones = [_phones_for_word(w)[0] for w in words]
    word_phones = [
        re.sub("|".join(vowel_phones), lambda m: m.group(0) + "[0|1|2]", re.sub(r"\d+", "", p)) for p in word_phones
    ]

    adversarial_phrases = []
    for phones, word in zip(word_phones, words):
        phones = phones.split()
        query_exps = []
        if len(phones) <= 2:
            query_exps.append(" ".join(phones))
        else:
            query_exps.extend(_phoneme_replacement(phones, max_replace=max(0, len(phones) - 2)))

        adversarial_words = []
        for query in query_exps:
            matches = pronouncing.search(query)
            matches_phones = [pronouncing.phones_for_word(m)[0] for m in matches]
            allowed_matches = [m for m, p in zip(matches, matches_phones) if p != phones]
            adversarial_words.extend([m for m in allowed_matches if word.lower() != m])

        if adversarial_words:
            adversarial_phrases.append(adversarial_words)

    rng = np.random.default_rng()
    adversarial_texts = []
    for _ in range(n):
        txts = []
        for choices, original_word in zip(adversarial_phrases, words):
            if rng.random() > (1 - include_input_words):
                txts.append(original_word)
            else:
                txts.append(rng.choice(choices))

        if include_partial_phrase and len(words) > 1 and rng.random() <= include_partial_phrase:
            n_words = rng.integers(1, len(words) + 1)
            adversarial_texts.append(" ".join(rng.choice(txts, size=n_words, replace=False)))
        else:
            adversarial_texts.append(" ".join(txts))

    return [t for t in adversarial_texts if t != input_text]


def generate_hard_negative_texts(n_texts: int = 150) -> list[str]:
    """Generate phonetically-similar-but-different phrases to WAKE_PHRASE."""
    return _generate_adversarial_texts(WAKE_PHRASE, n=n_texts, include_partial_phrase=0.2, include_input_words=0.1)


def generate_negative_clips(n_per_hard_negative: int = 3, n_per_generic: int = 5, seed: int = 1) -> np.ndarray:
    """Synthesize negative clips: phonetic adversarial texts plus generic
    command/filler phrases."""
    hard_negative_texts = generate_hard_negative_texts()
    logger.info("Generated %d hard negative texts (phonetically similar to %r)", len(hard_negative_texts), WAKE_PHRASE)

    negative_clips = generate_clips(hard_negative_texts, n_per_text=n_per_hard_negative, seed=seed)
    generic_clips = generate_clips(_GENERIC_NEGATIVE_PHRASES, n_per_text=n_per_generic, seed=seed + 1)
    return np.concatenate([negative_clips, generic_clips])


def extract_embeddings(clips: np.ndarray) -> np.ndarray:
    """Convert raw audio clips into the same embedding representation the
    runtime model consumes. Onnxruntime-only - does NOT need torch."""
    from openwakeword.utils import AudioFeatures

    af = AudioFeatures(ncpu=1)
    return af.embed_clips(clips, ncpu=1)


def _build_net(input_shape: tuple[int, int], layer_dim: int = 128, n_blocks: int = 1):
    """Same architecture as openwakeword.train.Model(model_type='dnn',
    n_classes=1), rebuilt directly here instead of importing
    openwakeword.train - see the module docstring."""
    import torch.nn as nn

    class FCNBlock(nn.Module):
        def __init__(self, dim):
            super().__init__()
            self.fcn_layer = nn.Linear(dim, dim)
            self.relu = nn.ReLU()
            self.layer_norm = nn.LayerNorm(dim)

        def forward(self, x):
            return self.relu(self.layer_norm(self.fcn_layer(x)))

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            self.flatten = nn.Flatten()
            self.layer1 = nn.Linear(input_shape[0] * input_shape[1], layer_dim)
            self.relu1 = nn.ReLU()
            self.layernorm1 = nn.LayerNorm(layer_dim)
            self.blocks = nn.ModuleList([FCNBlock(layer_dim) for _ in range(n_blocks)])
            self.last_layer = nn.Linear(layer_dim, 1)
            self.last_act = nn.Sigmoid()

        def forward(self, x):
            x = self.relu1(self.layernorm1(self.layer1(self.flatten(x))))
            for block in self.blocks:
                x = block(x)
            return self.last_act(self.last_layer(x))

    return Net()


def train_classifier(
    positive_embeddings: np.ndarray,
    negative_embeddings: np.ndarray,
    max_steps: int = 5000,
    progress_callback: Callable[[str], None] | None = None,
):
    """Train the small classifier head on top of the frozen embeddings."""
    import torch
    from torch import optim

    X = np.concatenate([positive_embeddings, negative_embeddings])
    y = np.concatenate([np.ones(len(positive_embeddings)), np.zeros(len(negative_embeddings))])

    rng = np.random.default_rng(42)
    perm = rng.permutation(len(X))
    X, y = X[perm], y[perm]

    n_val = max(1, int(0.15 * len(X)))
    X_train, y_train = X[n_val:], y[n_val:]
    X_val, y_val = X[:n_val], y[:n_val]

    model = _build_net(input_shape=(X.shape[1], X.shape[2]))
    optimizer = optim.Adam(model.parameters(), lr=0.0001)

    X_val_t = torch.tensor(X_val, dtype=torch.float32)
    y_val_t = torch.tensor(y_val, dtype=torch.float32).unsqueeze(1)

    rng_local = np.random.default_rng(7)
    batch_size = min(32, len(X_train))
    best_val_acc = -1.0
    best_state = None
    eval_every = max(1, max_steps // 20)

    for step in range(max_steps):
        idx = rng_local.integers(0, len(X_train), size=batch_size)
        x_batch = torch.tensor(X_train[idx], dtype=torch.float32)
        y_batch = torch.tensor(y_train[idx], dtype=torch.float32).unsqueeze(1)

        model.train()
        optimizer.zero_grad()
        pred = model(x_batch)
        loss = torch.nn.functional.binary_cross_entropy(pred, y_batch)
        loss.backward()
        optimizer.step()

        if step % eval_every == 0 or step == max_steps - 1:
            model.eval()
            with torch.no_grad():
                val_acc = ((model(X_val_t) > 0.5).float() == y_val_t).float().mean().item()
            logger.info("step %d/%d loss=%.4f val_acc=%.4f", step, max_steps, loss.item(), val_acc)
            if progress_callback:
                progress_callback(f"Training... step {step}/{max_steps}, val accuracy {val_acc:.0%}")
            if val_acc >= best_val_acc:
                best_val_acc = val_acc
                best_state = copy.deepcopy(model.state_dict())

    if best_state is not None:
        model.load_state_dict(best_state)
    logger.info("Best validation accuracy: %.4f", best_val_acc)
    return model


def export_model_to_onnx(model, output_path: Path, class_mapping: str, input_shape: tuple[int, int]) -> None:
    import torch

    model.eval().to("cpu")
    dummy_input = torch.rand(input_shape)[None,]
    # dynamo=False: use the legacy TorchScript-based exporter. The default
    # dynamo=True path in this torch version requires the extra
    # `onnxscript` package; the legacy exporter handles this small DNN
    # fine without it.
    torch.onnx.export(model, dummy_input, str(output_path), output_names=[class_mapping], dynamo=False)


def evaluate_against_negatives(onnx_path: Path, holdout_negative_clips: np.ndarray, sensitivity: float = 0.4) -> dict:
    """Run the exported ONNX model through the same inference_framework=
    'onnx' path the live app uses, against a held-out negative set."""
    from openwakeword.model import Model as InferenceModel

    inference_model = InferenceModel(wakeword_models=[str(onnx_path)], inference_framework="onnx")

    false_positives = 0
    chunk_size = 1280
    for clip in holdout_negative_clips:
        inference_model.reset()
        clip_fired = False
        for start in range(0, len(clip) - chunk_size, chunk_size):
            chunk = clip[start : start + chunk_size].astype(np.int16)
            predictions = inference_model.predict(chunk)
            if any(score > sensitivity for score in predictions.values()):
                clip_fired = True
                break
        if clip_fired:
            false_positives += 1

    return {
        "n_clips": len(holdout_negative_clips),
        "false_positives": false_positives,
        "false_positive_rate": false_positives / max(1, len(holdout_negative_clips)),
    }


def run_full_pipeline(
    real_clips: list[np.ndarray],
    progress_callback: Callable[[str], None] | None = None,
    n_synthetic_positive: int = 100,
    max_steps: int = 5000,
    output_path: Path = OUTPUT_MODEL_PATH,
) -> dict:
    """High-level orchestration for the UI wizard: augments your real
    recordings, blends in synthetic positives/negatives, trains, exports,
    and evaluates. Runs on a background thread; reports progress via
    progress_callback(str) so the UI can update a status label.

    Returns the evaluate_against_negatives() result dict.
    """

    def report(msg: str) -> None:
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    report("Augmenting your recorded samples...")
    augmented_real = augment_real_clips(real_clips)

    report(f"Synthesizing {n_synthetic_positive} additional positive samples...")
    synthetic_positive = generate_synthetic_positive_clips(n_clips=n_synthetic_positive)

    positive_clips = np.concatenate([augmented_real, synthetic_positive])
    report(f"Extracting embeddings for {len(positive_clips)} positive clips...")
    positive_embeddings = extract_embeddings(positive_clips)

    report("Generating hard negative phrases (phonetically similar to your wake word)...")
    negative_clips = generate_negative_clips()
    report(f"Extracting embeddings for {len(negative_clips)} negative clips...")
    negative_embeddings = extract_embeddings(negative_clips)

    n_holdout = max(1, int(0.2 * len(negative_clips)))
    holdout_negative_clips = negative_clips[:n_holdout]
    train_negative_embeddings = negative_embeddings[n_holdout:]

    report("Training classifier...")
    model = train_classifier(
        positive_embeddings, train_negative_embeddings, max_steps=max_steps, progress_callback=progress_callback
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    export_model_to_onnx(
        model, output_path, class_mapping=WAKE_PHRASE, input_shape=(positive_embeddings.shape[1], positive_embeddings.shape[2])
    )
    report(f"Exported model to {output_path}")

    report("Evaluating against held-out negatives...")
    eval_results = evaluate_against_negatives(output_path, holdout_negative_clips)
    report(f"Held-out false positive rate: {eval_results['false_positive_rate']:.1%}")

    return eval_results


def main() -> None:
    """Standalone CLI entry point - synthetic positives only, no real
    recordings. Use ui/wake_word_training_window.py for the personalized,
    real-voice path."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n-positive", type=int, default=200)
    parser.add_argument("--max-steps", type=int, default=5000)
    parser.add_argument("--output", type=Path, default=OUTPUT_MODEL_PATH)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    eval_results = run_full_pipeline(
        real_clips=[], n_synthetic_positive=args.n_positive, max_steps=args.max_steps, output_path=args.output
    )
    logger.info(
        "Compare this false_positive_rate against the current hey_jarvis setup "
        "(via test_wakeword.py --model-path %s) before changing config.yaml.",
        args.output,
    )
    logger.info("Result: %s", eval_results)


if __name__ == "__main__":
    main()
