# 0016. Master CLI orchestrator: composition-root registry, filesystem-artifact resume, and RuntimeError/ValueError as the CLI's error boundary

## Status

Accepted; amended on 2026-07-28 after the three-lecture quality pilot.

## Amendment: `discover` command removed (see ADR 0044)

Portal discovery is not part of this repository. The interactive `discover`
command of `cli/main.py` and the `ingestion/` package behind it were removed;
lectures now enter `workspace/data/queue.json` through
`scripts/register_local_lecture.py`. `run-all` and `run-phase` keep the
precondition described in Decision 4 -- the target lecture must already be in
`queue.json` -- but that row is written by registration, not by `discover`.
Every mention of `discover`, Phase 1, or `LectureDiscovery` below is historical
and does not describe a command available in this repository.

## 2026-07-28 Amendment: lecture-scoped LLM phases

The pilot demonstrated that the original whole-backlog behavior of Phases 5-7
was no longer an acceptable operational boundary. Repairing one two-pack
lecture reprocessed 109 packs from the other two queued lectures before
reaching the requested work. A single deterministic failure then required
repeating the same unrelated inference.

`SemanticOrchestrator.run`, `DraftOrchestrator.run`, and
`CriticOrchestrator.run` now accept an optional validated `lecture_id`. An
omitted value preserves the original whole-backlog API. The CLI always passes
the requested lecture identifier, and Phases 5-7 now support `run-phase` as
well as `run-all`. `_run_lecture_scoped_llm_phase` replaces
`_run_global_batch_phase`; it verifies every returned outcome for the selected
lecture. Each phase also rejects a pack or draft whose declared lecture and
sequence do not exactly match its directory and filename before inference or
output mutation, so a scoped run cannot process another lecture's artifacts.

This amendment supersedes the original Context decision, Decision 3's
Global-batch bullet, Decision 8, and the rejected `run_for_lecture` alternative
below. Those sections remain as historical rationale for the original
implementation. The amendment changes discovery scope only; concurrency
remains one, per-pack failures remain isolated, and the CLI still fails the
selected phase if any selected outcome failed.

The current registry contains 14 entries for Phases 2-15, including the
quality-certification boundary at Phase 10. Resume checks are stage-specific:
Phases 2-9 use exact files, directories, or sequence equality; Phase 10
revalidates publication readiness; Phases 11-12 revalidate current
deterministic delivery state; and Phases 13-15 always execute because no safe
resume predicate exists for those generated derivatives.

## Historical context (superseded on 2026-07-28)

The remaining sections record the original Phase 16 decision and are
non-normative where they conflict with the amendment above.

Phases 1-15 are implemented, tested, and review-board-approved, but nothing in
this codebase runs them together. `src/lecture_study/cli/main.py`
(Phase 1) only wires the interactive discovery/selection flow that
writes `workspace/data/queue.json`; every later phase's orchestrator or
generator/compiler pair is invoked only from that phase's own test
suite. `.prompts/fase16_master_cli.md` asks for a `run-all <lecture_id>`
(full pipeline, resumable) and `run-phase <phase_number> <lecture_id>`
(one phase in isolation) CLI, built on `argparse`, with domain-exception-
aware error handling and `sys.exit(1)` on failure.

