# 0015. Local TTS audio generation: argument-list subprocess invocation and hardcoded PT-BR voices

## Status

Accepted. **Erratum (ADR 0022):** the Rationale section below describes a
`PodcastScript` compiled by Phase 14 as "grammar-constrained JSON" -- this
is no longer how Phase 14 enforces its output shape (see ADR 0022: that
llama.cpp build's grammar sampler could not be used at all, so Phase 14
now embeds its schema as prompt text with post-hoc parse/validate and a
repair retry instead). This ADR's own decision (argument-list subprocess
invocation, `shell=False`) is unaffected by that correction.

## Context

Phase 14 (ADR 0014) produced `workspace/data/15_podcast_scripts/{lecture_id}/script.json`,
a `PodcastScript` whose `dialogue` is reserved explicitly for "a future
Phase 15 text-to-speech (TTS) pass". `.prompts/fase15_podcast_audio.md`
asks for that pass: turn each `PodcastLine` into a spoken-audio chunk via
the macOS `say` command, concatenate the chunks with `ffmpeg`, and
transcode to a single `podcast_final.m4a`, entirely offline and native to
Apple Silicon (`DIRETRIZES_ARQUITETURA.txt` section 2: no Docker, no
network dependency for AI/media processing). Every `PodcastLine.text` this
phase reads originates in Phase 14's LLM pass -- untrusted, unpredictably-
punctuated natural-language content, not a value this codebase controls.

## Decisions

1. **`ScriptNotFoundError`/`AudioGenerationError`** (`domain/errors.py`):
   `ScriptNotFoundError` subclasses `LectureNotFoundError`, matching
   `GlobalIndexNotFoundError`'s own precedent (Phase 12) -- the same
   "lecture has not reached the pipeline stage this code depends on"
   trigger every other `LectureNotFoundError` call site already covers,
   for the missing-`script.json` condition (Phase 14 never ran for this
   lecture). `AudioGenerationError` is a plain `RuntimeError`, matching
   `MockExamGenerationError`/`PodcastGenerationError`'s own shape (Phases
   13-14): a subprocess-level failure category (missing `say`/`ffmpeg`
   binary, non-zero exit), not a domain-policy check.
2. **`PodcastAudioGenerator.generate_podcast`** (`tts/generator.py`):
   validates `lecture_id` as a `SafeIdentifier` before any `Path` splice
   (matching `PodcastScriptGenerator.generate_script`'s own guard,
   Phase 14), reads `workspace/data/15_podcast_scripts/{lecture_id}/script.json`
   directly rather than importing `podcast/compiler.py`'s
   `PODCAST_SCRIPTS_OUTPUT_DIR` constant -- keeping `tts/` self-contained
   per ADR 0006's rule (each phase module imports only `domain.models`,
   never a sibling phase module), the same choice every prior phase in
   this chain (Phases 9, 11, 12, 13, 14) already made for its own upstream
   artifact. Writes numbered chunk files (`chunk_001.wav`, ...) and the
   final `podcast_final.m4a` to `workspace/data/16_podcast_audio/{lecture_id}/`,
   deleting only the intermediate `.wav`/`concat_list.txt` files afterward
   (in a `finally` block) -- `podcast_final.m4a` itself is the phase's
   deliverable, not a temporary. Each `chunk_path` is recorded in the
   shared `chunk_paths` list *before* its own `say` subprocess call, not
   after: a `principal-code-reviewer`/`staff-architect` pass on this ADR's
   first draft both found that appending only on success left the one
   chunk actually failing (not just every chunk before it) untracked and
   therefore never cleaned up, when `say` writes a partial `.wav` before
   crashing or exiting non-zero -- `Path.unlink(missing_ok=True)` no-ops
   harmlessly for the (rarer) case where no file was written at all, so
   recording the path unconditionally has no downside. `_concatenate_chunks`
   also unlinks any pre-existing `podcast_final.m4a` before invoking
   `ffmpeg`: the same review pass noted that a rerun for a lecture whose
   previous attempt already produced (or partially produced) that file
   would otherwise hand `ffmpeg`'s concat muxer an existing output path,
   which prompts for an overwrite confirmation that never arrives in a
   non-interactive run.
