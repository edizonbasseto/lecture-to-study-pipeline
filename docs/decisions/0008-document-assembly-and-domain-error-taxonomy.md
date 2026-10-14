# 0008. Document Assembly: canonical document contract, and a domain-level error taxonomy

## Status

Accepted.

## Context

Phase 7 leaves every lecture's `ValidatedLearningUnit`s scattered as one
JSON file per pack under `workspace/data/09_validated_units/{lecture.id}/`.
Phase 8 needed a deterministic assembly step -- no `llama.cpp`, no
inference -- that aggregates those units into a single
`CanonicalLectureDocument` per lecture, in strict `pack_sequence` order.

This phase was driven by three successive instructions, not one, and this
ADR was written retroactively after a docs-consistency-auditor pass found
the first two commits shipped without it (see Consequences):

1. `.prompts/fase8_document_assembly.md` -- the original instruction.
   Scoped `CanonicalLectureDocument`, `DocumentAssembler.assemble_lecture`,
   and a single validation rule (`MissingSequenceError` on a non-contiguous
   `pack_sequence`). Commit `d33ae6c` implemented exactly this: sequence
   validation only, `MissingSequenceError` defined locally in
   `assembly/assembler.py` (matching every prior phase's per-module
   convention), and a missing `09_validated_units/{lecture_id}/` directory
   silently produced an empty `CanonicalLectureDocument` rather than
   raising. No ADR, no README section, and no 4-agent review board were
   recorded for this commit -- an R0.8 gap this ADR does not paper over.