Research into every phase's actual entry point before writing any code
surfaced a structural fact the instruction's own `run-phase <phase_number>
<lecture_id>` command shape does not account for: Phases 5-7 (`semantic`,
`drafting`, `critic`) each expose only a whole-backlog `async def
run(self) -> list[...]`, scanning every pending pack/draft under their
own input directory across *all* lectures at once -- no `lecture_id`
parameter, no `queue.json`, nothing scoped to one lecture. This was
confirmed by reading `semantic/pipeline.py:58`, `drafting/pipeline.py:66`,
and `critic/pipeline.py:76` directly, not inferred from naming. **Operator
decision** (asked explicitly, since this changes both `run-phase`'s
semantics for three phases and whether already-shipped, review-board-approved
Phase 5-7 code gets touched): `run-phase 5/6/7 <lecture_id>` rejects with
a clear `PipelineExecutionError` rather than silently processing every
other lecture currently queued at that stage; `run-all` still calls their
global `run()` as an unavoidable step toward Phase 8+, with a post-run
existence check confirming this lecture's own output actually appeared.
Phases 5-7's orchestrator classes are not modified by this phase.

## Historical decisions (superseded where amended)

1. **`PipelineExecutionError`** (`domain/errors.py`): a plain
   `RuntimeError`, added per the instruction's own Step 16.1, for CLI-level
   orchestration failures that are not any one phase's own domain
   exception -- an unknown phase number, a lecture missing from
   `queue.json`, a required `--whisper-model-path`/`--llm-model-path` not
   supplied, `run-phase` targeting a Phase 5-7 number, or Phases 2-4's
   outcome-collecting orchestrators reporting a per-lecture failure via
   their own `Outcome.error` string (see decision 3) rather than raising.
2. **`cli/orchestrator.py`, a new module, is the pipeline's composition
   root and is deliberately exempt from ADR 0006's self-containment
   rule.** Every prior phase module imports only `domain.models` (plus,
   for domain exceptions, `domain.errors`) and never a sibling phase --
   that rule exists so Phase N's already-shipped behavior stays isolated
   from Phase M's later changes. `cli/orchestrator.py` inverts this on
   purpose: it is the one module whose entire job is depending on every
   phase's real class and real output-directory constant (`
   CANONICAL_OUTPUT_DIR`, `TRANSCRIBED_OUTPUT_DIR`, `EVIDENCE_PACKS_OUTPUT_DIR`,
   ... one per phase, imported directly rather than re-declared as
   parallel string literals) -- reusing, not duplicating, each phase's own
   constant, since this module sits architecturally *above* all of them,
   not beside them.
3. **`PhaseSpec` registry, `PhaseSpec.supports_single_lecture`, and two
   distinct execution shapes.** `PHASE_REGISTRY` is a tuple of 13
   `PhaseSpec` entries (Phases 2-9, 11-15 -- see decision 5 for why 1 and
   10 are absent), each pairing a `number`/`name` with an
   `artifact_exists(lecture_id) -> bool` resume check and an
   `async run(ctx, lecture_id) -> None` action. Two distinct action shapes
   exist, matched to what each phase's own orchestrator actually returns:
   - **Direct-raise phases** (8, 9, 11, 12, 13, 14, 15): call the
     phase's own method (`DocumentAssembler.assemble_lecture`,
     `MetadataAggregator.aggregate_lecture`,
     `PublishingOrchestrator.publish_lecture`,
     `AnkiDeckGenerator.generate_deck`, or the generator+compiler pair for
     13/14/15) and let its own domain exception propagate unchanged.
   - **Outcome-collecting phases** (2, 3, 4): `ExtractionOrchestrator`/
     `TranscriptionOrchestrator`/`EvidencePackOrchestrator.run()` never
     raise for a single lecture's failure -- each isolates it into a
     `LectureExtractionOutcome`/`LectureTranscriptionOutcome`/
     `LectureEvidenceOutcome` with `error: str | None`, so the rest of a
     multi-lecture queue is unaffected by one lecture's failure. A shared
     structural `_LectureOutcome` `Protocol` (`lecture_id: str`,
     `error: str | None`) lets `_find_outcome_error` read any of the three
     without importing three separate dataclasses; a non-`None` `.error`
     is re-raised as `PipelineExecutionError`.
   - **Global-batch phases** (5, 6, 7): `supports_single_lecture=False`.
     `run_phase` rejects these outright (decision, Context). `run_all`
     calls `_run_global_batch_phase`, which runs the phase's whole-backlog
     `run()` and inspects the real returned `PackLedgerOutcome`/
     `PackDraftOutcome`/`DraftReviewOutcome` list, filtered to the items
     belonging to `lecture_id` (via `pack_path.parent.name`/
     `draft_path.parent.name`, since none of the three carries a
     `lecture_id` field directly -- two structural Protocols,
     `_PackOutcome` and `_DraftOutcome`, express this without importing
     three separate dataclasses). `_raise_if_incomplete` raises if no item
     belongs to this lecture (no pending input at that stage) or if *any*
     of them failed, naming every failure -- not merely whether the output
     directory has at least one file (see decision 7, a `staff-architect`
     review-board finding on this ADR's first draft: Phases 5-7 isolate
     each pack/draft's own failure rather than raising, so a 9-failed/
     1-succeeded batch would otherwise satisfy a bare directory-non-empty
     check and let an incomplete `CanonicalLectureDocument` reach Phase 8
     as if it were complete).
4. **Per-lecture `queue.json` filtering for Phases 2-4.**
   `ExtractionOrchestrator`/`TranscriptionOrchestrator`/
   `EvidencePackOrchestrator` each require a `queue_path` constructor
   argument (a file listing every `LectureMetadata` to process), not a
   `lecture_id`. `_build_single_lecture_queue` reads the operator's real
   `workspace/data/queue.json` (historically written by the `discover`
   command; now written by `scripts/register_local_lecture.py`, see the
   `discover` removal amendment), filters to the one matching entry, and writes it to a fresh
   temp file the phase action unlinks in a `finally` block after the
   orchestrator run completes -- `run-all`/`run-phase` therefore still
   require the target lecture to have been discovered and enqueued first;
   `PipelineExecutionError` names this precondition explicitly if it was
   skipped.
5. **Filesystem-artifact resume, keyed to each phase's own real output
   path -- exact-file for most phases, item-count comparison for Phases
   5-7, both-files for Phases 11/13/14.** For Phases 2, 3, 8, 9, 12, 15,
   `artifact_exists(lecture_id)` checks the exact single file that
   phase's own module already writes, reusing its own constant per
   decision 2. For **Phases 11, 13, 14** (each of which writes two
   separate files -- `study_guide.md`/`study_guide.html`,
   `exam.json`/`simulado.md`, `script.json`/`script_legivel.md`, each its
   own atomic write), both files are required: a `principal-code-reviewer`
   pass on this ADR's first draft found that checking only the first file
   would resume-skip a lecture whose process was killed between the two
   writes, permanently missing the second file with no error ever raised.
   For **Phases 5-7**, `_phase_5/6/7_artifact_exists` compares this
   lecture's own upstream item count (evidence packs for 5/6, drafts for
   7) against its own downstream item count (ledgers/drafts/units),
   requiring at least as many of the latter as the former -- not merely
   "the output directory has any file", which a `staff-architect` finding
   on this ADR's first draft showed would resume-skip a lecture whose
   prior run only partially succeeded (see decision 3's Global-batch
   bullet for the matching same-run check). `run_all` walks
   `PHASE_REGISTRY` in order, skipping (logging, not erroring) any phase
   whose artifact already exists, and **stops at the first phase that
   raises**: every later phase's own input is an earlier phase's output,
   so continuing past a failure would only ever surface a confusing
   downstream `LectureNotFoundError` instead of the real cause. `run_phase`
   applies the same single check for the one requested phase.
6. **`main()`'s error boundary catches `(RuntimeError, ValueError)`, not
   an enumerated list of domain exception classes.** Every domain
   exception this codebase has ever defined (`LectureNotFoundError`,
   `SemanticLedgersNotFoundError`, `MissingSequenceError`,
   `UnresolvedReviewError`, `MockExamGenerationError`,
   `PodcastGenerationError`, `ScriptNotFoundError`, `AudioGenerationError`,
   `PipelineExecutionError`) is a `RuntimeError` or `ValueError` subclass;
   pydantic's own `ValidationError` (raised for a malformed `lecture_id`,
   e.g. containing `../`) is itself a `ValueError` subclass. Catching both
   bases at this one outermost CLI boundary is a deliberate, narrow
   catch-all -- not a "catch everything and hope" antipattern -- scoped
   precisely to the two base classes this taxonomy already commits to; a
   genuinely unexpected failure (`KeyError`, `TypeError`, an unwrapped
   `OSError`) is explicitly excluded and still surfaces as a full
   traceback rather than being disguised as a clean one-line exit.
7. **`lecture_id` is validated as a `SafeIdentifier` at the top of both
   `run_phase` and `run_all` -- one choke point, not 13.** A
   `security-reviewer` pass on this ADR's first draft found that every
   `_phaseN_artifact_exists` function spliced the raw, argparse-supplied
   `lecture_id` into a `Path` for its resume check *before* any phase's
   own method (each of which already validates `lecture_id` as its own
   first statement, matching every prior phase's convention) ever ran --
   a path-traversal existence oracle (`run-phase 8 '../../../etc'`
   probing `Path.exists()` outside `workspace/data/`), and, worse, a
   traversal value that happened to make `artifact_exists` return `True`
   would mark that phase "skipped" and exit 0 without any phase's own
   guard ever firing. Fixed by calling
   `_LECTURE_ID_ADAPTER.validate_python(lecture_id)` as the first
   statement of both `run_phase` and `run_all`, before `spec.
   artifact_exists` is ever reached for any phase -- closing the gap at
   its single entry point rather than requiring 13 individual
   `artifact_exists` functions to each revalidate independently.
8. **`_run_global_batch_phase` logs how many of a batch run's items
   belonged to other lectures.** Since Phases 5-7 have no per-lecture
   entry point (Context), `run_all` calling their global `run()` also
   advances every other lecture currently pending at that stage in the
   same call -- a `principal-code-reviewer` finding on this ADR's first
   draft noted this reprocessing cost compounds as the operator's lecture
   backlog grows (each new `run-all <lecture>` invocation re-touches
   every already-ledgered/drafted/reviewed pack at that stage, since
   Phases 5-7's own already-shipped orchestrators have no per-pack skip
   of their own) and was previously undisclosed anywhere in this ADR. Not
   fixed structurally (that would mean modifying Phases 5-7's own shipped
   orchestrators, the exact change the Context's operator decision
   already declined) -- instead made visible: `_run_global_batch_phase`
   logs an explicit line naming how many processed items belonged to
   lectures other than the one requested, whenever that count is nonzero.

## Historical rationale

- **Why a registry of `PhaseSpec` entries instead of one large
  `if phase_number == N` chain.** 13 phases means 13 near-identical
  "check artifact, run action, log" bodies; a data-driven registry keeps
  `run_all`/`run_phase`'s own logic (the actually-interesting resume/
  reject/stop-on-failure behavior) in exactly one place each, walked
  generically, instead of duplicated across 13 branches.
- **Why `_LectureOutcome` is a `Protocol`, not three separate `isinstance`
  branches or a shared base class.** Phases 2-4's three `Outcome`
  dataclasses were each already shipped (Phases 2, 3, 4) with an identical
  `lecture_id`/`error` shape but no common base -- retrofitting one now
  would touch three already review-board-approved modules for a Phase 16
  convenience. A structural `Protocol` gets the same one-function
  handling without editing any of them.
- **Why Phase 1 is not in `PHASE_REGISTRY`.** `LectureDiscovery.
  discover_lectures` returns every candidate lecture unconditionally (no
  `lecture_id` filter) and is inherently interactive (`questionary.
  checkbox` in `prompt_lecture_selection`) -- there is no artifact to
  check existence of, and no way to script "discover just this one
  lecture". `run-all`/`run-phase` both treat "already discovered and
  enqueued" as a precondition the operator satisfies via the pre-existing
  `discover` command, not a step they automate.
- **Why Phase 10 is not in `PHASE_REGISTRY`.** `.prompts/fases10_e_11_
  publishing.md` and ADR 0011 deliver Phases 10 and 11 together as one
  unit: `MarkdownCompiler.compile` (the "Phase 10" Markdown-generation
  step) is a pure function with no persistence of its own, called
  directly inside `PublishingOrchestrator.publish_lecture` (Phase 11),
  which is the only artifact-writing entry point that actually exists on
  disk. There is no standalone "Phase 10" callable to register.

## Consequences

- `domain/errors.py` gains `PipelineExecutionError`; its module docstring
  is updated to list Phase 16's addition, per this file's established
  "docstring is the authoritative site list" convention.
- `cli/main.py` keeps every Phase 1 function (`render_lecture_table`,
  `prompt_lecture_selection`, `save_queue`, `run_cli`) and its existing
  test coverage (`tests/test_cli.py`) unchanged; `main()` gains an
  `argv: list[str] | None = None` parameter (`None` still defaults to
  `sys.argv`, matching `argparse.ArgumentParser.parse_args`'s own
  convention) so tests can drive it without touching real process
  arguments. `QUEUE_PATH` moves to being defined once in
  `cli/orchestrator.py` and imported into `cli/main.py`, rather than
  declared separately in both -- the two modules live in the same `cli`
  package (not a sibling-phase boundary ADR 0006 governs), so keeping two
  literal copies of the same path would be pure duplication.
- (Historical; removed, see ADR 0044.) `python -m lecture_study.cli.main
  discover` reproduced the pre-existing Phase 1 flow exactly at the time. `python -m lecture_study.cli.main run-all
  <lecture_id>` and `run-phase <phase_number> <lecture_id>` are new.
- `cli/` (both `main.py` and `orchestrator.py`) reaches 100% statement
  coverage with every phase's own class mocked via `unittest.mock`/
  `monkeypatch` (no real subprocess, LLM inference, or filesystem I/O
  beyond `tmp_path`-scoped fixtures) plus `argparse`-level dispatch tests
  driving `main(argv=[...])` end to end, including the `(RuntimeError,
  ValueError)` boundary's exit-code-1 path. The pre-existing, never-
  covered `if __name__ == "__main__":` guard is now marked
  `# pragma: no cover` explicitly rather than left as a silent gap.
