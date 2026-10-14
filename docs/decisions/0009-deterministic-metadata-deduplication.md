# 0009. Global Metadata Aggregation: deterministic text-normalization deduplication

## Status

Accepted.

## Context

Phase 8 leaves every lecture's clinical facts scattered across one
`SemanticLedger` per pack under
`workspace/data/07_semantic_ledgers/{lecture.id}/` (Phase 5's output).
`.prompts/fase9_metadata_aggregation_v2.md` asked for a Phase 9 that
consolidates those ledgers into one global index per lecture -- a
deduplicated glossary plus dosage/treatment and contraindication/alert
subsets -- using "deterministic data engineering (NO LLM) to spare
hardware", explicitly ruling out a second `llama.cpp` pass for this step.

Because no LLM re-reads the facts, textually near-identical entities
extracted from different packs (e.g. `"Aspirina 100mg "` from one pack and
`"aspirina 100mg"` from another) would otherwise be treated as distinct and
appear twice in the index.

## Decisions

1. **`LectureGlobalIndex` domain model** (`domain/models.py`, immediately
   after `SemanticLedger`): `lecture_id: SafeIdentifier`, `glossary_terms:
   list[str]`, `dosages: list[SemanticFact]`, `red_flags:
   list[SemanticFact]`, `model_config = ConfigDict(strict=True)`.
   **Deviation from the instruction's literal name:** the instruction names
   this model `LectureMetadata`, but `domain/models.py` already defines a
   `LectureMetadata` (Phase 1: `id`, `title`, `subject`, `url`,
   `estimated_duration_sec` -- the lecture-discovery/queueing entity), used
   across five source modules (`ingestion/discovery.py` (removed; see ADR 0044), `cli/main.py`,
   `extraction/pipeline.py`, `transcription/pipeline.py`,
   `evidence/pipeline.py`) and six test files. A second, structurally
   unrelated class sharing that name in the same module would silently
   shadow the Phase 1 one and break every one of those call sites. The
   operator was asked and chose
   `LectureGlobalIndex` over renaming the Phase 1 class; this ADR records
   that choice as a deliberate deviation from the instruction's literal
   text, not an oversight (see ADR 0008's own precedent for recording an
   instruction's literal wording separately from what was actually
   honored when the two cannot both hold).
2. **`MetadataAggregator.aggregate_lecture`** (`metadata/aggregator.py`):
   - Validates `lecture_id` as a `SafeIdentifier` before any `Path` splice,
     matching the `assembly`/`critic`/`semantic`/`drafting` convention.
   - Raises `LectureNotFoundError` (reused from `domain/errors.py`, not
     redefined) if
     `workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json`
     does not exist -- Phase 8's canonical document, not the raw Phase 5
     ledgers directory, is the authoritative record of which lectures
     completed the pipeline. A lecture directory under `07_semantic_ledgers/`
     left over from a lecture that never finished Phase 8 must not be
     silently indexed.
   - Raises `SemanticLedgersNotFoundError` (new, `domain/errors.py`) if
     `07_semantic_ledgers/{lecture_id}/` does not exist at all. Phase 5
     writes one ledger file per evidence pack unconditionally (even a
     zero-fact ledger, per `SemanticLedger.facts`'s own empty-list
     default), so a canonically-complete lecture always had this
     directory; its total absence is data loss after the fact, not a
     lecture with no clinical content. An *existing but empty* directory
     (zero `ledger_*.json` files) is, by contrast, a valid zero-fact
     outcome -- matching Phase 8's own directory-exists-but-empty
     precedent (`test_assemble_lecture_handles_empty_lecture_directory`).
   - Reads every `ledger_*.json` under `07_semantic_ledgers/{lecture_id}/`
     and flattens their `facts` in `pack_sequence` order.
3. **`_normalize_text(text: str) -> str`** (`metadata/aggregator.py`,
   module-private): lowercases, collapses internal whitespace runs to a
   single space, strips surrounding whitespace, then strips
   `string.punctuation` from both edges. This is surface-form
   normalization only -- it does not stem, translate, or interpret
   meaning.
4. **Deduplication signatures.** `glossary_terms` dedupes on
   `_normalize_text(entity_name)` alone (first-seen original-casing text is
   kept, so the glossary stays human-readable rather than all-lowercase).
   `dosages`/`red_flags` dedupe on the pair
   `(_normalize_text(entity_name), _normalize_text(clinical_context))` held
   as a tuple in a `set`, not a concatenated string: Python hashes the
   tuple's two fields independently, so there is no naive-concatenation
   collision risk (e.g. `("ab", "c")` and `("a", "bc")` would hash equal
   under `f"{a}{b}"` but not as a tuple). First-seen `SemanticFact` wins
   each signature; iteration order follows `pack_sequence`, so "first-seen"
   means "earliest in the lecture", not filesystem-glob order.
5. **Category routing.** `SemanticFact.category` is a free-text field an
   LLM populates -- `domain/models.py` defines no category enum, and
   `semantic/llm_worker.py`'s extraction prompt only asks for "medical
   facts, dosages, and red flags" in prose, not a closed vocabulary Phase 5
   is actually constrained to produce. Routing is therefore substring
   matching (case-insensitive) against two keyword tuples this phase
   defines as its own best-effort heuristic -- not a vocabulary derived
   from Phase 5's prompt -- (`dosagem`/`dosage`/`dose`/`posologia`/
   `tratamento`/`treatment` for `dosages`; `contraindic`/`red_flag`/
   `red flag`/`alerta`/`warning`/`risco`/`risk` for `red_flags`) rather
   than an exact match against a taxonomy that does not exist in this
   codebase. A category matching both tuples routes the fact into both
   `dosages` and `red_flags` (not disambiguated: the fact is clinically
   both). A category matching neither is not an error -- the fact still
   contributes to `glossary_terms` -- but `_log_uncategorized_facts` emits
   a debug-level log line naming the lecture, entity, and category, so a
   systematic classification gap (e.g. the extraction prompt's wording
   drifting away from these keywords) is observable instead of silent.
   `entity_name`/`category` are truncated to 200 characters before logging
   (`_truncate_for_log`) -- a hygiene bound against an unusually long
   OCR/transcript-derived value bloating log output, not a security
   control. This is the first log statement in the codebase to include
   LLM-extracted lecture free text; a `security-reviewer` pass confirmed
   it introduces no injection surface (lazy `%r`-formatted positional
   arguments, so embedded control characters cannot forge a log record or
   corrupt formatting) and no *additional* exposure of lecture content
   beyond what Phases 3-8 already persist unencrypted to
   `workspace/data/*.json` on every run -- this debug-level, off-by-default
   log is a strict subset of data already at rest since Phase 3's
   transcripts.