2. `.prompts/fase8_assembly_policies.md` -- a corrective architectural
   decision issued after `d33ae6c` shipped, opening with "Você encontrou
   dois pontos cegos críticos" ("two critical blind spots were found"):
   the missing-directory case (silently producing an empty document was
   wrong; must raise `LectureNotFoundError`) and the Critic-rejection case
   (`review.passed == False` was never checked at all; must raise
   `UnresolvedReviewError`). Of the two, only `LectureNotFoundError`'s
   location is stated explicitly ("crie-a se necessário em
   `src/lecture_study/domain/errors.py`"); `UnresolvedReviewError`'s bullet
   asks only for "uma exceção de domínio" with no file named. Neither this
   instruction nor a corrective commit implementing it landed before the
   next step.
3. `.prompts/fase8_assembly_full.md` -- a consolidated rewrite folding (1)
   and (2) into one spec, including all three validation rules and the
   `domain/errors.py` location for all three exceptions (not just the two
   from step 2). Executing this instruction found the repository still at
   `d33ae6c`'s state -- one gate implemented, two missing, errors not
   centralized -- and produced corrective commit `70240ea`, which this ADR
   documents together with (1) and (2) as one coherent design rather than
   as an undocumented patch.

Phases 1-7 each define their exceptions locally, in the module that raises
them (`RawVideoNotFoundError` in `extraction/pipeline.py`,
`UnboundedLectureEndTimeError` in `evidence/chunker.py`, etc.) -- there is
no other precedent in this codebase for a shared `domain/errors.py`.
`domain/errors.py`'s existence is a literal, explicit requirement of
instruction (2) above, not a judgment call made independently during
implementation; see Rationale for why that requirement is honored as
written rather than folded back into the per-module convention.

## Decisions

1. **`CanonicalLectureDocument` domain model.** Added to `domain/models.py`
   immediately after `ValidatedLearningUnit`: `lecture_id: SafeIdentifier`,
   `total_units: int` (`Field(ge=0)`, since a valid empty-lecture directory
   assembles to zero units rather than being rejected), `units:
   list[ValidatedLearningUnit]` (already in `pack_sequence` order by the
   time the model is constructed), `assembled_at: float`, `model_config =
   ConfigDict(strict=True)`. `lecture_id` uses `SafeIdentifier` for the same
   filesystem-splicing reason as every other lecture-identifying field
   since ADR 0002 -- the value is spliced into
   `CANONICAL_DOCUMENTS_OUTPUT_DIR / lecture_id` on save. A
   `@model_validator(mode="after")` enforces `total_units == len(units)`,
   matching the cross-field-invariant pattern already used by
   `EvidencePack._validate_temporal_window` and
   `TranscriptionSegment._validate_non_negative_duration` -- added after a
   principal-code-reviewer/staff-architect finding that the field was
   otherwise a derived value the model itself did not protect, so a future
   second writer of `canonical_document.json` (a repair script, a manual
   edit) could desync the two without the domain layer noticing.
2. **`src/lecture_study/domain/errors.py`.** Houses the three
   assembly-policy exceptions, per instruction (2) and (3)'s explicit
   location requirement:
   - `LectureNotFoundError(RuntimeError)` -- no
     `workspace/data/09_validated_units/{lecture_id}/` directory; Phase 7
     never ran for this lecture. Matches the `RuntimeError`-subclass
     convention already used for missing-handoff errors
     (`RawVideoNotFoundError`, `TranscriptionHandoffNotFoundError`,
     `CanonicalHandoffNotFoundError`).
   - `MissingSequenceError(ValueError)` -- the lecture's `pack_sequence`
     values are not a contiguous run starting at 1, or contain a duplicate
     (two units sharing one `pack_sequence`; the error message names which
     case fired, since a duplicate collapses to the same "distinct values
     != len(units)" set-mismatch as a gap and the two are easy to conflate
     when debugging).
   - `UnresolvedReviewError(ValueError)` -- some unit's `review.passed` is
     `False`. Both match the `ValueError`-subclass convention already used
     for data-validation-policy errors (`UnboundedLectureEndTimeError`,
     `DegenerateWindowError`).
3. **`DocumentAssembler.assemble_lecture`, `assembly/assembler.py`.** Runs,
   in order:
   0. `lecture_id` is validated as a `SafeIdentifier` (via the same
      `TypeAdapter(SafeIdentifier)` convention as
      `critic`/`semantic`/`drafting` `pipeline.py`) *before* any `Path`
      splice -- added after a security-reviewer/principal-code-reviewer
      finding that the original `d33ae6c`/first `70240ea` cut spliced the
      raw caller-supplied string into `VALIDATED_UNITS_INPUT_DIR /
      lecture_id` and only validated it later, incidentally, when
      `CanonicalLectureDocument` was constructed -- by which point
      `_load_units` had already called `.exists()`/`.glob()` against
      whatever path an unvalidated `lecture_id` (e.g. containing `..`)
      produced. Every sibling phase validates before the first path use;
      Phase 8 now does too.
   1. `_load_units` raises `LectureNotFoundError` if
      `VALIDATED_UNITS_INPUT_DIR / lecture_id` does not exist -- it must
      never fall back to an empty unit list, since that would silently
      manufacture a canonical document for a lecture Phase 7 never
      processed. A directory that exists but is empty (Phase 7 ran and
      produced zero packs) is not this case and assembles to a valid
      zero-unit document.
   2. `_validate_contiguous_sequence` sorts by `pack_sequence` and raises
      `MissingSequenceError` if the sequence numbers are not exactly
      `{1, ..., len(units)}`, distinguishing a duplicate-value cause
      (`len(distinct values) != len(units)`) from a genuine gap in the
      error message.
   3. `_validate_no_unresolved_review` walks the sequence-ordered units and
      raises `UnresolvedReviewError`, naming the first offending
      `pack_sequence`, on the first unit whose `review.passed` is `False`.
   Only once all four pass does `assemble_lecture` construct the
   `CanonicalLectureDocument` and call `_save_document`, which persists to
   `workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json`
   via the write-to-`.tmp`-then-`replace` pattern already used by every
   other phase's disk writes in this codebase, so a crash mid-write can
   never leave a half-written canonical document behind.

## Rationale

- **Why a shared `domain/errors.py` instead of extending the per-module
  convention.** The honest driver is instruction (2)/(3)'s literal text,
  not an independently-discovered architectural rule. A post-hoc
  justification was considered -- "domain-policy errors (violate an
  invariant of the domain model itself) go in `domain/errors.py`;
  technical/IO errors (local to one worker's interaction with the outside
  world) stay local" -- but a staff-architect review of this ADR's first
  draft correctly noted that rule does not actually discriminate against
  existing code: `evidence/chunker.py`'s `DegenerateWindowError` and
  `UnboundedLectureEndTimeError` are the same *kind* of domain-invariant
  violation and were not migrated. Rather than overclaim a clean taxonomy
  this codebase does not otherwise follow, this ADR records the honest
  reason (explicit instruction) and treats `chunker.py`'s errors as
  intentionally grandfathered, not as an inconsistency to silently paper
  over. Any future domain-policy error should ask "does this belong next
  to `LectureNotFoundError`/`MissingSequenceError`/`UnresolvedReviewError`
  in `domain/errors.py`, or is it scoped to one phase like `chunker.py`'s
  errors" as a genuine per-case judgment call, not resolve it from a
  restated rule this file cannot honestly claim already exists.
