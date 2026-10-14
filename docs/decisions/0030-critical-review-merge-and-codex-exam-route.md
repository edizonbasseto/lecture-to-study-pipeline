# 0030. Critical-review document merge and Codex-based mock-exam route

## Status

Accepted. **Superseded by ADR 0038** as to dual-route reachability: this ADR's "local route stays reachable" clause no longer holds -- the local exam generator is deleted and the Codex route is the only one.

## Context

Reference lecture 10003 ("Cirurgias esplênicas") is the single proof-of-quality
lecture for the personal-study route (ADR 0024's uncertified route). Working
through it end to end surfaced three related problems in the same review pass.

**Duplicate documents.** `critical_summary_document` (rendered as
`resumo_critico.pdf/.md`) and the `SUMMARY` `StudyEdition` (rendered separately
as `apostila_resumida.pdf/.md`) were both built from the exact same source --
`bundle.summary.chapters` -- with `critical_summary_document` only re-prefixing
each point with "Ponto essencial:"/"Alerta clínico:". Shipping two documents
that are the same content twice, restated, serves no one. The operator's own
words: "pra mim os dois podem ser um só, contido em divisões, e com mais
perguntas possíveis de prova."

**A rejected figure atlas that kept shipping.** An earlier round already
decided a separate figures-only document ("atlas") did not make sense -- a
figure belongs next to the text that explains it, not in its own file -- and
built `SelectedFigure`/inline embedding into the complete edition instead. But
`figure_atlas_document`, its `StudyDocumentFigure` DTO, and the script wiring
that staged a `figuras.md/.pdf/.typ` document alongside the inline figures were
never removed: the rejected document kept shipping in parallel with the one
that replaced it.

**No question count that scales with content.** `simulado.md`'s question count
was hardcoded to 20 in the (session-scratchpad, never committed) Codex
generation call. The operator had already forbidden a fixed word-count target
for prose generation elsewhere in this same milestone ("no word-count target or
floor, anywhere, ever" -- evidence completeness is the only criterion); a fixed
question count is the same shape of problem for exam generation.

## Decision

1. **One merged document, `critical_review_document`, replaces both.** It
   divides by chapter, and within each chapter by content type: "Pontos
   essenciais", then "Alertas clínicos" (only when the chapter has any), then
   "Perguntas de revisão" (only when review questions are grounded for that
   chapter). The standalone `SUMMARY` edition render (`apostila_resumida`) is
   dropped; `bundle.summary` itself stays in `PersonalStudyBundle` (flashcards
   still derive from it), it is just no longer rendered as its own document.
   Filename `resumo_critico` is kept as the surviving output name --
   `apostila_resumida` is retired, not renamed.

2. **The figure atlas is deleted outright**, not fixed: `figure_atlas_document`,
   `StudyDocumentFigure`, `_render_typst_figure`, `_render_markdown_figure`,
   `format_figure_timestamp`, the `.figures` field on `StudyDocumentSection`,
   and the `figuras.md/.pdf/.typ` write in `_write_figures` (which still stages
   the copied frame images themselves -- the inline embedding path needs those
   on disk -- it just no longer also builds a document from them).

3. **`MockQuestion` gains an optional `pack_sequence: int | None = None`
   field**, so a review question can be placed in the chapter it was written
   for. It is optional and defaulted so the certified local-llama.cpp Phase 13
   route (`exams/generator.py`), which has no notion of a learning-unit split,
   is unaffected -- only the new Codex route populates it.

4. **Question count scales with the lecture's own content**: one question per
   grounded clinical fact in a unit's own Semantic Ledger, capped per unit
   (`--questions-per-unit-cap`, default 5) so a single dense unit (10003's
   densest unit has 51 ledger facts) does not produce 51 near-duplicate
   questions, with a floor across the whole exam (`--minimum-total-questions`,
   default 20) backfilled from the densest remaining units if the capped total
   falls short. For 10003 this produced 65 questions across 13 grounded units
   (one unit had zero facts and was skipped) -- the cap bound it, the floor
   never had to fire.

5. **A new script, `scripts/generate_codex_mock_exam.py`, generates the exam
   via the Codex CLI (`codex exec`)**, not the OpenAI API: the operator's
   ChatGPT subscription is Pro, and billing the API separately is explicitly
   ruled out (same reasoning already applied to drafting/podcast/flashcard
   generation this milestone). One `codex exec` call per learning unit, prompt
   built by embedding `TypeAdapter(list[MockQuestion]).json_schema()` as text
   (not `--output-schema`, which chokes on `MockQuestion.options: dict[str,
   str]`), with the shared `lecture_study.ops.llm_response_contract` JSON
   contract parser and one retry on schema or question-count mismatch, reusing
   the same contract module `exams/generator.py`'s local-LLM route already
   uses. Each `codex exec` subprocess runs with its `cwd` and `-C` working root
   set to a fresh, empty, per-call scratch directory -- never the repository
   checkout -- since the prompt is fully self-contained (facts are inlined as
   text, no file access is functionally needed) and the repository root holds
   `.env` (2026-09-15 security review finding: least privilege, and a defense
   line against a prompt-injection payload in lecture content asking the model
   to read and echo a local file).

## Alternatives considered

1. **Keep `resumo_critico` and `apostila_resumida` as two documents.** Rejected
   by the operator directly -- see Context.
2. **Divide the merged document by unit instead of by chapter.** The operator
   chose "por tipo de conteúdo dentro de cada capítulo" (sub-blocks inside each
   chapter) over one-division-per-unit; a chapter can span multiple units (the
   summary-chapter grouping heuristic already merges related units), so
   dividing by chapter is coarser than by unit but matches the document the
   operant already reads the rest of the apostila through.
3. **Ground review questions with `StudyQuestion.source_sequences`
   (the older, deterministic, non-LLM question builder) instead of adding
   `pack_sequence` to `MockQuestion`.** Considered and rejected by the
   operator when presented directly: the real `simulado.md` on the live site
   is sourced from the inference-generated `MockExam`, not the deterministic
   template (`mock_exam_document` stays reachable per ADR 0024's own D-10, but
   is not what ships); grounding the merged document's questions from a
   different, unshipped question set would desynchronize the two.
4. **Literal "one question per raw ledger fact", uncapped.** 10003's raw
   ledger-fact count is 377 across 14 units (one unit alone has 51). Confirmed
   with the operator directly rather than guessed at, given this session's own
   prior history of guessing at a numeric target and being corrected twice
   (word-count) and three times (Typst paragraph spacing) -- the operator chose
   the per-unit cap instead.
5. **`--output-schema` for the Codex call.** Rejected: an earlier session in
   this same milestone found it chokes on `MockQuestion.options: dict[str,
   str]`; the schema-as-text-in-prompt approach `exams/generator.py` already
   uses for the local llama.cpp route was reused instead.

## Consequences

- **Positive:** one document to read instead of two restating the same
  content; the exam's size now reflects how much a lecture actually covers
  instead of an arbitrary fixed number; the figure atlas the operator already
  rejected stops shipping.
- **Positive:** reusing `lecture_study.ops.llm_response_contract` for the Codex
  route means both mock-exam-generation routes (certified local llama.cpp,
  uncertified Codex) share one JSON-contract parser and one retry-feedback
  vocabulary instead of each inventing its own.
- **Negative, accepted:** `scripts/generate_codex_mock_exam.py` calls a real
  external CLI (`codex exec`) per unit -- 13 calls, roughly 8 minutes wall
  clock for 10003 -- with no automated test exercising the actual subprocess
  (infeasible without either a live Codex session in CI or a fake binary); its
  pure logic (fact formatting, count derivation, the supplemental-backfill
  loop, the count-mismatch retry path) is unit-tested with a stubbed
  `_call_codex_for_questions` instead.
- **Neutral:** `build_ledger_grounded_drafts` (`compiler.py`) is now called
  from nowhere in `main()` -- it is the function whose unconditional use was
  the actual root cause of an earlier "bullet list instead of prose" defect
  (see its own docstring and
  `tests/test_personal_study_generator.py::TestApostilaUsesDraftingProseDirectly`)
  -- kept in place, public and unit-tested, as a still-correct utility rather
  than deleted, since a future document type may want literal ledger-fact
  bullets again.
