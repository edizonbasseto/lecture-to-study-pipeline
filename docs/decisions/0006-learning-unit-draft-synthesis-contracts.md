# 0006. Learning Unit draft synthesis contracts: LearningUnitDraft schema, two-artifact grounding, and the accepted residual hallucination risk

## Status

Accepted. **Superseded by ADR 0038** as to the local execution route: drafting now runs scripts/generate_codex_drafts.py; the local qwen3-32b/llama.cpp worker this ADR specified is deleted. The erratum below is historical. **Erratum (ADR 0020):** the llama-cli invocation example below
references `--json-schema`, a flag name confirmed wrong running the real
pipeline end to end -- the correct flag is `--json-schema-file`, and the
call site now also passes `-n` to cap completion length. See ADR 0020 for
the corrected contract and the incident that found it. Every other
decision recorded here is unaffected.

**Erratum (ADR 0022):** `--json-schema-file` itself was subsequently
dropped entirely (this llama-cli build's grammar sampler fails to
initialize on any structured-output request, a known upstream defect) in
favor of embedding the schema as prompt text plus post-hoc parse/validate
with a single repair retry, distinct from and composing with this ADR's
critic-rejection `feedback` mechanism. See ADR 0022 for the full incident
and rationale.

## Context

Phase 6 (`generate_draft`, `src/lecture_study/drafting/llm_worker.py`;
`DraftOrchestrator`, `src/lecture_study/drafting/pipeline.py`) turns each
Phase 4 `EvidencePack`, grounded in its matching Phase 5 `SemanticLedger`,
into a `LearningUnitDraft` first-draft study guide section via a local
llama.cpp (Metal) inference call. The originating instruction
(`.prompts/fase6_drafting.md`) states the phase's "Regra de Ouro" (Golden
Rule) explicitly: "A IA não pode inventar dados clínicos que não estejam
no Ledger" (the AI must not invent clinical data absent from the Ledger).
Phase 6 is the first phase in this codebase to consume two upstream
handoff artifacts (`EvidencePack` from Phase 4, `SemanticLedger` from
Phase 5) instead of one, and it deliberately mirrors Phase 5's worker and
orchestrator shape (ADR 0005) almost 1:1, per this repo's established
convention that each phase module is self-contained (imports only from
`domain.models`, never a sibling phase module).

## Decisions

1. **`LearningUnitDraft` domain model.** Added to `domain/models.py`
   immediately after `EvidencePack`: `lecture_id: SafeIdentifier`,
   `pack_sequence: int = Field(ge=1)`, `title: str`, `body_markdown: str`,
   `clinical_warnings: list[str] = Field(default_factory=list)`,
   `model_config = ConfigDict(strict=True)`. `clinical_warnings` defaults
   to an empty list for the same reason `SemanticLedger.facts` does
   (ADR 0005 decision 1): a draft grounded in a pack with no extracted
   clinical facts is a valid, not an error, outcome.
2. **Grammar-constrained decoding against the full container schema.**
   `generate_draft` passes `LearningUnitDraft.model_json_schema()` to
   llama.cpp's `--json-schema` flag and prompts the LLM to echo
   `lecture_id`/`pack_sequence` verbatim, mirroring ADR 0005 decision 2.
   The command is `llama-cli -m {model_path} -c 8192 --temp 0.2
   --json-schema {schema_path} -f {prompt_path}` per the originating
   instruction's literal example (a larger context window and a small
   positive temperature than Phase 5's `-c 4096 --temp 0.0`, since Phase 6
   generates long-form Markdown prose rather than extracting short
   structured facts).
3. **Two-artifact grounding: the prompt embeds both the Evidence Pack and
   the Semantic Ledger.** `_build_prompt` includes the pack's slide OCR
   text and transcript segments (the raw evidence) *and* a rendered list
   of the ledger's `SemanticFact`s (the "authoritative facts, do not add
   others"), with an explicit instruction not to invent clinical data
   absent from the Ledger. This is the phase's primary, but non-structural,
   defense against hallucination — see decision 6.
4. **Traceability guard on echoed keys, plus a second guard on the loaded
   ledger's own content.** `_validate_traceability` (mirroring ADR 0005
   decision 3) checks the LLM's echoed `lecture_id`/`pack_sequence` against
   the source `EvidencePack`. Additionally, `DraftOrchestrator._load_ledger`
   checks the *loaded* `SemanticLedger`'s own `lecture_id`/`pack_sequence`
   against the `EvidencePack` it was fetched for, raising `ValueError` on a
   mismatch. `_load_ledger`'s path is built from the pack's own validated
   fields, so this is not a path-traversal risk, but the file's *content*
   is untrusted input (a stale rerun, a manual repair, a future discovery
   change) that must not be silently folded into the prompt as another
   pack's authoritative clinical facts. This closes a gap the first
   review-board round found: the traceability discipline applied to LLM
   *output* had no symmetric check on the ledger *input*.
5. **Single domain exception per worker, isolated at pack granularity.**
   `LlmParsingError` is the sole failure type `generate_draft` raises
   (missing binary, non-zero exit, no/malformed JSON, a schema-violating
   or mistraced hallucination), matching ADR 0005 decision 4.
   `DraftOrchestrator._process_pack` catches `Exception` broadly around
   pack loading, ledger loading (including decision 4's new content guard),
   and draft generation together: any of a corrupted pack file, a missing
   ledger, a mistraced ledger, or an LLM failure isolates that one pack via
   `PackDraftOutcome.error`, matching ADR 0005 decision 8's per-pack (not
   per-lecture) isolation granularity.
