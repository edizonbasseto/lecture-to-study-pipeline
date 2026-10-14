# 0032. Kokoro (mlx-audio) replaces macOS `say` as the podcast TTS engine

## Status

Accepted. **Amended by ADR 0045:** the local-only mandate no longer binds the LLM phases; lecture content verified to carry no patient-identifying data may be sent to the remote model route. **Addendum (2026-09-17):** the TTS subprocess is now invoked
as `python -m mlx_audio.tts.generate` (this interpreter's own module
form) instead of resolving the console script through the sanitized
environment's PATH -- that PATH does not necessarily include the venv's
bin directory, which is how a real audio pass failed. ffmpeg keeps the
`resolve_trusted_executable` path. Revises ADR 0015 decision 4 (voice mapping) and its Rationale
section "Why hardcoded voices instead of a config file or CLI flag" (the
part deferring any non-`say` TTS runtime to "a later milestone if it is
ever needed" -- this is that milestone, by direct operator request after
round-5 document review). ADR 0015's decisions 1-3 (error taxonomy,
argument-list subprocess invocation, `shell=False`) are unaffected and
still govern this phase's subprocess calls.

## Context

The operator flagged macOS `say`'s output as "robótico" (2026-09-15
document-review handoff, thread 3) and asked directly to investigate a
better local/offline TTS engine rather than keep `say` or drop the
podcast artifact. `DIRETRIZES_ARQUITETURA.txt` section 2 requires all AI
processing to run locally and natively on Apple Silicon, forbids Docker
for AI processes, and names Metal API execution as the standard for
compute-heavy local engines (llama.cpp, whisper.cpp); section 11 (Local
Inference Process Boundary) requires every external ASR/LLM/TTS
subprocess to use a canonical executable, a sanitized environment,
finite timeouts, and bounded output capture. Any replacement engine had
to satisfy the same offline-only, subprocess-isolated posture `say`
already met.

## Decision

Replace `say` with **Kokoro-82M served through the `mlx-audio` CLI**
(`mlx_audio.tts.generate`, package `mlx-audio`), invoked as a subprocess
exactly like `say` and `ffmpeg` are today: argument list, `shell=False`,
sanitized environment, finite timeout, bounded output capture (same
`run_bounded_process`/`resolve_trusted_executable` helpers ADR 0015
already established). One subprocess call still synthesizes one
`PodcastLine` into one chunk file; `ffmpeg` concatenation and AAC
transcode are unchanged.

- **Model:** `mlx-community/Kokoro-82M-bf16`, downloaded once to the
  Hugging Face cache on first run (standard `huggingface_hub` cache
  behavior; no code in this repo manages that cache directly). Inference
  runs on Apple's MLX framework, i.e. natively through Metal, matching
  `DIRETRIZES_ARQUITETURA.txt` section 2's stated preference for Metal-
  native local engines -- unlike a PyTorch or ONNX Runtime path, which
  the instruction group explicitly steered away from for this project
  (ADR 0015's own rationale; llama.cpp/whisper.cpp already avoid those
  runtimes for the same reason).
