# 0007. Workspace migration and the Critic & Repair Loop: `workspace/data/` root, `CriticReview`/`ValidatedLearningUnit` contracts, and feedback-driven regeneration

## Status

Accepted. **Superseded by ADR 0038** as to the local execution route: the repair loop lives on in scripts/generate_codex_critic_review.py (ADR 0036); the local qwen3-32b/llama.cpp worker this ADR specified is deleted. The erratum below is historical. **Erratum (ADR 0020):** the llama-cli invocation example below
references `--json-schema`, a flag name confirmed wrong running the real
pipeline end to end -- the correct flag is `--json-schema-file`, and the
call site now also passes `-n` to cap completion length. See ADR 0020 for
the corrected contract and the incident that found it. Every other
decision recorded here is unaffected.

**Erratum (ADR 0022):** `--json-schema-file` itself was subsequently
dropped entirely (this llama-cli build's grammar sampler fails to
initialize on any structured-output request, a known upstream defect) in
favor of embedding the schema as prompt text plus post-hoc parse/validate.
`evaluate_draft` also gained its own single repair retry on parse
failure, on top of this ADR's feedback-driven Repair Loop retry. See ADR
0022 for the full incident and rationale.

## Context

Phases 1-6 wrote every execution artifact under a `data/` directory at the
project root (`data/01_raw` through `data/08_drafts`, plus
`data/queue.json`). `git` never tracks an empty directory regardless of
`.gitignore` state, and this repository never commits real run output, so
`data/` existed only as an untracked, empty scaffold on disk -- not as a
tracked-but-empty placeholder. The originating instruction
(`.prompts/fase7_critic_and_workspace.md`) states two objectives for
Phase 7: isolate all execution data from the project root, and add a
rigorous content-review step — a local-LLM "Critic" that checks each
Phase 6 `LearningUnitDraft` against its source `EvidencePack` and
`SemanticLedger`, with a bounded Repair Loop that resends the critic's
feedback to Phase 6's drafting worker for regeneration on failure.

## Decisions

1. **All execution data moves under `workspace/data/`, and `workspace/` is
   gitignored.** Every phase's directory constant (`RAW_VIDEO_DIR`,
   `AUDIO_OUTPUT_DIR`, `SLIDES_OUTPUT_DIR`, `CANONICAL_OUTPUT_DIR`,
   `CANONICAL_INPUT_DIR`, `TRANSCRIBED_OUTPUT_DIR`,
   `TRANSCRIBED_INPUT_DIR`, `EVIDENCE_PACKS_OUTPUT_DIR`,
   `EVIDENCE_PACKS_INPUT_DIR`, `SEMANTIC_LEDGERS_OUTPUT_DIR`,
   `SEMANTIC_LEDGERS_INPUT_DIR`, `DRAFTS_OUTPUT_DIR`, `QUEUE_PATH`) is
   re-rooted from `data/...` to `workspace/data/...`, and the pre-existing
   `data/` directory tree is relocated to `workspace/data/` unchanged
   (`01_raw` through `07_output_pdf`). No test needed a code change beyond
   the constant's default value: every orchestrator test already
   `monkeypatch`s these module-level `Path` constants to a `tmp_path`
   fixture rather than touching the real default, matching the pattern
   established since ADR 0002. `tests/test_workspace_paths.py` pins each
   constant's new default so a future regression restoring the old root is
   caught directly, without depending on an orchestrator test's
   `monkeypatch` to incidentally notice.
2. **`CriticReview` domain model.** Added to `domain/models.py`
   immediately after `LearningUnitDraft`: `passed: bool`,
   `hallucination_score: float = Field(ge=0.0, le=1.0)`, `feedback: str`,
   `model_config = ConfigDict(strict=True)`, per the originating
   instruction's literal field list. `feedback` is unconditionally
   required (no default): it must always carry the reviewer's reasoning,
   since a passing review's `feedback` documents the grounding rationale
   and a failing one becomes the next `generate_draft` call's correction
   instruction (decision 5).