3. **Argument-list subprocess invocation only; `shell=True` is forbidden.**
   Both `subprocess.run` calls (`say`, `ffmpeg`) pass their command as a
   list, `shell=False` (the module default, never overridden). A
   `PodcastLine.text` containing shell metacharacters (`;`, `` ` ``,
   `$(...)`, unbalanced quotes) is passed to `say` as a single opaque
   positional argument in this mode -- it can never be interpreted by a
   shell, because no shell is ever invoked to parse it. This is the same
   protection class `flashcards/generator.py` (Phase 12) and
   `publishing/markdown_compiler.py` (Phase 11) already apply to LLM- or
   OCR-sourced free text reaching a different sink (CSV, Markdown); here
   the sink is an OS subprocess argument vector instead.
4. **Strict, hardcoded PT-BR voice mapping.** `_VOICE_BY_SPEAKER =
   {"Host": "Luciana", "Expert": "Felipe"}` in `tts/generator.py`, keyed by
   `PodcastLine.speaker`'s own `Literal["Host", "Expert"]` (domain/models.py,
   Phase 14) -- the dict is therefore exhaustive by construction; no third
   speaker value can ever reach it. No configuration surface, no fallback
   to a system default voice: the source material is Portuguese, and a
   silently mismatched voice/accent would degrade every finished episode
   without raising any error.

## Rationale

- **Why `shell=False` (argument lists) instead of any string-building
  path to `subprocess.run(..., shell=True)`.** A `PodcastScript` compiled
  by Phase 14 is grammar-constrained JSON but not shell-safe text --
  nothing in that pass sanitizes `text` for shell metacharacters, because
  nothing downstream of it needed shell-safety until this phase. Building
  a shell command string from `text` (e.g. `f"say -v {voice} '{text}'"`)
  would require correctly escaping every shell metacharacter the LLM might
  produce, including one this codebase has not enumerated; passing the
  argument list directly removes the entire escaping problem instead of
  attempting to solve it correctly.
- **Why chunk-then-concatenate instead of a single `say` invocation over
  the whole script.** `say` has no notion of a multi-voice script; one
  invocation is one voice for its whole input. Chunking per dialogue line
  is the only way to alternate `Luciana`/`Felipe` mid-episode, and
  concatenating post hoc via `ffmpeg`'s `concat` demuxer is the standard,
  lossless way to join same-format audio segments before the final AAC
  transcode.
- **Why the workspace directory is not deleted wholesale after
  concatenation.** `podcast_final.m4a` is written into the same
  `workspace/data/16_podcast_audio/{lecture_id}/` directory the chunk
  files use, per the instruction's own literal `ffmpeg` invocation
  (relative `podcast_final.m4a`, run with that directory as the working
  directory). Deleting the whole directory after concatenation would
  delete the deliverable itself; only the two known-temporary file
  categories (`.wav`, `concat_list.txt`) are unlinked.
- **Why hardcoded voices instead of a config file or CLI flag.** V1 has
  exactly one source language (Portuguese) and exactly two fixed speaker
  roles (`Host`, `Expert`) by domain-model construction (Phase 14's own
  `Literal["Host", "Expert"]`); a configuration surface for a dimension
  that cannot currently vary would be speculative generality with no
  present caller. This also avoids any PyTorch/ONNX-based TTS runtime
  dependency for V1, per the instruction's own stated goal, deferring
  that class of dependency (and any future non-PT-BR voice requirement)
  to a later milestone if it is ever needed.

## Consequences

- `domain/errors.py` gains `ScriptNotFoundError` (a `LectureNotFoundError`
  subclass) and `AudioGenerationError` (a plain `RuntimeError`); its module
  docstring and `LectureNotFoundError`'s own docstring are updated to list
  Phase 15's new call sites, per this file's established "docstring is the
  authoritative site list" convention. `AudioGenerationError` is this
  file's third consecutive addition (after `MockExamGenerationError`,
  Phase 13, and `PodcastGenerationError`, Phase 14) with zero domain-policy
  content -- a `staff-architect` pass on this ADR's first draft flagged
  this as a widening pattern with no capping decision, the same shape
  ADR 0014 already named explicitly for llama.cpp call-site duplication.
  **Operator decision:** placement stays in `domain/errors.py`, per the
  Phase 15 instruction's own explicit direction ("Em
  `src/lecture_study/domain/errors.py`, adicione: ... `AudioGenerationError`")
  -- not re-litigated per-phase, matching ADR 0013's own precedent of
  reading an instruction naming this file directly as decisive over the
  domain-policy-vs-technical criterion. A dedicated ADR revisiting
  `domain/errors.py`'s scope as a whole (fold the three `*GenerationError`
  classes back to per-module locals, or formally accept this file as the
  project's general subprocess/inference-failure taxonomy) is deferred as
  a named V2.0 item, not left as a vague future note a fourth phase would
  otherwise repeat again.
- `tts/` is llama.cpp-free: unlike Phases 6/13/14/9's LLM-inference call
  sites, this phase invokes only OS-native `say`/`ffmpeg`, so it carries
  none of ADR 0006's self-containment duplication burden those phases
  already flagged (ADR 0014's Consequences) -- there is no shared worker
  machinery for this phase to duplicate.
- `tts/` reaches 100% statement coverage with all subprocess calls mocked
  (`unittest.mock.patch`, no real `say`/`ffmpeg` invocation in the test
  suite), including a dedicated regression test asserting that a
  dialogue line containing shell metacharacters (quotes, semicolons,
  backticks) reaches `subprocess.run` as one intact list element, never
  concatenated into a shell string.
- Running this phase for real requires the `Felipe` PT-BR voice to be
  installed locally (macOS ships `Luciana` by default; `Felipe` may need
  a manual System Settings > Accessibility > Spoken Content download) --
  an operational prerequisite, not a code-level concern this ADR's scope
  covers.

## Alternatives Considered

- **A single `subprocess.run` per episode, alternating voices via SSML-
  like markup `say` does not actually support.** Rejected: `say` has no
  multi-voice input format; per-line invocation is the only mechanism
  that lets each line select its own PT-BR voice.
- **`shlex.quote`-escaped string interpolation into `shell=True`.**
  Considered as a way to keep a single readable command string. Rejected:
  even correctly `shlex.quote`-escaped, this still routes untrusted LLM
  text through a shell parse step that the argument-list form skips
  entirely -- strictly more attack surface for no behavioral benefit, and
  the instruction's own P0 rule forbids `shell=True` outright.