- **Voices:** `_VOICE_BY_SPEAKER = {"Host": "pf_dora", "Expert":
  "pm_alex"}` -- Kokoro's two Brazilian-Portuguese voices (`lang_code
  "p"`, confirmed via the model's own phonemizer log to resolve to
  `EspeakG2P(language='pt-br')`, not European Portuguese). Verified by
  direct synthesis of real dialogue text from lecture 10003's own
  `script.json` (longest line, 322 characters) before adoption, per the
  project's standing "read the actual output, don't assume" rule --
  output duration was consistent with the input length, not silently
  truncated by length alone.
- **`--join_audio` is mandatory, not optional.** `PodcastLine.text` is
  untrusted LLM output with no guard against an embedded newline.
  Kokoro's own pipeline splits internally on `"\n+"` (its CLI's own
  warning: "Chunking logic not yet implemented, so long texts may be
  truncated unless you split them with '\n'") and, without
  `--join_audio`, writes one file per resulting segment
  (`chunk_001_000.wav`, `chunk_001_001.wav`, ...) with no flag to name
  the output exactly -- a `principal-code-reviewer` pass on this ADR's
  first draft caught, and reproduced live against the real binary, that
  a newline-containing line would then have every segment past the
  first silently dropped (only `_000` was ever read) and permanently
  leaked as an orphan file. `--join_audio` merges every internal segment
  back into one file, confirmed live to always be named exactly
  `{file_prefix}.wav` -- which, since `file_prefix` is set to
  `chunk_path.stem`, is the exact chunk filename this pipeline already
  expects, needing no separate rename step.
- **Dependencies added** (`pyproject.toml`, via `uv add`): `mlx-audio`,
  `misaki` (Kokoro's text-to-phoneme frontend), `phonemizer-fork` (the
  espeak-backed G2P engine `misaki` uses for non-English languages),
  `espeakng-loader` (vendors its own `libespeak-ng.dylib` and voice data
  inside the wheel -- confirmed no system `espeak-ng` install or Homebrew
  dependency is required; `espeakng_loader.get_library_path()` resolves
  inside `.venv`, not `/opt/homebrew`). No `torch`, no ONNX Runtime: a
  `uv pip install --dry-run` of the alternative `kokoro-mlx` package was
  tried first and rejected specifically because it pulled in
  `torch==2.14.0` transitively (spaCy-based tooling, not the inference
  engine itself); `mlx-audio`'s own dependency tree was confirmed clean
  of both before being adopted.

## Alternatives considered

- **macOS Enhanced/Premium `say` voices** (e.g. `Luciana (Enhanced)`,
  downloaded via Sistema > Acessibilidade > Conteúdo Falado). Zero new
  dependency, zero architecture change -- only the voice name in
  `_VOICE_BY_SPEAKER` would change. Rejected as the adopted path (not
  ruled out for the future) per explicit operator choice to go directly
  to a real neural engine rather than stage through this smaller
  intermediate step; noted here as the cheaper fallback if Kokoro's
  output or dependency footprint proves unacceptable in practice.
- **`kokoro-mlx`** (a different PyPI package offering the same Kokoro
  model on MLX). Rejected: pulls `torch` transitively, contradicting
  `DIRETRIZES_ARQUITETURA.txt`'s Metal-native stance and ADR 0015's own
  explicit reason for avoiding a PyTorch/ONNX-based TTS runtime.
- **Piper TTS.** Offline and lightweight, but not Metal-native on Apple
  Silicon (compiles from source, runs on CPU) -- a worse fit for the
  stated Metal-native hardware target than `mlx-audio`'s MLX pipeline.

## Consequences

- `tts/generator.py` no longer resolves/invokes `say`; it invokes the
  TTS engine instead. `_SAY_TIMEOUT_SECONDS` becomes a TTS-engine-neutral
  timeout constant sized for Kokoro's own per-line latency (measured:
  roughly 1-2 seconds per line including process startup and model load
  from the Hugging Face cache, well under `say`'s existing 300-second
  budget, which is kept as a generous ceiling rather than tightened).
- First run of Phase 15 for a fresh environment downloads the
  `mlx-community/Kokoro-82M-bf16` model from Hugging Face (a few hundred
  MB) -- a one-time network fetch by `huggingface_hub`'s own cache
  mechanism, not a per-episode network dependency; every subsequent
  synthesis is fully offline, matching `say`'s own zero-network
  operation once the model is cached.
- The regression test asserting `say`/`ffmpeg` argument-list invocation
  (ADR 0015's "shell metacharacters reach the subprocess as one intact
  argument" test) needs an equivalent test against the new `mlx_audio`
  invocation; test updates land in the same commit as the code change.
