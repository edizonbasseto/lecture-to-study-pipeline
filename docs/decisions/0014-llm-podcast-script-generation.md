# 0014. LLM Podcast Script Generation (Two-Speaker Deep Dive, Unit Traceability)

## Status

Accepted. **Superseded by ADR 0038:** the local llama.cpp podcast generator (podcast/generator.py) is deleted; scripts run scripts/generate_codex_podcast.py (ADR 0037). The deterministic compiler survives. The erratum below is historical. **Erratum (ADR 0020):** the llama-cli invocation referenced
below uses `--json-schema`, a flag name confirmed wrong running the real
pipeline end to end -- the correct flag is `--json-schema-file`, and the
call site now also passes `-n` to cap completion length. See ADR 0020 for
the corrected contract and the incident that found it. Every other
decision recorded here is unaffected.

**Erratum (ADR 0022):** `--json-schema-file` itself was subsequently
dropped entirely (this llama-cli build's grammar sampler fails to
initialize on any structured-output request, a known upstream defect) in
favor of embedding the schema as prompt text plus post-hoc parse/validate.
This ADR's own Critic/Repair retry (already present) is unaffected. See
ADR 0022 for the full incident and rationale.

## Context

Phase 13 (ADR 0013) established this codebase's pattern for a new
generative task (not a copy/transform of already-extracted facts) using
the local llama.cpp path: grammar-constrained JSON output, one Critic/
Repair retry, and a cross-field contract check on top of Pydantic's own
schema validation.
`.prompts/fase14_podcast_script.md` asks for a second such task: a
two-speaker "Deep Dive" educational podcast script (Host asks, Expert
explains) synthesized from one lecture's `CanonicalLectureDocument`,
reserved for a future Phase 15 text-to-speech (TTS) pass. Unlike Phase
13's mock exam (five self-contained questions with no per-question
provenance requirement), the instruction requires every dialogue line to
carry `source_unit_id`: clinical traceability must survive all the way
into the finished audio material, not just the written study guide.

## Decisions

1. **`PodcastLine`/`PodcastScript` domain models** (`domain/models.py`,
   appended after `MockExam`): `PodcastLine` holds
   `speaker: Literal["Host", "Expert"]`, `text: str`,
   `source_unit_id: str`, `model_config = ConfigDict(strict=True)`, plus
   an `after`-mode `model_validator` rejecting a blank (whitespace-only)
   `text`. The instruction specifies `speaker: Literal["Host", "Expert"]`
   explicitly and states its own purpose (future TTS voice mapping)
   verbatim, so no deviation was considered here, unlike `lecture_id`'s
   typing below. `PodcastScript` holds `lecture_id: SafeIdentifier` (a
   deviation from the instruction's literal `str`, flagged explicitly
   here rather than left implicit -- see Alternatives Considered),
   `title: str` (with its own blank-rejecting `model_validator`),
   `dialogue: list[PodcastLine] = Field(min_length=1)` -- a
   `principal-code-reviewer` pass on this ADR's first draft found none of
   these three guards present, so a schema-valid-but-empty LLM response
   (`{"lecture_id": "...", "title": "", "dialogue": []}`) would pass every
   check in `generate_script` and be compiled to disk as a silently
   "successful" episode. All three now reject at the same Pydantic
   boundary every other structural check in this file uses.