## Rationale

- **Why Python string normalization instead of a second `llama.cpp`
  semantic-dedup pass.** The instruction's own framing is the primary
  driver: a local LLM call per candidate pair (or per fact, against a
  running index) is O(N) *model invocations*, each one loading/holding
  weights on the same Apple Silicon GPU the rest of the pipeline (Phases
  3, 5, 6, 7) already contends for, for a step whose only job is
  collapsing near-identical strings. `_normalize_text` plus a `set`-backed
  signature check is O(N) in plain Python, uses no VRAM, and needs no
  subprocess -- matching Phase 8's own "no LLM, pure aggregation"
  precedent (`docs/decisions/0008-...md`) one phase later.
- **Accepted risk: deep semantic paraphrases are not merged.** Two facts
  that say the same clinical thing in different words -- `"Evitar na
  gravidez"` and `"Não usar em gestantes"` -- normalize to two different
  strings and therefore remain two separate `red_flags` entries. This is
  accepted, not overlooked: Phase 9's `LectureGlobalIndex` is documented as
  a raw appendix artifact (a glossary and fact list for reference), not a
  clinically-curated summary a reader trusts as deduplicated-by-meaning.
  A future phase that needs semantic merging can add an LLM-backed pass on
  top of this deterministic one without this ADR's decisions needing to
  change; running that pass here, inside a phase explicitly scoped to be
  LLM-free, would reintroduce the hardware cost this phase exists to
  avoid.
- **Why tuple-in-`set` instead of an explicit `hashlib` digest.** The
  instruction asks for a "hash-based signature"; Python's `set` already
  hashes its tuple keys internally, so a tuple *is* a hash-based signature
  without hand-rolling string concatenation and a digest function that
  would need to defend against the same delimiter-collision class of bug
  a tuple sidesteps for free.

## Consequences

- **Superseded by
  `docs/decisions/0012-deterministic-flashcard-generation.md`:**
  `LectureGlobalIndex.glossary_terms` was changed from `list[str]` to
  `list[SemanticFact]`. Decision 4's dedup signature for `glossary_terms`
  is unchanged (`_normalize_text(entity_name)` alone, first-seen wins),
  but the value kept per signature is now the full `SemanticFact` object
  instead of just its `entity_name` string, so `clinical_context` survives
  glossary deduplication. This was required by Phase 12: an Anki
  flashcard's Back needs a term's definition/context, which the
  `list[str]` shape discarded at aggregation time. `publishing/
  markdown_compiler.py`'s `_render_glossary` was updated in the same
  commit to read `fact.entity_name` off the new shape, preserving its
  existing bullet-list-of-terms-only rendering (the appendix does not
  show `clinical_context` for glossary entries; only the flashcard deck
  does).