3. **`ValidatedLearningUnit` domain model.** Added immediately after
   `CriticReview`: `lecture_id: SafeIdentifier`, `pack_sequence: int =
   Field(ge=1)`, `content: LearningUnitDraft`, `review: CriticReview`,
   `model_config = ConfigDict(strict=True)`. `lecture_id` uses
   `SafeIdentifier` rather than a bare `str` (the originating
   instruction's literal wording), consistent with every other
   lecture-identifying field in this domain since ADR 0002 decision 6 —
   the value is spliced into `VALIDATED_UNITS_OUTPUT_DIR / lecture_id` on
   save (decision 7), the same filesystem-splicing hazard `SafeIdentifier`
   exists to close.
4. **`evaluate_draft`, `critic/llm_worker.py`.** Mirrors
   `drafting/llm_worker.py`'s `generate_draft` shape almost 1:1 (temp-file
   prompt/schema, `asyncio.create_subprocess_exec`, balanced-brace
   candidate scan against `CriticReview.model_json_schema()`), per this
   repo's established convention that each phase module is self-contained
   (ADR 0006 context). The one deliberate divergence:
   `llama-cli -m {model_path} -c 8192 --temp 0.0 --json-schema
   {schema_path} -f {prompt_path}` — `--temp 0.0`, not Phase 6's `0.2`,
   because the originating instruction's Regra de Ouro demands "temperatura
   0.0, determinismo máximo" for the critic specifically: a Repair Loop
   deciding whether to retry drafting needs a stable verdict across
   otherwise-identical inputs, not sampling noise. There is no
   `_validate_traceability` step (unlike `generate_draft`/`extract_ledger`):
   `CriticReview` carries no `lecture_id`/`pack_sequence` fields for the
   LLM to echo, so `CriticOrchestrator` (decision 6) is solely responsible
   for keeping a review paired with the draft it was actually run against.
5. **`critic/pipeline.py` imports `drafting/llm_worker.generate_draft`
   directly — a new, narrow exception to ADR 0006's self-containment rule,
   not an application of existing precedent.** ADR 0006 states that rule
   unqualified: "each phase module is self-contained (imports only from
   `domain.models`, never a sibling phase module)". No prior phase module
   crosses that boundary; `critic/pipeline.py` is the first. This ADR
   deliberately introduces the exception rather than extending it silently,
   because the Repair Loop has no substitute: regenerating a draft under
   critic feedback must run through the exact same grounding/traceability
   guarantees (`_validate_traceability` against `pack`, the same JSON
   schema, the same balanced-brace parse) `generate_draft` already
   provides, and duplicating that function into `critic/llm_worker.py` for
   one extra prompt branch would fork a second copy of Phase 6's own
   contract logic — a correctness hazard the self-containment rule was
   never meant to trade `_load_pack`/`_load_ledger`-style cross-artifact
   reads (already routine at the orchestrator layer, e.g.
   `DraftOrchestrator._load_ledger` reading Phase 5's output) for. The
   import is scoped to a single function at the orchestrator layer only —
   `critic/llm_worker.py` itself still imports nothing outside
   `domain.models`, matching every other worker module.
   `feedback: str | None = None` (rather than a new `regenerate_draft`
   function) keeps that one call site trivial: `feedback=None` reproduces
   Phase 6's original behavior exactly, so this phase adds no new code path
   for `DraftOrchestrator`'s own callers to reason about.
6. **`CriticOrchestrator`, `critic/pipeline.py`, concurrency 1.** Mirrors
   `DraftOrchestrator`'s discovery/isolation/save shape (ADR 0006 decision
   7): walks `DRAFTS_INPUT_DIR` for `draft_*.json` under
   `SafeIdentifier`-validated lecture directories, loads the matching
   `EvidencePack`/`SemanticLedger` pair with the same untrusted-content
   traceability guard `DraftOrchestrator._load_ledger` established, and
   isolates each draft's failure into a `DraftReviewOutcome` (parallel to
   `PackDraftOutcome`) so one malformed file or one LLM hallucination
   never aborts the rest of the run. Concurrency defaults to 1 per the
   originating instruction's explicit Regra de Ouro ("Concorrência
   `Semaphore(1)` para inferência"): both `evaluate_draft` and the repair
   call to `generate_draft` occupy the full context window of the same
   local model process ADR 0005/0006 already constrain to single-flight.
