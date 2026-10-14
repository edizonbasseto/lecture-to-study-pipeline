# 0005. Semantic ledger extraction contracts: SemanticLedger container schema, llama.cpp grammar contract, and orchestrator discovery model

## Status

Accepted. **Superseded by ADR 0038** as to the local execution route: the extraction contracts live on (relocated to ops/llm_instructions.py and enforced by scripts/generate_codex_semantic_ledgers.py), but the local qwen3-32b/llama.cpp worker this ADR specified is deleted. The erratum below is historical. **Erratum (ADR 0020):** the llama-cli invocation examples below
reference `--json-schema`, a flag name confirmed wrong running the real
pipeline end to end -- the correct flag is `--json-schema-file`, and every
call site now also passes `-n` to cap completion length. See ADR 0020 for
the corrected contract and the incident that found it. Every other
decision recorded here is unaffected.

**Erratum (ADR 0022):** `--json-schema-file` itself was subsequently
dropped entirely (this llama-cli build's grammar sampler fails to
initialize on any structured-output request, a known upstream defect) in
favor of embedding the schema as prompt text plus post-hoc parse/validate
with a single repair retry. See ADR 0022 for the full incident and
rationale.

## Context

Phase 5 (`extract_ledger`, `src/lecture_study/semantic/llm_worker.py`;
`SemanticOrchestrator`, `src/lecture_study/semantic/pipeline.py`) turns
each Phase 4 `EvidencePack` into a `SemanticLedger` of clinical facts via
a local llama.cpp (Metal) inference call. The originating instruction
(`.prompts/fase5_semantic_ledger.md`) specified `extract_ledger(...) ->
SemanticLedger`, but the Phase 1 `SemanticLedger` scaffold modeled a
single clinical entity (`entity_name`, `category`, `clinical_context`,
`timestamp_ref`), while the instruction's own text calls for extracting
"fatos" (facts, plural) per pack. A follow-up instruction
(`.prompts/fase5_schema_refactor.md`) resolved this gap explicitly and is
recorded here alongside the extraction and discovery decisions it drove.

## Decisions

1. **`SemanticLedger` container restructure.** The Phase 1 scaffold's
   single-entity model is renamed `SemanticFact` (unchanged fields:
   `entity_name`, `category`, `clinical_context`, `timestamp_ref`,
   `model_config = ConfigDict(strict=True)`). A new `SemanticLedger`
   becomes the Phase 5 handoff artifact: `lecture_id: SafeIdentifier`,
   `pack_sequence: int = Field(ge=1)`, `facts: list[SemanticFact] =
   Field(default_factory=list)`. This is a breaking change to a domain
   model with no external consumers yet (Phase 6+ does not exist), so it
   was applied in place rather than versioned, matching ADR 0004 decision
   2's precedent for `EvidencePack`. `facts` defaults to an empty list: a
   pack with no clinical content (an introduction, a joke, a transition
   slide) is a valid, not an error, outcome, proven by
   `test_semantic_ledger_accepts_empty_facts_list` and
   `test_extract_ledger_accepts_empty_facts_list`.
2. **Grammar-constrained decoding against the full container schema.**
   `extract_ledger` passes `SemanticLedger.model_json_schema()` (not just
   `list[SemanticFact]`) to llama.cpp's `--json-schema` flag, per the
   schema-refactor instruction. The LLM is prompted to echo `lecture_id`
   and `pack_sequence` back verbatim (they are given in the prompt, not
   inferred), and the raw stdout is parsed with
   `SemanticLedger.model_validate_json()` directly, giving a single
   strict-contract validation point matching the instruction's ETAPA 5.1
   point 4 verbatim.
