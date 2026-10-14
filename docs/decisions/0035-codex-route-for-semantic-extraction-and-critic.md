# 0035. Codex CLI replaces local qwen3-32b for semantic extraction (Phase 5)
and critic review (Phase 7)

## Status

Accepted. **Superseded by ADR 0038** as to dual-route reachability: this ADR's "local route stays reachable" clause no longer holds -- the local workers are deleted and these Codex routes are the only ones, now orchestrated as child processes.

## Context

ADR 0030 already moved Phase 6 (learning-unit drafting) and the mock-exam
generator from the local `qwen3-32b`/`llama.cpp` route to the Codex CLI
(`codex exec`, billed through the operator's ChatGPT Pro subscription, not
the OpenAI API) because local drafting quality was rejected. That ADR left
Phase 5 (semantic extraction, `semantic/llm_worker.py`) and Phase 7 (critic
review, `critic/llm_worker.py`) on the local route, with no ADR recording
why -- an undocumented gap surfaced directly by the operator during a
later batch of lectures (10004/10005/10006).

Running Phase 5 against 10006's evidence pack 12 surfaced a concrete,
reproducible defect in the local route. The raw `llama-completion` output
for that pack (captured directly, not inferred) ends its facts array with:

```json
{
    "entity_name": "'Formiga andando' – descrição visual clássica ...",
    "category": "tecnica_ultrasonografica_torax_veterinario",
    "临床上下文": "Este sinal ajuda diferenciar ...",
    "timestamp_ref": 5192.0,
    "correction_needed_for_language_consistency": true // This field is not valid in the schema; it's an error.
}
```

Two distinct model-behavior defects appear in the same fact: the model
substitutes a Chinese key (`临床上下文`) for the schema's declared
`clinical_context`, and it self-corrects mid-generation by appending a
JavaScript-style `//` comment, which is not valid JSON. The strict parser
(`ops/llm_response_contract.py`) has no repair heuristics by design (see
ADR 0022/0034's own reasoning) and correctly rejects this output; there is
no fix on the parser side that does not reintroduce the repair-heuristic
risk that design deliberately avoids.

This reproduced byte-for-byte identically across three full-lecture Phase
5 reruns (`sha256=7a312fd1...`, 1627 bytes, same "Expecting ':' delimiter
at line 26, column 9" error each time) despite the retry attempt's
feedback text differing slightly between runs (`build_llm_retry_feedback`
is category-based, not a raw-output replay) -- confirming this is a
deterministic, content-triggered model defect on this specific pack, not
llama.cpp/Metal sampling noise (the non-determinism ADR 0034 and the
2026-09-16 handoff describe, which affected different packs on different
reruns of the same lecture).

## Decision

1. **Phase 5 and Phase 7 move to the Codex CLI route**, following ADR
   0030's exact pattern: one `codex exec` call per pack/draft, prompt
   self-contained (facts/transcript inlined as text), `--sandbox
   read-only`, scratch `cwd` never the repository checkout, shared
   `ops.llm_response_contract` parser and `build_llm_retry_feedback`
   retry vocabulary, one retry on a parse/schema failure.

2. **New scripts, not a CLI/orchestrator rewire**:
   `scripts/generate_codex_semantic_ledgers.py` (Phase 5) and
   `scripts/generate_codex_critic_review.py` (Phase 7), mirroring
   `generate_codex_mock_exam.py`/`generate_codex_drafts.py`'s own
   structure exactly. `SemanticOrchestrator`/`CriticOrchestrator` (the
   local llama.cpp orchestrators) are left in place, untouched, exactly as
   ADR 0030 left the certified local exam route (`exams/generator.py`)
   reachable -- the local route remains available, just no longer the one
   this milestone's personal-study batches use.

3. **Extraction/critic instruction text is imported verbatim from the
   local workers** (`semantic.llm_worker._EXTRACTION_INSTRUCTION`,
   `critic.llm_worker._CRITIC_INSTRUCTION`), matching
   `generate_codex_drafts.py`'s own precedent of importing
   `drafting.llm_worker._DRAFT_COVERAGE_CATEGORIES` across module
   boundaries rather than duplicating the wording -- the extraction and
   review rules apply identically regardless of inference backend.

4. **Phase 7's repair loop (redraft-on-rejection) is explicitly OUT of
   scope for this decision.** `generate_codex_critic_review.py` produces
   one `CriticReview` per draft and persists the `ValidatedLearningUnit`
   as-is; it does not call back into drafting to regenerate a rejected
   draft the way `CriticOrchestrator._evaluate_with_repair` does locally.
   A rejected draft under the Codex route is left flagged
   (`review.passed is False`) for a human decision, not auto-repaired.
   Wiring an automatic Codex-based repair loop is a separate decision,
   not bundled into this one.

## Alternatives considered

1. **Patch the local route's prompt to forbid comments/non-English
   keys explicitly, keep qwen3-32b.** Rejected by the operator directly:
   asked "por que esse modelo se mudamos para o Codex" once told the
   defect was model-specific -- the operator's own standing preference
   (`2026-09-quality-over-cost-preference.md`: compute cost is not a
   constraint, quality is) already answers this in Codex's favor, and a
   prompt patch does not guarantee the defect does not recur on different
   content.
2. **Keep Phase 5/7 local, migrate only the specific failing pack
   manually.** Rejected: solves this one instance, not the class of
   defect; the operator explicitly asked for the broader migration
   ("Fase 5 e 7 juntas") once the gap was surfaced.
3. **Migrate Phase 5 only, leave Phase 7 local for now.** Considered and
   presented to the operator directly; the operator chose the wider
   scope (both phases together) over the smaller one.

## Consequences

- **Positive:** closes the pack-12-class defect for both phases at once;
  both phases now share the same billing model (ChatGPT Pro subscription)
  and the same Codex invocation contract already proven by ADR 0030's
  drafting/exam routes.
- **Positive:** documents explicitly, for the first time, why Phase 5/7
  stay local vs move to Codex -- closing the exact undocumented-decision
  gap that triggered this ADR.
- **Negative, accepted:** Phase 7's automatic repair loop is not available
  under the Codex route (see Decision 4) until a follow-up decision wires
  one; a rejected draft needs a human call in the meantime.
- **Negative, accepted:** each `codex exec` call is a real external
  subprocess with no automated test exercising it directly (same
  limitation ADR 0030 already accepts for the exam route); pure logic
  (prompt building, parsing, retry) is unit-tested with a stubbed Codex
  call instead.
- **Neutral:** the local llama.cpp Phase 5/7 orchestrators
  (`SemanticOrchestrator`, `CriticOrchestrator`) and their workers stay in
  the repository, unmodified and still reachable via `run-phase 5`/`7`,
  for any future case where the local route is preferred again.