2. **`PodcastGenerationError`** (new, `domain/errors.py`, placed directly
   in this file): raised by `podcast/generator.py` for the same two
   categories `MockExamGenerationError` (Phase 13) covers -- subprocess-
   level inference failure and JSON/Pydantic-contract breakage -- plus a
   third this phase adds: broken traceability (an echoed `lecture_id`
   that does not match the source document, or a `source_unit_id` that
   does not name a real unit of it). Placed here following Phase 13's own
   precedent directly, not re-litigated: ADR 0013's Alternatives
   Considered already records the domain-policy-vs-technical tension this
   placement carries (per ADR 0008's own criterion) at length, and this
   phase's instruction repeats the same "same pattern as Phase 13"
   direction rather than asking for a different shape.
3. **`PodcastScriptGenerator.generate_script`** (`podcast/generator.py`):
   - Validates `lecture_id` as a `SafeIdentifier` before any `Path`
     splice, and reads
     `workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json`
     directly, raising the pre-existing `LectureNotFoundError` if absent
     -- matching Phase 13's own convention at the same file.
   - Assigns each `document.units[i]` a synthetic id `"unit-{pack_sequence:03d}"`
     (`_UNIT_ID_TEMPLATE`, matching `critic/pipeline.py`'s own
     `unit_{pack_sequence:03d}.json` file-naming convention for the same
     concept) since `ValidatedLearningUnit`/`LearningUnitDraft` carry no
     first-class "unit id" field of their own -- this generator is the
     first phase to need one to cite. The Evidence Pack given to the LLM
     is each unit's `body_markdown`, prefixed with its own bracketed id
     (`"[unit-001] ..."`), so the LLM can quote a specific id back in
     `source_unit_id` without the generator needing to parse free text to
     recover it.
   - Prompts a local llama.cpp (Metal) subprocess, grammar-constrained via
     `--json-schema` to `PodcastScript.model_json_schema()` directly (an
     *object* schema, unlike Phase 13's array schema -- see Alternatives
     Considered) -- the LLM echoes `lecture_id` back (Phase 6's
     `LearningUnitDraft` traceability-echo pattern) and supplies `title`
     and `dialogue` itself.
   - Parses stdout via a balanced, string-literal-aware `{...}` object
     scanner (`_iter_balanced_object_spans`, matching `drafting/
     llm_worker.py`'s own convention for an object-shaped target, not
     `exams/generator.py`'s array-shaped one).
   - **Post-parse traceability check** (`_validate_traceability`,
     matching `drafting/llm_worker.py`'s own same-named helper in shape):
     rejects an echoed `lecture_id` mismatch, and rejects any
     `PodcastLine.source_unit_id` absent from the lecture's actual unit-id
     set -- this check cannot live in `PodcastLine`'s own Pydantic
     validator (unlike `MockQuestion.correct_answer`-in-`options`, Phase
     13) because the valid id set is per-document, not knowable from one
     `PodcastLine` instance alone. Collects *every* offending line before
     raising, not just the first: a `principal-code-reviewer` pass on this
     ADR's first draft noted that stopping at the first bad line means a
     repair retry only ever hears about one failure at a time, and could
     fix that one while still failing on a second, previously-unreported
     line -- exhausting the single retry without ever succeeding even
     though one feedback message naming both could have let it.
   - **Critic/Repair retry (1 attempt):** any failure -- subprocess-level,
     JSON/Pydantic-contract, or traceability -- on the first attempt
     triggers exactly one more attempt with the failure's message appended
     as repair feedback; a second failure raises `PodcastGenerationError`
     and gives up, matching Phase 13's retry shape exactly per the
     instruction's own "mesmo padrão... usado na Fase 13" direction.
4. **`PodcastScriptCompiler.compile`** (`podcast/compiler.py`): persists
   `workspace/data/15_podcast_scripts/{lecture_id}/script.json` (raw
   `PodcastScript.model_dump(mode="json")`, reserved for a future Phase 15
   TTS pass to consume directly) and
   `workspace/data/15_podcast_scripts/{lecture_id}/script_legivel.md` -- a
   readable transcript, one `**Host:** "..."`/`**Expert:** "..."` line per
   `PodcastLine`, each suffixed with `*(Fonte: {source_unit_id})*`, per
   the instruction's own example format. Both `title` and every dialogue
   line's `text`/`source_unit_id` are Markdown-escaped before
   interpolation (`_escape_markdown`), even though `source_unit_id` is
   already generator-validated to the fixed `"unit-NNN"` shape and so
   never actually contains a special character today -- escaped anyway
   rather than trusting that upstream constraint to hold forever, matching
   this codebase's proactive-over-reactive posture on this exact risk
   class (see ADR 0013's own Rationale on the same point). Unlike
   `exams/compiler.py`/`publishing/markdown_compiler.py`'s shared regex,
   `podcast/compiler.py`'s `_MARKDOWN_SPECIAL_CHARS` also escapes a
   literal `"`: `_render_line` wraps `text` in a literal `"..."` pair (per
   the instruction's own example format), a template choice unique to
   this compiler, so an embedded quote in the LLM's own output would
   otherwise visually close that wrapper early in the rendered transcript
   -- a `principal-code-reviewer` finding on this ADR's first draft (`"`
   is valid CommonMark-escapable ASCII punctuation, so this is consistent
   with, not a departure from, the existing escaping approach).
   `ExamCompiler`'s I/O-ownership shape (Phase 13, not
   `publishing/markdown_compiler.py`'s pure-transform one) is the closer
   structural analog `PodcastScriptCompiler` follows: it owns its own
   file writes directly, since Phase 14 -- like Phase 13 -- has no
   separate orchestrator module to own them instead.

## Rationale

- **Why an object schema (`PodcastScript.model_json_schema()`) instead of
  Phase 13's array schema.** `PodcastScript` needs a `title` the LLM
  itself proposes, which has no natural home in a bare
  `list[PodcastLine]` the way Phase 13's exam array could stay pure data.
  An object target also lets the same `lecture_id`-echo traceability
  pattern Phase 6 already established apply here, which a plain array
  target cannot express without inventing a wrapper object anyway --
  matching the object-shaped majority of this codebase's llm_worker.py
  siblings (`drafting`, `semantic`, `critic`) rather than Phase 13's own,
  narrower deviation (justified there by its own array-specific
  instruction wording, which this phase's instruction does not repeat).
- **Why `source_unit_id` is required on every line, including Host's,
  not just Expert's.** The instruction's own P0 rule is "Toda afirmação
  do Expert deve ser ancorada no Evidence Pack", naming Expert
  specifically. `PodcastLine`'s schema has no optional/nullable
  `source_unit_id`, and a Host question is itself drawn from a specific
  clinical case in the Evidence Pack (per the instruction's own "Host faz
  perguntas baseadas nos casos"), so requiring it uniformly keeps the
  domain model's one invariant simple (every line is grounded in exactly
  one unit) instead of a conditional-on-`speaker` requirement that would
  need its own cross-field validator for a distinction the instruction's
  traceability goal does not actually need.
- **Why the unit-id/traceability check lives in `podcast/generator.py`,
  not `PodcastLine`.** Symmetric with `MockQuestion`'s own boundary
  choice (ADR 0013): a check knowable from one instance alone
  (`correct_answer` against that same instance's `options`) belongs in
  the Pydantic model; a check that needs the whole document's unit-id set
  (not visible to a single `PodcastLine`) belongs in the generator that
  has that context, exactly where `drafting/llm_worker.py`'s own
  `_validate_traceability` already lives for the same structural reason.

## Consequences

- `domain/models.py` now holds a seventh and eighth lecture-scoped output
  model (`PodcastLine`, `PodcastScript`) alongside `LectureMetadata`,
  `LectureGlobalIndex`, `CanonicalLectureDocument`, `Flashcard`,
  `MockQuestion`, `MockExam` -- six prior models, so the new pair is
  seventh/eighth, not "eighth and ninth" (a `docs-consistency-auditor`
  finding on this ADR's first draft, which also found the same off-by-one
  arithmetic in ADR 0013's own Consequences section -- "sixth and
  seventh" for what was actually its fifth/sixth model; corrected there
  too in this same review pass).
- `domain/errors.py` gains `PodcastGenerationError` as a `RuntimeError`
  directly (matching `MockExamGenerationError`'s own shape, not a
  `LectureNotFoundError` subclass); its module docstring and
  `LectureNotFoundError`'s own docstring are updated to list Phase 14's
  new call sites, per this file's established "docstring is the
  authoritative site list" convention.
- Phase 14 is llama.cpp's fifth call site in this codebase (after
  `semantic`, `drafting`, `critic`, `exams`), duplicating
  `_write_temp_file`/`_start_llama_process`/balanced-span-scan machinery
  locally per ADR 0006's self-containment rule. ADR 0013's Consequences
  already flagged the fourth call site as crossing the informal "rule of
  three" threshold; a `staff-architect` pass on this ADR's first draft
  flagged the fifth as a second consecutive deferral with no corrective
  action, and asked the operator to decide explicitly rather than let a
  third ADR repeat the same unresolved note. **Operator decision:**
  duplication stays as-is through Phase 16 (the pipeline's remaining
  scope). ADR 0006's own self-containment rule exists specifically to
  keep each already-shipped phase (`semantic`, `drafting`, `critic`,
  `exams`) isolated from changes made for a later phase's sake;
  refactoring four consolidated phases to extract a shared base now, two
  phases from the end of the V1 pipeline, risks a regression in code that
  already works, for a code-deduplication benefit with no user-facing
  payoff at this stage. A "Shared LLM Worker Base" extraction is deferred
  as a deliberate, named V2.0 technical-debt item, to be taken up only
  after Phase 16 ships -- not reopened per-phase, and not left as a vague
  "future ADR" note a sixth phase would otherwise repeat again.
- `podcast/` reaches 100% statement coverage with all subprocess calls
  mocked (`FakeProcess`/`monkeypatch`, matching `test_exams.py`'s own
  pattern), including retry-then-succeed/retry-then-fail for each of the
  three failure categories (malformed JSON, `lecture_id` mismatch,
  unknown `source_unit_id`), missing-binary/non-zero-exit, and direct
  `_parse_script`/`_iter_balanced_object_spans` regression coverage (a
  brace inside a quoted `text` field, banner noise with brace tokens
  ahead of the real object) -- added proactively this time, rather than
  after a review-board finding the way Phase 13's equivalent array-scanner
  coverage was. A review-board fix pass added further coverage: the
  empty-`dialogue`/blank-`title`/blank-`text` domain-level rejections
  (Decision 1), a regression test proving `_validate_traceability` names
  every offending line in one message, not just the first (Decision 3),
  and a `script_legivel.md` rendering test for an embedded quote inside a
  dialogue line (Decision 4).

## Alternatives Considered

- **`PodcastScript.lecture_id: str`, the instruction's literal type,
  instead of `SafeIdentifier`.** Not taken, for the same reason ADR 0013
  gave for `MockExam.lecture_id` (flagged there only after a
  `docs-consistency-auditor` finding on the first draft; recorded here
  from the start instead): every other phase's `lecture_id` field already
  uses `SafeIdentifier`, and `PODCAST_SCRIPTS_OUTPUT_DIR / script.lecture_id`
  splices this value into a filesystem path in `PodcastScriptCompiler.compile`
  -- a bare `str` would accept a `../`-containing value at construction
  time and defer the path-traversal guard entirely to
  `generate_script`'s own `_LECTURE_ID_ADAPTER.validate_python` call,
  leaving `PodcastScript` itself unable to guarantee its own `lecture_id`
  is filesystem-safe if ever constructed another way.
- **A module-local `LlmParsingError` in `podcast/generator.py`, per ADR
  0008's domain-policy-vs-technical criterion, instead of
  `PodcastGenerationError` in `domain/errors.py`.** The same tension ADR
  0013's Alternatives Considered documents at length for
  `MockExamGenerationError` applies here (most of this class's trigger
  set is "technical/IO failure local to one worker", not domain policy).
  Not re-argued in full here to avoid duplicating that record; the
  instruction's explicit "mesmo padrão... usado na Fase 13" direction is
  read as endorsing Phase 13's placement choice specifically, including
  its unresolved tension, rather than asking this ADR to resolve it
  differently.
- **A separate, cheaper Host-only requirement for `source_unit_id`
  (nullable for Host lines, required for Expert).** Considered to mirror
  the instruction's P0 rule naming Expert specifically. Rejected: see
  Rationale above -- a Host question is itself grounded in a specific
  case, and a uniform non-nullable field keeps the model's invariant
  simple without a `speaker`-conditional validator whose only benefit
  would be exempting Host from a traceability guarantee the instruction
  never actually asks to relax.