3. **Traceability guard on echoed keys.** Because `lecture_id` and
   `pack_sequence` are LLM output, not orchestrator-constructed,
   `extract_ledger` verifies the parsed `SemanticLedger`'s
   `lecture_id`/`pack_sequence` match the source `EvidencePack`'s own
   values, raising `LlmParsingError` on a mismatch
   (`_validate_traceability`). Without this guard, a hallucinated
   `pack_sequence` would silently misfile a lecture's ledger under the
   wrong `ledger_NNN.json` filename in `SemanticOrchestrator._save_ledger`
   — a traceability corruption, not merely a parsing failure. This
   mirrors the codebase's established pattern of defending an invariant
   at the domain/worker boundary via an explicit check rather than
   trusting external input (`EvidencePack`'s own `anchor_frame` window
   `model_validator`, `_validate_temporal_window`, present in
   `EvidencePack`'s schema since Phase 4's original commit and discussed,
   though not separately numbered, in ADR 0004).
4. **Single domain exception per worker.** `LlmParsingError` is the sole
   failure type `extract_ledger` raises — missing binary, non-zero
   llama.cpp exit, no JSON object in stdout, malformed JSON syntax, a
   schema-violating hallucination, and a traceability mismatch all surface
   through it. This matches `WhisperTranscriptionError`'s scope in Phase 3
   (ADR 0003): one named, diagnosable exception type per worker rather
   than a raw builtin error leaking through or a proliferation of
   near-duplicate exception classes.
5. **Ephemeral prompt/schema files, cleaned on every exit path.** The
   prompt text and the JSON schema are each written to a `tempfile`-backed
   file (avoiding shell-escaping bottlenecks with raw transcript text) and
   removed in a `finally` block regardless of success, a non-zero exit, or
   a parse failure, matching Phase 3's `whisper_worker.py` ephemeral
   `-oj` JSON cleanup discipline.
6. **Resilient stdout extraction.** `_parse_ledger` scans stdout for
   top-level, balanced `{...}` spans (`_iter_balanced_object_spans`, a
   single linear pass that is string-literal-aware, so a brace inside a
   quoted value — e.g. a fact's own `clinical_context` text — never
   desynchronizes the brace count) and validates each candidate span in
   turn against `SemanticLedger.model_validate_json()`, rather than
   trusting the first or the outermost braces in the raw text. llama.cpp's
   CLI output is not guaranteed to be pure JSON (banner text or an echoed
   prompt, embedding raw untrusted OCR/transcript text, may precede the
   real object); a candidate span failing schema validation does not
   abort the search, since a syntactically-valid-but-wrong-shape JSON
   fragment in that noise must not shadow a well-formed ledger object
   appearing later in the same stdout. See Repair Loop item (e) below for
   the review-round fix that closed the two ways an earlier version of
   this function fell short of that guarantee.
7. **Orchestrator discovers packs by directory scan, not `queue.json`.**
   Unlike `ExtractionOrchestrator`, `TranscriptionOrchestrator`, and
   `EvidencePackOrchestrator` (ADR 0002-0004), `SemanticOrchestrator`'s
   constructor takes only `model_path` and `concurrency_limit` — no
   `queue_path`, per the originating instruction's literal signature.
   `run()` instead walks `EVIDENCE_PACKS_INPUT_DIR` for `pack_*.json` files
   under each subdirectory whose name is itself a valid `SafeIdentifier`
   (see Repair Loop item (a) below): every lecture that reached Phase 4
   already has its packs on disk, so there is no separate per-lecture
   handoff file to look up. A missing `EVIDENCE_PACKS_INPUT_DIR` (Phase 4
   never ran) yields an empty outcome list, not an error, consistent with
   ADR 0004's "zero packs is valid" precedent.
