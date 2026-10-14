"""Domain-level errors shared across pipeline phases, for validation gates
on an upstream handoff artifact's existence rather than one phase's
internal logic.

Every other pipeline module (extraction, evidence, transcription, ...)
defines its exceptions locally, next to the code that raises them (see
OcrError, WhisperTranscriptionError, DegenerateWindowError). This file
started as the one deliberate exception for Phase 8 (see docs/decisions/
0008-document-assembly-and-domain-error-taxonomy.md's Rationale section for
why that instruction is honored as written rather than folded back into
the per-module convention, and why "domain-policy vs technical" is not
claimed as an established, codebase-wide rule) and was extended by Phase 9
(docs/decisions/0009-deterministic-metadata-deduplication.md), per ADR
0008's own Consequences section anticipating exactly this: "Future phases
that add a domain-policy validation... should extend this file rather
than default back to a local module-level exception class." Phase 9 also
reuses LectureNotFoundError itself (not just this file) for a second,
structurally distinct trigger, and Phase 11 (`publishing/pipeline.py`)
reuses it at two call sites -- once for that same Phase 9 trigger, once
for a third, new trigger of its own -- see that class's docstring for
every condition and call site. Phase 12
(`flashcards/generator.py`) adds `GlobalIndexNotFoundError`, a
`LectureNotFoundError` subclass, for that same missing-`global_index.json`
trigger at their own call sites -- see those classes' docstrings. (The
local Phase 13/14 generators that once added `MockExamGenerationError`
and `PodcastGenerationError` here were deleted under ADR 0038, and those
two dead classes went with them; exam and podcast failures now surface
as the Codex scripts' own nonzero exits, wrapped by the orchestrator.)
Phase 15
(`tts/generator.py`) adds `ScriptNotFoundError`, a `LectureNotFoundError`
subclass for a missing-`script.json` trigger (see that class's own
docstring), and `AudioGenerationError` for its `mlx_audio`/`ffmpeg` subprocess
failures, following Phase 13-14's own `*GenerationError` precedent. Phase 16
(`cli/orchestrator.py`) adds `PipelineExecutionError` for CLI-level
orchestration failures that are not any one phase's own domain
exception -- see that class's own docstring.
"""


class LectureNotFoundError(RuntimeError):
    """Raised when a lecture is missing an upstream handoff artifact a
    phase depends on:
    - Phase 8 (`assembly/assembler.py`): no
      `workspace/data/09_validated_units/{lecture_id}/` directory -- Phase
      7 never ran (or never ran for this lecture_id).
    - Phase 9 (`metadata/aggregator.py`): no
      `workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json`
      file -- Phase 8 never produced a canonical document for this
      lecture.
    - Phase 11 (`publishing/pipeline.py`), first check: no
      `workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json`
      file -- the same underlying condition as Phase 9's check above, this
      call site guards `PublishingOrchestrator.publish_lecture` before
      Markdown compilation runs.
    - Phase 11 (`publishing/pipeline.py`), second check: no
      `workspace/data/11_metadata/{lecture_id}/global_index.json` file --
      Phase 9 never persisted a global index for this lecture. Reads that
      file directly rather than calling `MetadataAggregator` itself, so
      this phase stays self-contained (imports only `domain.models`, per
      ADR 0006) instead of depending on a sibling phase's orchestrator.
    - Phase 12 (`flashcards/generator.py`) raises the subclass
      `GlobalIndexNotFoundError` (see below) for that same missing-
      `global_index.json` condition, at its own call site. A caller doing
      `except LectureNotFoundError` catches both Phase 11's and Phase 12's
      checks for this file; `except GlobalIndexNotFoundError` narrows to
      Phase 12's alone.
    - Phase 13 (`exams/generator.py`): no
      `workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json`
      file -- the same underlying condition as Phase 9's and Phase 11's
      first check above, this call site guards `MockExamGenerator.
      generate_exam` before any llama.cpp inference runs.
    - Phase 14 (`podcast/generator.py`): no
      `workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json`
      file -- the same underlying condition as Phase 9's, Phase 11's, and
      Phase 13's checks above, this call site guards
      `PodcastScriptGenerator.generate_script` before any llama.cpp
      inference runs.
    - Phase 15 (`tts/generator.py`) raises the subclass
      `ScriptNotFoundError` (see below) for no
      `workspace/data/15_podcast_scripts/{lecture_id}/script.json` file --
      Phase 14 never produced a podcast script for this lecture. A caller
      doing `except LectureNotFoundError` catches this check too; `except
      ScriptNotFoundError` narrows to Phase 15's alone.
    All cases must abort rather than silently producing an incomplete or
    manufactured result."""


class MissingSequenceError(ValueError):
    """Raised when a lecture's pack_sequence values are not a contiguous
    run starting at 1. Assembling past a gap would misrepresent the
    lecture's clinical reasoning order."""