- `LectureGlobalIndex` was not persisted to `workspace/data/` by this
  phase as originally accepted -- the instruction scoped Phase 9 to the
  domain model and the aggregation engine only, with no output-directory
  or CLI/pipeline wiring requirement (unlike Phases 2-8, each of which
  writes its handoff artifact to disk). **Superseded by
  `docs/decisions/0011-markdown-compilation-and-publishing-pipeline.md`:**
  `aggregate_lecture` now also writes
  `workspace/data/11_metadata/{lecture_id}/global_index.json`, using
  exactly the write-to-`.tmp`-then-`replace` convention this bullet
  anticipated, added as a direct dependency of Phase 11's
  `PublishingOrchestrator`.
- `domain/models.py` now holds two lecture-scoped, differently-shaped
  classes with adjacent but distinct names (`LectureMetadata`,
  `LectureGlobalIndex`); a future reader must not conflate them. Both
  docstrings state their owning phase explicitly to close that ambiguity.
- Category routing is keyword-based, not schema-enforced: a
  `SemanticFact` whose `category` text does not contain any configured
  keyword (e.g. a plain glossary term with no dosage/alert significance)
  is correctly excluded from both `dosages` and `red_flags` while still
  contributing to `glossary_terms`, and is logged at debug level so the
  gap is observable. A `SemanticFact` matching both keyword tuples is
  intentionally indexed in both `dosages` and `red_flags`, not
  deduplicated between the two. If Phase 5's extraction prompt's category
  wording drifts, `_DOSAGE_CATEGORY_KEYWORDS`/`_RED_FLAG_CATEGORY_KEYWORDS`
  must be revisited together with it -- the debug log is the signal that
  drift has happened, not a guarantee it hasn't.
- `domain/errors.py`'s `LectureNotFoundError` now fires for two
  structurally distinct conditions (Phase 8's missing
  `09_validated_units/{lecture_id}/`, Phase 9's missing
  `canonical_document.json`); its docstring was updated in this same
  commit to state both, closing the staleness a single-condition
  docstring would otherwise leave for a Phase 9 caller. See Alternatives
  Considered for why reuse (not a third phase-specific class) was chosen
  for this particular condition, in contrast to `SemanticLedgersNotFoundError`.

## Alternatives Considered

- **Reuse the instruction's literal `LectureMetadata` name, renaming the
  existing Phase 1 class instead.** Rejected: the Phase 1 class is
  referenced across five source modules and six test files; renaming it is
  a strictly larger, riskier change than naming the new Phase 9 model
  something else, for a naming collision the instruction's author did not
  know existed when it was written. See Decision 1.
- **A second `llama.cpp` pass for semantic-aware deduplication.** Rejected
  per the instruction's explicit "SEM LLM... poupar hardware" framing and
  Rationale above.
- **Dedup key as a delimiter-joined string
  (`f"{entity_name}|{clinical_context}"`) instead of a tuple.** Rejected:
  a tuple is both simpler to construct and immune to the case where the
  delimiter character itself appears inside either normalized field
  (clinical free text can plausibly contain `|`), which would silently
  under-deduplicate or over-deduplicate depending on where the delimiter
  lands.
- **A brand-new, Phase-9-specific exception (e.g.
  `CanonicalDocumentNotFoundError`) instead of reusing `LectureNotFoundError`
  for the missing-`canonical_document.json` check.** Considered, not taken:
  a dedicated class would let a future caller discriminate "Phase 7 never
  ran" from "Phase 8 never ran" by type instead of by parsing the message
  string -- a real advantage a `principal-code-reviewer`/`staff-architect`
  pass on this ADR's first draft raised. It was not adopted because
  `LectureNotFoundError` already means, at the concept level shared by
  both call sites, "this lecture has not reached the pipeline stage this
  code depends on" -- the same concept Phase 8 named it for, just checked
  against a different downstream artifact. Reuse keeps that one concept
  under one name instead of multiplying near-synonymous
  `*NotFoundError` classes as each new phase adds one more upstream check;
  the docstring fix (Decision 2 / Consequences) closes the ambiguity a
  reused name would otherwise leave. A future phase is free to revisit
  this if a caller genuinely needs to `except` the two cases separately,
  at which point splitting by type becomes a real requirement rather than
  a speculative one.
- **Reusing `LectureNotFoundError` (instead of a new class) for the
  missing-`07_semantic_ledgers/{lecture_id}/`-directory case.** Rejected,
  in contrast to the point above: that condition is not "this lecture
  hasn't reached this pipeline stage" (the lecture demonstrably has --
  `canonical_document.json` exists) but "expected data is missing for a
  lecture that should have it" -- a different failure class (data loss /
  corruption) that would be actively misleading under the
  `LectureNotFoundError` name. `SemanticLedgersNotFoundError` is a new,
  separately named class for that reason.
