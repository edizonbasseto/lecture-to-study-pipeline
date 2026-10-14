# 0011. Markdown Compilation and Publishing Pipeline

## Status

Accepted.

## Context

Phase 8 leaves a lecture's vetted content in one
`CanonicalLectureDocument` (`workspace/data/10_canonical_documents/`) and
Phase 9 leaves its deduplicated clinical facts in one
`LectureGlobalIndex`. `.prompts/fases10_e_11_publishing.md` asked for
Phase 10 (deterministic Markdown compilation of both into one study
guide) and Phase 11 (orchestration: read the Phase 8/9 artifacts, run the
Phase 10 compiler, export to a reader-facing format) as one combined
effort. Like Phases 8-9, neither phase involves `llama.cpp` or model
inference -- both are pure, deterministic data transformation over
already Critic-approved (Phase 7) and deduplicated (Phase 9) content.

The instruction's literal Phase 11 wording assumed a `PublishingOrchestrator`
reading a persisted `workspace/data/11_metadata/` artifact. ADR-0009,
however, explicitly recorded that `MetadataAggregator.aggregate_lecture`
does **not** persist `LectureGlobalIndex` to disk -- a deliberate Phase 9
scoping decision, not an oversight. The operator was asked how to resolve
this conflict and chose to extend Phase 9 with persistence now, per
ADR-0009's own Consequences section, which had already anticipated this
exact follow-up ("A future phase adding persistence... would follow the
same write-to-`.tmp`-then-`replace` convention").

The instruction also allowed avoiding heavy binary rendering dependencies
(`pdfkit`, `weasyprint`) in favor of an HTML-first export if that is
"safer and faster" on the target Apple Silicon machine, deferring true
PDF export to a later phase.

## Decisions

1. **`MetadataAggregator.aggregate_lecture` now persists**
   (`metadata/aggregator.py`): a new `METADATA_OUTPUT_DIR =
   Path("workspace/data/11_metadata")` constant and a `_save_index`
   staticmethod, mirroring `DocumentAssembler._save_document`'s
   write-to-`.tmp`-then-`.replace()` convention exactly. `aggregate_lecture`
   now writes `workspace/data/11_metadata/{lecture_id}/global_index.json`
   before returning the in-memory `LectureGlobalIndex`, closing the gap
   ADR-0009 left open. This is the only change to a Phase 9 file; no other
   Phase 9 behavior (validation gates, deduplication, category routing)
   changes.
2. **`MarkdownCompiler.compile`** (new `publishing/markdown_compiler.py`):
   `compile(document: CanonicalLectureDocument, index: LectureGlobalIndex)
   -> str`. Renders, in order: a header (`# {lecture_id}` plus the
   `assembled_at` UNIX timestamp formatted as `YYYY-MM-DD`), the lecture
   body (one `## {unit.content.title}` section per `ValidatedLearningUnit`
   in `document.units`, already in `pack_sequence` order per Phase 8's own
   invariant, followed by `unit.content.body_markdown`), and a clinical
   appendix (`## Apêndice Clínico` containing `### Glossário`, `###
   Dosagens`, and `### Alertas Vermelhos` subsections sourced from
   `index.glossary_terms`/`dosages`/`red_flags`). An empty `units` list
   renders an explicit placeholder sentence rather than an empty body
   section; each empty appendix subsection renders its own "Nenhum ...
   catalogado" placeholder, so a lecture with no dosage/red-flag facts
   still produces a well-formed, non-misleading document instead of a
   silently missing section.
3. **`PublishingOrchestrator.publish_lecture`** (new
   `publishing/pipeline.py`):
   - Validates `lecture_id` as a `SafeIdentifier` before any `Path`
     splice, matching every prior phase's convention.
   - Reads `workspace/data/10_canonical_documents/{lecture_id}/canonical_document.json`
     directly (not via `DocumentAssembler`, which would require Phase 7's
     `09_validated_units/` and re-assemble/re-write a document Phase 8
     already produced); raises `LectureNotFoundError` (reused, not
     redefined) if that file is missing.
   - Reads `workspace/data/11_metadata/{lecture_id}/global_index.json`
     directly (not by calling `MetadataAggregator().aggregate_lecture`)
     to obtain the `LectureGlobalIndex`; raises `LectureNotFoundError` if
     that file is missing. See Decision 1 for why Phase 9 now guarantees
     this file exists, and Alternatives Considered for why a direct
     `MetadataAggregator` call (this ADR's first draft) was reverted
     during review.
   - Runs `MarkdownCompiler().compile(document, index)` and writes the
     result to `workspace/data/12_published/{lecture_id}/study_guide.md`.
   - Renders that Markdown to HTML via `markdown_it.MarkdownIt("commonmark",
     {"html": False}).render(...)` (see Decision 6 and Alternatives
     Considered for why `html` is explicitly disabled), wrapped in a
     minimal `<!DOCTYPE html>` document, and writes it to
     `workspace/data/12_published/{lecture_id}/study_guide.html`.
   - Both writes use the same write-to-`.tmp`-then-`.replace()` convention
     as every other phase. The two writes (`.md` then `.html`) are each
     individually atomic but not jointly atomic as a pair -- see
     Consequences.
4. **`domain/errors.py`'s `LectureNotFoundError` docstring** extended with
   two new bullets: Phase 11's `publishing/pipeline.py` reuses it once for
   the same missing-`canonical_document.json` condition Phase 9 already
   guards, and again for its own new missing-`global_index.json`
   condition (Decision 3).
5. **`markdown-it-py` added as a direct dependency** (`pyproject.toml`):
   it was already resolved transitively (via `rich`'s own Markdown
   rendering dependency) and present in `.venv`/`uv.lock`, so promoting it
   to direct added no new download, no new binary, and no new supply-chain
   surface -- only an explicit, auditable declaration of a dependency the
   codebase now imports directly.
6. **Two independent escaping layers, added during `security-reviewer`
   and `principal-code-reviewer` review of this ADR's first draft**,
   because `entity_name`/`clinical_context`/`category` (`SemanticFact`)
   and a unit's `title` are LLM-extracted free text from OCR'd
   slides/transcripts -- content this pipeline does not author and cannot
   guarantee is markdown- or HTML-safe:
   - `publishing/pipeline.py` constructs its `MarkdownIt` renderer as
     `MarkdownIt("commonmark", {"html": False})` instead of the
     `commonmark` preset's default (`html: True`). With `html: True`
     (verified empirically: `MarkdownIt().options["html"] is True`), raw
     HTML in source Markdown -- e.g. a `clinical_context` value containing
     literal `<script>...</script>` -- passes through into
     `study_guide.html` unmodified; with `html: False` it is HTML-escaped
     to inert literal text instead.
   - `publishing/markdown_compiler.py` adds a module-private
     `_MARKDOWN_SPECIAL_CHARS` regex and a `MarkdownCompiler._escape_markdown`
     classmethod, applied to `unit.content.title`,
     `fact.entity_name` (including each `SemanticFact` in
     `index.glossary_terms` -- see `docs/decisions/
     0012-deterministic-flashcard-generation.md`'s retrofit note on
     `LectureGlobalIndex.glossary_terms`'s `list[str]` -> `list[SemanticFact]`
     change: `_render_glossary` now escapes `fact.entity_name` per fact, the
     same access pattern already used for dosages/red_flags, not a
     separate raw-string path), and `fact.clinical_context` before
     interpolation -- backslash-escaping
     CommonMark's structurally significant ASCII punctuation
     (`` \`*_#+-[]()<>| ``) per the CommonMark spec's own escaping rules.
     This is independent of and complementary to the `html: False` change
     above: it stops a stray `#`/blank-line combination in free text from
     rendering as a spurious heading, or an unbalanced `` * ``/`` ` ``
     from corrupting the rest of the compiled document's formatting --
     concerns about markdown *structure*, not HTML execution.
     `unit.content.body_markdown` is deliberately **not** escaped: Phase 6
     writes it as markdown by design (it is the study guide's own prose,
     Critic-approved in that form), unlike the appendix's raw extracted
     facts.
   - `_escape_markdown` also collapses any embedded newline run
     (`_EMBEDDED_NEWLINE_RUN`) in these same fields to a single space
     before the punctuation escape runs. A `principal-code-reviewer` pass
     on this Decision's first implementation found that punctuation
     escaping alone does not stop an embedded blank line (two consecutive
     newlines) from starting a genuine new markdown block -- e.g. a
     numbered-list marker (`"1. ..."`) carries no punctuation
     `_MARKDOWN_SPECIAL_CHARS` escapes, so `clinical_context` containing
     `"Contexto normal.\n\n1. Item forjado"` still fractured the compiled
     document into an orphaned second block. Every call site interpolates
     these fields as a single line within a template prefix (`"- **{...}**:
     "`, `"## {...}"`), so a field that can no longer contain a newline
     can never itself begin a fresh line -- closing the vector directly
     instead of enumerating every possible block-starting marker.

## Rationale

- **Why extend Phase 9 instead of keeping Phase 11 read-through-nothing
  in-memory.** The operator was presented both options (in-memory call
  with zero Phase 9 changes, vs. adding persistence) and chose
  persistence, so Phase 11 could genuinely "read" a real
  `workspace/data/11_metadata/` artifact as the instruction's literal text
  described, and so that artifact exists on disk for any future consumer
  (a manual audit, a future CLI, a different orchestrator) independent of
  `PublishingOrchestrator` ever running.
- **Why `markdown-it-py` over `markdown`/`pdfkit`/`weasyprint`.** The
  instruction explicitly permitted deferring PDF export and using
  whichever Markdown-to-HTML approach is lightest for the Apple Silicon
  environment, avoiding complex binary dependencies where possible.
  `weasyprint` and `pdfkit` both pull in non-trivial native rendering
  stacks (Cairo/Pango, or a wkhtmltopdf binary respectively) -- exactly
  the "complex binary dependencies" the instruction asked to avoid if
  possible. `markdown-it-py` is pure Python, CommonMark-compliant, and
  was already present in the dependency tree (transitively, via `rich`)
  with zero additional installation cost. HTML export alone (no PDF) is
  therefore Phase 11's scope; PDF export is left to a future phase, as the
  instruction allowed.
- **Why `PublishingOrchestrator` reads `canonical_document.json` directly
  instead of calling `DocumentAssembler.assemble_lecture` again.** Phase 8
  already ran and already wrote this file; re-invoking the assembler would
  require Phase 7's `09_validated_units/` directory to still exist (an
  unnecessary coupling for a phase that only needs Phase 8's already-final
  output) and would re-validate/re-write a document that does not need to
  change. Reading the file directly, exactly as `MetadataAggregator`
  itself already does for its own precondition check, keeps Phase 11
  dependent only on Phase 8's and Phase 9's actual output artifacts.
- **Why `PublishingOrchestrator` reads `global_index.json` from disk
  rather than calling `MetadataAggregator().aggregate_lecture` (this
  ADR's original design).** A `staff-architect` review flagged that
  calling another phase's orchestrator class directly -- not just reading
  its persisted artifact -- breaks
  `docs/decisions/0006-learning-unit-draft-synthesis-contracts.md`'s
  self-containment rule ("each phase module is self-contained... never a
  sibling phase module"), a rule this codebase has granted exactly one
  prior, narrowly-scoped exception to (`critic/pipeline.py` importing a
  stateless `drafting/llm_worker.py` function, explicitly named and
  scoped in `docs/decisions/0007-...md`). The rejected design pulled in
  `MetadataAggregator.aggregate_lecture` whole -- its own precondition
  chain (re-checking `canonical_document.json`, raising
  `SemanticLedgersNotFoundError` off `07_semantic_ledgers/`) and its
  disk-write side effect -- which meant `publish_lecture` transitively
  depended on Phase 5's raw ledger directory still existing even after
  Phase 9 had already run and persisted its own artifact, defeating the
  entire point of Decision 1's persistence: reading only the *files* a
  phase's declared upstream dependencies promise, exactly like
  `_load_canonical_document` already does for Phase 8's output. The
  original "avoid a redundant round-trip" framing for the in-memory-call
  design was a real but strictly smaller benefit than the coupling and
  self-containment cost it introduced.
- **Why `html: False` and `_escape_markdown` instead of the `MarkdownIt`
  default.** `security-reviewer` and `principal-code-reviewer` passes on
  this ADR's first draft, run independently, both reproduced the same
  defect empirically: `MarkdownIt()`'s `commonmark` preset defaults to
  `html: True` (CommonMark's spec allows raw HTML passthrough by design),
  so a `clinical_context` value containing `<script>...</script>` -- OCR
  can plausibly capture such a string verbatim from a slide's visible
  text, or an LLM can copy it verbatim into extracted content -- rendered
  into `study_guide.html` as a live, executable script tag, not inert
  text, once the operator opened that file in a browser (the file's
  stated purpose). This is a defect the original design's own stated goal
  ("well-formed, non-misleading document") directly contradicted, so it
  is fixed at the source rather than accepted as a residual risk: `html:
  False` closes the HTML-execution vector, and `_escape_markdown` closes
  the adjacent markdown-structure-corruption vector (spurious headings,
  broken bold/code spans) the same untrusted free text can cause even
  without raw HTML.

## Consequences

- Phase 9 is no longer a pure in-memory, no-disk-write phase; ADR-0009's
  Consequences section was updated in this same change to mark that
  bullet superseded rather than left silently stale, closing the drift a
  `docs-consistency-auditor` pass would otherwise flag.
- `workspace/data/11_metadata/{lecture_id}/global_index.json` and
  `workspace/data/12_published/{lecture_id}/{study_guide.md,study_guide.html}`
  are new on-disk artifacts, all unencrypted plain files -- their
  **at-rest confidentiality** is covered by ADR-0010's PII risk
  acceptance like every other phase's output, not a new exposure
  category. `study_guide.html` is, however, a qualitatively different
  artifact from every prior phase's machine-parsed JSON: it is a document
  a human opens in a browser, which is a code-execution surface (Decision
  6) ADR-0010 never analyzes -- ADR-0010's FileVault-delegation rationale
  addresses "bytes on a powered-off/stolen disk," not "a script tag
  executing once the file is opened." That render-time risk is this
  ADR's own responsibility and is addressed directly by Decision 6, not
  by reference to ADR-0010.
- PDF export is out of scope for this ADR. `DIRETRIZES_ARQUITETURA.txt`
  Section 3 names `Typst` as the project's mandated PDF renderer; a
  future phase adding PDF export should use it (or explicitly record a
  deviation, per this project's established ADR convention for
  instruction deviations -- see ADR-0009 Decision 1) rather than
  defaulting to `study_guide.html` post-processing via a headless browser
  or another renderer without addressing that mandate.
- The two `12_published/{lecture_id}/` writes (`.md` then `.html`) are
  each individually atomic (write-`.tmp`-then-`.replace()`) but not
  jointly atomic as a pair: a crash between them leaves only
  `study_guide.md` on disk. This is a low-severity, self-healing gap --
  `publish_lecture` is idempotent, so re-running it after a partial write
  overwrites both files consistently
  (`test_publish_lecture_recovers_after_a_partial_previous_write`) -- but
  it is a real deviation from every single-artifact phase (2-9), which
  have no such partial-write window because they write exactly one file.
  A future phase with a stricter joint-atomicity requirement would need
  either a two-phase-commit-style write (e.g. write both `.tmp` files
  first, then `.replace()` both) or a single combined artifact.
- `PublishingOrchestrator` has no constructor parameters, matching
  `DocumentAssembler`'s and `MetadataAggregator`'s zero-argument,
  no-dependency-injection convention -- this codebase's LLM-backed
  orchestrators (`CriticOrchestrator`, `DraftOrchestrator`, ...) take
  constructor arguments (`model_path`, `concurrency_limit`) because they
  configure a subprocess; the three deterministic, no-LLM orchestrators
  (Phases 8, 9, 11) do not, and Phase 11 was kept consistent with Phases 8
  and 9 rather than the LLM-backed phases.

## Alternatives Considered

- **Keep Phase 9 in-memory-only; have `PublishingOrchestrator` call
  `MetadataAggregator().aggregate_lecture` without any Phase 9 file
  change.** This was the initial recommendation (smaller diff, zero
  changes to a already-`Accepted` ADR-0009 decision) but the operator
  explicitly chose to add persistence instead; see Rationale above for why
  that choice was taken as given rather than re-litigated.
- **`PublishingOrchestrator` calls `MetadataAggregator().aggregate_lecture(lecture_id)`
  directly and consumes its in-memory return value**, instead of reading
  `workspace/data/11_metadata/{lecture_id}/global_index.json` from disk
  (this ADR's originally accepted design, changed during review). Looked
  attractive because it avoids a redundant disk round-trip for data
  already held in memory, and because `aggregate_lecture`'s own
  precondition checks (`LectureNotFoundError`,
  `SemanticLedgersNotFoundError`) would otherwise seem to duplicate
  `_load_canonical_document`'s own check. Rejected on `staff-architect`
  review: it imports and instantiates a sibling phase's orchestrator
  class, not just a domain model, breaking ADR-0006's self-containment
  rule in a way that is broader and less scoped than this codebase's one
  prior exception (ADR-0007); it transitively re-requires Phase 5's
  `07_semantic_ledgers/{lecture_id}/` directory to still exist even after
  Phase 9 has already run and persisted `global_index.json`, defeating
  the purpose of Decision 1's persistence; and it gives
  `workspace/data/11_metadata/` two potential producers (a future
  standalone Phase 9 entrypoint, and Phase 11's indirect call) with no
  stated ownership. See Rationale for the full comparison.
- **Raw HTML passthrough (`MarkdownIt()`'s `commonmark`-preset default,
  `html: True`)**, i.e. this ADR's originally accepted `_HTML_RENDERER`
  construction. Rejected on `security-reviewer` and
  `principal-code-reviewer` review: verified empirically to pass literal
  `<script>...</script>` (and any other raw HTML) from source Markdown
  straight into `study_guide.html` unescaped -- see Decision 6 and
  Rationale.
- **`markdown` (PyPI package, `Markdown`) instead of `markdown-it-py`.**
  Considered: also pure Python, also lightweight. Not chosen because
  `markdown-it-py` was already a resolved, cached dependency (via `rich`),
  making it the zero-new-download option; `Markdown` would have required a
  new network fetch and lockfile addition for equivalent functionality.
- **`weasyprint`/`pdfkit` for direct PDF export, as the instruction's
  first-listed option.** Rejected per the instruction's own permission to
  avoid "complex binary dependencies... if possible" -- both pull in a
  native rendering stack (Cairo/Pango, or the external `wkhtmltopdf`
  binary) that is a meaningfully heavier, less portable dependency on
  Apple Silicon than a pure-Python Markdown renderer, for a PDF
  requirement the instruction itself made optional ("ou pdf").