8. **Failure isolation at pack granularity, not lecture granularity.** The
   originating instruction requires that one pack's LLM failure not abort
   the rest of the lecture's packs. `PackLedgerOutcome` is therefore keyed
   by `pack_path` (not `lecture_id`), and `Semaphore(concurrency_limit)`
   (default 1, matching Phase 3's Metal-contention rationale) bounds
   concurrent pack-level `extract_ledger` calls directly, not
   lecture-level calls as in `TranscriptionOrchestrator` — Phase 5
   processes many packs per lecture (one llama.cpp invocation each),
   unlike Phase 3's one whisper.cpp invocation per lecture. A pack file
   that fails to parse as `EvidencePack` (corrupted `pack_NNN.json`) is
   isolated the same way as an `extract_ledger` failure: both are caught
   by the same `try/except Exception` in `_process_pack`, since either
   failure mode must not abort the run.
9. **Atomic single-file write per pack, not per-lecture directory swap.**
   `SemanticOrchestrator._save_ledger` writes `ledger_NNN.json` via a
   `tmp`-then-`replace` atomic swap (Phase 3's pattern), not Phase 4's
   whole-directory staging-then-rename swap. Phase 4 swaps a whole
   lecture's pack set atomically because `build_evidence_packs`
   regenerates every pack for a lecture in one call; Phase 5 processes
   packs independently and in isolation (decision 8), so there is no
   "whole lecture's ledger set" to swap atomically — each `ledger_NNN.json`
   is its own unit of atomicity.

This decision, plus four Repair Loop fixes the same review board pass
required, were applied after the phase's first commit (`a36b7b1`) and are
recorded here rather than treated as silent rework. (a)
`SemanticOrchestrator._discover_pack_paths` now filters lecture
subdirectories through `_is_canonical_lecture_dir_name` (a
`SafeIdentifier` validation) instead of an unfiltered
`EVIDENCE_PACKS_INPUT_DIR.glob("*/pack_*.json")`:
`principal-code-reviewer` found that `EvidencePackOrchestrator._save_packs`
can leave `{lecture_id}.staging/` and `{lecture_id}.previous/` directories
behind on a crashed Phase 4 run (decision 8's own precedent, ADR 0004
Repair Loop item (c)), cleaned up only on Phase 4's own next rerun; an
unfiltered scan would treat those as real lectures and burn a real Metal
LLM call on stale or partial pack data. `SafeIdentifier`'s alphabet never
permits a `.`, so validating each directory name against it excludes
both suffixes without duplicating that exclusion list. (b)
`llm_worker._parse_ledger` now scans every `{` in stdout with
`json.JSONDecoder.raw_decode`, returning the first successful full parse,
instead of slicing between the first `{` and the last `}`:
`principal-code-reviewer` found that llama.cpp's default completion mode
echoes the input prompt (which embeds raw, untrusted OCR/transcript text)
before the generated JSON, and a stray `{` inside that echoed noise (OCR
garbling, a table, clinical notation) would anchor the naive slice on the
wrong brace. A forward scan that discards a `{` whose `raw_decode` fails
correctly skips past such noise to the real object. (c)
`SemanticOrchestrator._save_ledger` is now called with `pack.lecture_id`
(the `EvidencePack`'s own Pydantic-validated field) instead of
`pack_path.parent.name` (the raw directory name read off disk):
`staff-architect`, `principal-code-reviewer`, and `security-reviewer`
independently found that the directory name was never actually validated
at the point of use — the `lecture_id: SafeIdentifier` parameter
annotation on `_save_ledger` was enforced nowhere, since no Pydantic
model construction happens at that boundary — so a future discovery
change (archive extraction, a symlink-tolerant scan, a moved directory)
could silently misfile a ledger under an unvalidated path with no
detection. (d) `extract_ledger` now performs both `_write_temp_file`
calls (prompt, then schema) inside the `try` block, rather than before
it: `security-reviewer` found that if the second write failed after the
first succeeded, the prompt file — holding raw lecture transcript text —
was never reached by the `finally` cleanup and leaked on disk
indefinitely, contradicting decision 5's own stated cleanup guarantee.

A second review-board round (against fix commit `591c8b3`) required three
further corrections, applied in a follow-up commit and recorded here as
the same Repair Loop's continuation. (e) `llm_worker._parse_ledger`'s
item-(b) `raw_decode`-forward-scan fix itself fell short in two ways,
both found by `principal-code-reviewer` with a live repro and a
benchmark: first, it raised `LlmParsingError` on the *first*
syntactically-valid JSON object it found, even when that object failed
`SemanticLedger`'s schema, instead of continuing to a real ledger object
appearing later in stdout — so a coincidental JSON-shaped fragment inside
llama.cpp's echoed prompt (e.g. a slide showing a small JSON/config
snippet) could cause a real, well-formed pack to be spuriously rejected.
Second, the forward scan's failure path was quadratic: a long run of
unmatched `{` characters (plausible from garbled OCR) made every failed
`raw_decode` call construct a `json.JSONDecodeError`, whose `lineno`
computation is itself `O(pos)`, for `O(n)` positions — measured at over 9
seconds for 200,000 stray braces. `_parse_ledger` was rewritten again to
scan for top-level *balanced* `{...}` spans in one linear,
string-literal-aware pass (`_iter_balanced_object_spans`) instead of
trying every raw `{` character: an unclosed brace run now yields zero
candidate spans (no per-position parse attempt, so no quadratic blowup),
and a schema-invalid candidate span no longer aborts the search, since
the loop keeps trying subsequent spans until one validates or the input
is exhausted. (f) `_write_temp_file` now unlinks its own just-created
file if the write itself raises (disk full, EIO) instead of only
after `mkstemp` succeeds and the write also succeeds:
`staff-architect` and `security-reviewer` both noted, as a residual (not
blocking) gap in item (d)'s fix, that a failure *inside* a single write
call — as opposed to the second call never running — still left an
orphaned file, since the caller's `prompt_path`/`schema_path` local
variable is never assigned when `_write_temp_file` itself raises. (g)
This ADR's own decision 3 and decision 6 text, and `README.md`'s Phase 5
section, were corrected to match the actual current code:
`docs-consistency-auditor` found that decision 3 mis-cited
`_validate_temporal_window`'s origin (it predates ADR 0004's Repair Loop
entirely — present in `EvidencePack`'s original Phase 4 schema, not added
by any Repair Loop fix), decision 6 still described the pre-`591c8b3`
first-`{`/last-`}` slice after the code had already moved past it twice,
and README's discovery description had not been updated for item (a)'s
`SafeIdentifier`-filtered directory scan.

## Rationale

Decision 1 resolves the schema gap the originating instruction's literal
type signature left ambiguous; decisions 2-6 apply the schema-refactor
instruction's explicit requirements plus the defensive traceability and
resilience choices needed to make grammar-constrained local-LLM output
trustworthy. Decisions 7-9 adapt the established Phase 2-4 orchestrator
pattern (ADR 0002-0004) to Phase 5's structurally different discovery
model (directory scan, not queue-driven) and failure granularity
(per-pack, not per-lecture).

## Consequences

- `SemanticLedger.entity_name`, `.category`, `.clinical_context`, and
  `.timestamp_ref` no longer exist on `SemanticLedger` itself; any future
  phase consuming individual facts must read `.facts[i].<field>` instead.
- A future phase reading Phase 5 output should expect
  `data/07_semantic_ledgers/{lecture_id}/ledger_{sequence:03d}.json`,
  produced independently per pack rather than as one atomic per-lecture
  directory swap; a partial run leaves some `ledger_NNN.json` files
  present and others (still-failing packs) absent, by design.
- `SemanticOrchestrator` has no `queue.json`-driven duplicate-lecture-id
  guard, unlike its Phase 2-4 siblings: there is no queue input to
  validate, since discovery reads directly from Phase 4's own output tree.

## Alternatives Considered

- **Keep `extract_ledger` returning a bare `list[SemanticFact]` and let the
  orchestrator/worker construct `SemanticLedger` from the known
  `evidence_pack.lecture_id`/`pack_sequence`** — rejected per the
  schema-refactor instruction's explicit requirement that the LLM's
  grammar be constrained against `SemanticLedger.model_json_schema()` in
  full, not just the facts array.
- **Trust the LLM's echoed `lecture_id`/`pack_sequence` without a
  traceability guard** — rejected: a wrong `pack_sequence` would silently
  overwrite or misfile a ledger under the wrong output filename, a data
  integrity failure worse than the loud `LlmParsingError` decision 3
  produces instead.
- **Lecture-level concurrency bound (mirroring
  `TranscriptionOrchestrator`)** — rejected: Phase 5 issues one llama.cpp
  subprocess call per pack, not per lecture, so bounding at lecture
  granularity would let every pack within one lecture launch
  concurrently, defeating the Metal-contention rationale
  `Semaphore(1)` exists for.