7. **The Repair Loop: bounded regenerate-then-re-evaluate, always
   persisted.** `_evaluate_with_repair` evaluates the original draft once,
   then — while the review keeps failing and the attempt count is below
   `max_retries` (default 2) — calls `generate_draft` with the rejecting
   `feedback`, re-evaluates the new draft, and increments the attempt
   counter. The loop's last `(draft, review)` pair is always persisted as
   a `ValidatedLearningUnit` under
   `VALIDATED_UNITS_OUTPUT_DIR/{lecture_id}/unit_{pack_sequence:03d}.json`
   (`tmp`-then-`replace` atomic write, matching every prior phase's
   convention) via `DraftReviewOutcome.succeeded = True`, whether the
   final review passed or retries were exhausted while still failing: an
   exhausted-but-persisted unit stays traceable for manual follow-up,
   where discarding it would silently erase which drafts need
   `hallucination_score`/`feedback`-guided human review.
8. **Regenerated intermediate drafts are not written back to
   `DRAFTS_OUTPUT_DIR`.** Only the loop's final draft is persisted, inside
   the `ValidatedLearningUnit.content` field — an interim rejected
   attempt has no independent value once superseded, and overwriting
   Phase 6's own `draft_NNN.json` from a different orchestrator would
   blur which phase owns that file's write path.

## Rationale

Decision 1 satisfies the instruction's isolation objective with the
minimal change every existing orchestrator test already tolerates: only
the constants' literal default values move, because tests were already
decoupled from the default via `monkeypatch` + `tmp_path`. Decisions 2-3
translate the instruction's literal field lists into this domain's
existing conventions (`SafeIdentifier`, `ConfigDict(strict=True)`) rather
than a new pattern. Decisions 4-5 keep the critic worker inside the
established one-module-per-phase, no-cross-imports discipline while
avoiding contract duplication for the repair path specifically. Decisions
6-8 adapt ADR 0006's orchestrator shape to a three-upstream-artifact
(draft + pack + ledger) discovery model and encode the Repair Loop's exact
termination and persistence semantics, which the originating instruction
states only at the level of "até max_retries" / "se aprovado (ou esgotar
retries), salva".

## Consequences

- A future phase reading Phase 7 output should expect
  `workspace/data/09_validated_units/{lecture_id}/unit_{sequence:03d}.json`,
  produced independently per draft; `review.passed = False` on a persisted
  unit is a valid, not an error, outcome once `max_retries` is exhausted,
  and downstream consumers must check it explicitly rather than assuming
  every persisted unit is print-ready.
- Every phase's default data root changed in this commit
  (`data/...` -> `workspace/data/...`); any local script, notebook, or
  manual run instruction outside this repository's own code/tests/README
  that still references the old `data/` root must be updated separately —
  this ADR and the migration it documents cover only what this repository
  tracks.
- `evaluate_draft` has no analogous `feedback` input (a critic reviewing
  its own critique is out of scope); only `generate_draft` gained the
  parameter.
- Historical ADRs 0002-0006 are left referencing the old `data/...` paths
  unchanged, since they document decisions as made at the time; only
  living documentation (`README.md`, `DIRETRIZES_ARQUITETURA.txt`, and the
  code/tests themselves) was updated to the new root.

## Alternatives Considered

- **A shared `llm_worker_base` module factoring out `_write_temp_file`,
  `_start_llama_process`, `_iter_balanced_object_spans`.** Rejected: this
  repo has carried this exact duplication across `semantic/llm_worker.py`
  and `drafting/llm_worker.py` since ADR 0005/0006 by deliberate
  "isolamento de falhas por pacote" — each phase module self-contained,
  no shared runtime coupling a future phase change could destabilize
  across modules. `critic/llm_worker.py` follows the same precedent rather
  than introducing a shared dependency this phase's instruction did not
  ask for.
- **Re-evaluate the regenerated draft with the same passed/failed verdict
  trusted without a second `evaluate_draft` call, to save one inference
  pass per repair attempt.** Rejected: a regenerated draft is a fresh LLM
  output with no guarantee the correction actually landed; skipping
  re-evaluation would let a still-hallucinating regeneration through
  unchecked, defeating the Repair Loop's purpose.
- **Uncap the Repair Loop (retry until passed).** Rejected: the
  originating instruction specifies `max_retries: int = 2` explicitly, and
  an unbounded loop against a local model with a documented residual
  hallucination risk (ADR 0006 Consequences) could spin indefinitely on a
  draft the critic will never accept.