- **Why `LectureNotFoundError` keeps its name instead of following the
  `*HandoffNotFoundError` sibling convention.** `RawVideoNotFoundError`,
  `TranscriptionHandoffNotFoundError`, and `CanonicalHandoffNotFoundError`
  all name the missing *handoff artifact*, and a reviewer flagged that
  `LectureNotFoundError` instead reads as "no such lecture" (a different,
  already-used concept: `LectureMetadata`/ingestion discovery). The name
  is kept as-is because it is instruction (2)'s literal, explicit class
  name, not a name chosen independently during implementation; the
  docstring on `LectureNotFoundError` in `domain/errors.py` exists
  specifically to close that ambiguity for a reader who only sees the
  class name.
- **Why validation runs handoff-existence, then sequence, then review,
  in that fixed order.** This is instruction (3)'s literal rule order
  ("REGRAS DE VALIDAÇÃO (Tolerância Zero): 1... 2... 3..."). It also
  happens to be the cheapest-first ordering (a missing directory is a
  single `Path.exists()` check; sequence and review both require every
  unit already loaded and parsed), so no change was needed to make the
  mandated order also the efficient one.

## Consequences

- `domain/errors.py` is now the first shared exception module in this
  codebase. Future phases that add a domain-policy validation (as opposed
  to a technical/IO failure local to one worker) should extend this file
  rather than default back to a local module-level exception class --
  understanding that this is a precedent this ADR sets going forward, not
  a rule the rest of the codebase already followed (see Rationale).
- Assembly is fully synchronous and has no `asyncio`, no subprocess, and no
  Metal/`llama.cpp` dependency -- unlike Phases 2-7, it needs no
  concurrency limit and no per-lecture failure isolation, since it is pure
  in-process JSON aggregation over already-validated Phase 7 output.
- `assemble_lecture(lecture_id: str)` takes a single lecture id per call
  and has no batch/discovery entry point (`run()`), unlike every
  `pipeline.py` orchestrator in Phases 2-7, which self-discover all
  pending work under their input directory. This matches instruction
  (1)/(3)'s literal method signature; a future phase (CLI or batch
  orchestration wiring across all lectures with a `09_validated_units/`
  directory) will need to add that discovery layer on top of
  `DocumentAssembler` rather than inside it.
- Commit `d33ae6c` shipped without an ADR, without a README section, and
  without evidence of the DIRETRIZES_ARQUITETURA.txt-mandated 4-agent
  review board (`principal-code-reviewer`, `security-reviewer`,
  `staff-architect`, `docs-consistency-auditor`) having run against it.
  Corrective commit `70240ea` and this ADR are the first artifacts in this
  phase's history to close both gaps; the review board ran against the
  full `6326922..HEAD` diff (both commits) after `70240ea` landed, and its
  findings (the `SafeIdentifier`-before-path-splice fix, the
  `total_units` invariant, the duplicate-`pack_sequence` message, and this
  ADR's Rationale/Alternatives Considered sections) are folded into
  Decisions 1-3 above rather than tracked as a separate pending item.

## Alternatives Considered

- **Keep `MissingSequenceError` local to `assembly/assembler.py`
  (as `d33ae6c` originally did) and only move `LectureNotFoundError` /
  `UnresolvedReviewError` to `domain/errors.py`.** Rejected: instruction
  (3) explicitly lists all three exceptions under the `domain/errors.py`
  requirement, and splitting the three semantically-equivalent assembly
  gates across two locations (two in the shared module, one local) would
  be a strictly worse compromise than either fully-local or
  fully-shared -- a future reader would have no way to predict which of
  the three lives where without checking each one.
- **Validate `lecture_id` inside `CanonicalLectureDocument`'s constructor
  only (the original design) instead of eagerly in
  `assemble_lecture`.** Rejected after review: `SafeIdentifier` on the
  Pydantic model does reject a malformed `lecture_id` before any data
  reaches disk, but only *after* `_load_units` has already spliced the
  unvalidated string into a `Path` and called `.exists()`/`.glob()`
  against it -- a real (if currently unreachable, since the only caller in
  this repo is a hardcoded literal) directory-traversal-shaped read. Every
  sibling phase validates at the function boundary before the first path
  use; Phase 8 now matches that instead of relying on a downstream model
  constructor to catch it late.
- **Silently skip (rather than raise) an individual Critic-rejected unit,
  assembling the document from the remaining passing units.** Rejected by
  instruction (2) directly: "Tolerância Zero... O documento canônico NÃO
  pode ser gerado" -- a partially-assembled document missing a
  clinically-unvetted unit is exactly the kind of incomplete-but-silent
  output the whole validation chain (Phases 5-8) exists to prevent, since
  a reader of the final study material has no way to know a unit is
  missing from the middle of a lecture's reasoning chain.