class UnresolvedReviewError(ValueError):
    """Raised when any ValidatedLearningUnit in the sequence has
    review.passed == False. Critic-rejected clinical content must never
    enter the canonical document."""


class SemanticLedgersNotFoundError(RuntimeError):
    """Raised when a lecture has a Phase 8
    `workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json`
    but its Phase 5 `workspace/data/07_semantic_ledgers/{lecture_id}/`
    directory is missing entirely. Phase 5 writes one ledger file per
    evidence pack unconditionally, even a zero-fact one (see
    `SemanticLedger.facts`'s own empty-list default) -- so a
    canonically-complete lecture with no ledgers directory at all means
    the directory was lost or moved after Phase 8 ran, not a lecture with
    no clinical content. That legitimate case still has ledger files on
    disk, just with empty `facts` lists, and is not this error."""


class GlobalIndexNotFoundError(LectureNotFoundError):
    """Raised when a lecture has no
    `workspace/data/11_metadata/{lecture_id}/global_index.json` file --
    Phase 9 (`metadata/aggregator.py`) has not produced a global index for
    this lecture. Guards Phase 12 (`flashcards/generator.py`) before
    flashcard generation runs.

    Subclasses `LectureNotFoundError` rather than `RuntimeError` directly:
    `publishing/pipeline.py` (Phase 11) already guards this exact same
    missing-file condition by raising `LectureNotFoundError` itself (see
    that class's docstring), and this codebase's own precedent (ADR 0009's
    Alternatives Considered) is to mint a new class only when a caller
    needs to discriminate a condition from `LectureNotFoundError` by type
    -- not to multiply disjoint `*NotFoundError` classes for what is, at
    the concept level, the same "this lecture has not reached the
    pipeline stage this code depends on" trigger. Phase 12's own
    instruction explicitly names a distinct `GlobalIndexNotFoundError`
    class, so this subclass exists to honor that instruction literally
    while `except LectureNotFoundError` still catches it -- see
    docs/decisions/0012-deterministic-flashcard-generation.md's
    Alternatives Considered."""


class ScriptNotFoundError(LectureNotFoundError):
    """Raised when a lecture has no
    `workspace/data/15_podcast_scripts/{lecture_id}/script.json` file --
    the podcast stage (`scripts/generate_codex_podcast.py` writing through
    `podcast/compiler.py`, ADR 0037/0038) has not produced a podcast
    script for this lecture. Guards Phase 15
    (`tts/generator.py`) before any `mlx_audio`/`ffmpeg` subprocess runs.

    Subclasses `LectureNotFoundError` rather than `RuntimeError` directly,
    matching `GlobalIndexNotFoundError`'s own precedent (Phase 12): this
    is the same "lecture has not reached the pipeline stage this code
    depends on" trigger every `LectureNotFoundError` call site already
    covers, and Phase 15's own instruction names a distinct
    `ScriptNotFoundError` class explicitly -- honored literally here while
    `except LectureNotFoundError` still catches it."""


class AudioGenerationError(RuntimeError):
    """Raised by Phase 15 (`tts/generator.py`) when local, native audio
    synthesis cannot produce a lecture's `podcast_final.m4a`: an `mlx_audio`
    subprocess invocation fails for a dialogue chunk (missing binary,
    non-zero exit), or the final `ffmpeg` concat/transcode invocation
    fails (missing binary, non-zero exit).

    Placed here directly (historically following the Phase 13-14
    generation-error precedent, those classes since removed with the
    local LLM route under ADR 0038): a subprocess-level failure
    category, not a domain-policy check.
    `tts/generator.py` raises this class directly at every failure site,
    so a caller doing `except AudioGenerationError` catches the whole
    synthesis path in one place."""


class PipelineExecutionError(RuntimeError):
    """Raised by Phase 16 (`cli/orchestrator.py`) for a CLI-level
    orchestration failure that is not itself one specific phase's own
    domain exception: a lecture missing from `queue.json` (never
    discovered/enqueued), a required model path
    (`--whisper-model-path`) not supplied for a phase that needs one,
    an unknown phase number, `run-phase` targeting a
    phase with no per-lecture entry point (Phases 5-7, which only expose
    a whole-backlog `run()`), or a phase's outcome-collecting orchestrator
    (Phases 2-4) reporting a per-lecture failure via its own
    `LectureExtractionOutcome`/`LectureTranscriptionOutcome`/
    `LectureEvidenceOutcome.error` string rather than raising.

    Every other phase's own exception (`LectureNotFoundError`,
    `SemanticLedgersNotFoundError`, `MissingSequenceError`,
    `UnresolvedReviewError`, `MockExamGenerationError`,
    `PodcastGenerationError`, `ScriptNotFoundError`,
    `AudioGenerationError`) propagates through `cli/orchestrator.py`
    unchanged -- this class is additive, not a wrapper, so `except
    LectureNotFoundError` (for example) still narrows to that phase's own
    condition at the CLI boundary."""