- **Accepted risk: no cross-process lock on Phases 5-7's Metal-bound
  inference.** ADR 0003/0005 already document `concurrency_limit=1`
  *within* one orchestrator run because concurrent llama.cpp/Metal
  inference risks a kernel panic or OOM on the target Apple Silicon
  hardware. Phase 16 is the first module making `run-all`/`run-phase` a
  scriptable, loopable CLI entry point (e.g. two terminal sessions each
  running `run-all` for a different lecture at once) -- a `staff-architect`
  finding on this ADR's first draft flagged that nothing here prevents
  two concurrent CLI processes from both invoking
  `SemanticOrchestrator(...).run()` simultaneously. Accepted as a known
  V1 risk rather than addressed with a lock/PID-file mechanism, matching
  ADR 0010's own risk-acceptance precedent in this codebase: `DIRETRIZES_
  ARQUITETURA.txt`'s single-operator, strictly-local usage model makes
  concurrent invocation an operator-discipline concern (do not run two
  pipeline commands at once), not a Phase 16 feature requirement.

## Historical alternatives considered

- **Add a `run_for_lecture(lecture_id)` method to Phases 5-7's
  orchestrators.** Considered as the structurally "more correct" fix
  (true per-lecture scoping for `run-phase 5/6/7`). Rejected per the
  operator's explicit decision (Context): it would modify three already-
  shipped, review-board-approved phases for a Phase 16 convenience the
  instruction never asked for, and `run-all`'s own need (process this
  lecture completely) is already met by calling the existing global
  `run()` once.
- **Run the global batch and filter output for `run-phase 5/6/7
  <lecture_id>`.** Rejected: this would silently burn local LLM inference
  time on every other lecture currently queued at that stage merely
  because the operator asked to run one, and the command's own name would
  no longer describe what it actually does.
- **One flat `cli.py` module, as the instruction's literal
  `src/lecture_study/cli.py` path suggests.** Infeasible as written: a
  `lecture_study/cli/` package already exists (Phase 1), and a module and
  a package cannot share one import path. The instruction's own
  parenthetical ("ou expanda o entrypoint existente") is read as
  endorsing exactly this: expanding the existing package instead of a
  literal new top-level file.

## 2026-07-28 explicit model-family amendment

The LLM-backed phase boundary now accepts `--llm-model-family qwen2|qwen3`.
Qwen2 remains the compatibility default. Qwen3 must be selected explicitly;
the prompt renderer adds `/no_think`, and exact token-budget preflight rejects
the invocation when the declared family differs from the GGUF architecture.
The selected family is threaded through the composition root, phase
orchestrator, worker prompt renderer, and tokenizer preflight. It is not
inferred from the model filename.

This option controls prompt compatibility only. It does not authenticate an
actor, establish model independence, satisfy calibration, or grant clinical
production authority.