6. **Accepted residual risk: no structural, content-level grounding check
   on `clinical_warnings`/`body_markdown`.** The Golden Rule's "no invented
   clinical data" requirement is enforced only via decision 2's
   grammar-constrained decoding (shape) and decision 3's prompt instruction
   (behavior), not via a runtime check that every `clinical_warnings` entry
   or `body_markdown` claim actually traces back to a `SemanticLedger.facts`
   entry. A well-formed, correctly-traced `LearningUnitDraft` whose prose
   contains a fabricated clinical claim would currently be persisted
   undetected. A lexical-overlap heuristic (e.g. rejecting a
   `clinical_warnings` entry with no token overlap against any fact's
   `entity_name`/`clinical_context`) was considered and rejected: free-text
   clinical writing legitimately paraphrases source facts, so such a check
   would produce false rejections at least as often as it catches real
   hallucinations, trading a silent risk for a noisy, unreliable one. This
   mirrors this pipeline's broader posture (`DIRETRIZES_ARQUITETURA.txt`
   §6: "Falhas de extração JSON do LLM acionam retry automático... guiado
   pela tipagem estrita do Pydantic") of enforcing shape strictly and
   trusting grammar-constrained decoding plus prompt discipline for
   content — the same posture Phase 5 takes for `SemanticFact` extraction.
   Human review of generated drafts against their source `SemanticLedger`
   remains the closing control for this residual risk; a future phase may
   add an automated grounding check if this proves insufficient in
   practice.
7. **Orchestrator discovers packs by directory scan, reusing Phase 5's
   filtering, and loads the ledger by filename convention.** Like
   `SemanticOrchestrator` (ADR 0005 decision 7), `DraftOrchestrator.run()`
   walks `EVIDENCE_PACKS_INPUT_DIR` for `pack_*.json` under subdirectories
   whose name is a valid `SafeIdentifier`, excluding Phase 4's
   `.staging`/`.previous` crash-recovery leftovers (ADR 0004 Repair Loop
   item (c)). `_load_ledger` derives the ledger path
   (`{SEMANTIC_LEDGERS_INPUT_DIR}/{pack.lecture_id}/ledger_{pack.pack_sequence:03d}.json`)
   from the pack's own validated fields, matching exactly how
   `SemanticOrchestrator._save_ledger` wrote it — the filename convention
   is the real contract boundary between Phase 5 and Phase 6, not any
   private detail of `semantic/pipeline.py`. A pack that reached Phase 4
   but has no matching ledger on disk yet (Phase 5 has not processed it) is
   an isolated per-pack failure (decision 5), not a systemic error: Phase 6
   can safely run before Phase 5 has finished a lecture, consistent with
   ADR 0005's "partial run is valid" precedent.
8. **Atomic single-file write per pack.** `DraftOrchestrator._save_draft`
   writes `draft_NNN.json` via a `tmp`-then-`replace` atomic swap, matching
   ADR 0005 decision 9's per-pack (not per-lecture) atomicity rationale for
   the identical reason: Phase 6 processes packs independently, so there is
   no "whole lecture's draft set" to swap atomically.

## Rationale

Decisions 1-2 apply the originating instruction's explicit schema and
command requirements. Decisions 3-6 are the phase's core content-quality
contract: grounding the LLM in both upstream artifacts, checking
traceability symmetrically on both output and newly-untrusted ledger
input, and explicitly recording — rather than silently accepting — the
Golden Rule's residual enforcement gap. Decisions 7-8 adapt ADR 0005's
established Phase 5 orchestrator pattern to Phase 6's two-artifact
discovery model.

## Consequences

- A future phase reading Phase 6 output should expect
  `data/08_drafts/{lecture_id}/draft_{sequence:03d}.json`, produced
  independently per pack; a partial run leaves some `draft_NNN.json` files
  present and others (still-failing or not-yet-ledgered packs) absent, by
  design.
- `LearningUnitDraft.clinical_warnings`/`body_markdown` content is not
  automatically verified against `SemanticLedger.facts` at runtime;
  reviewing generated drafts against their source ledger before they reach
  a learner remains a human responsibility until/unless a future phase adds
  an automated grounding check (decision 6).
- `DraftOrchestrator` has no `queue.json`-driven duplicate-lecture-id
  guard, matching `SemanticOrchestrator`'s precedent (ADR 0005
  Consequences): discovery reads directly from Phase 4/5's own output
  trees, so there is no queue input to validate.

## Alternatives Considered

- **Trust the loaded `SemanticLedger`'s content without a traceability
  check, relying only on the filename convention** — rejected: the
  filename convention holds under normal same-pipeline provenance but is
  not itself an enforced boundary, matching the exact reasoning ADR 0005
  decision 3/Repair Loop item (c) already applied to `_save_ledger`'s
  `lecture_id` argument; a silent mismatch here would blend one pack's
  drafted content with another pack's clinical facts.
- **Add a lexical-overlap grounding check rejecting unsupported
  `clinical_warnings` entries** — rejected for now (decision 6): free-text
  paraphrase makes token-overlap an unreliable signal, and a false-reject
  failure mode is not obviously safer than the current false-accept one:
  both require human review of generated content before publication.
- **Bound concurrency above 1** — rejected for the same Metal-contention
  reason as Phase 3/5 (ADR 0003, ADR 0005 Alternatives Considered):
  long-form Markdown generation occupies the model's entire context
  window, so concurrent llama.cpp invocations risk a kernel panic or an
  out-of-memory crash on the M5 Pro's unified memory.
