# 0033. Exam questions illustrate with the same figure already selected for their unit

## Status

Accepted.

## Context

The operator asked directly why no exam question ever carried an image
(2026-09-15 handoff, item 5: "Dúvida: nenhuma questão usa imagens?
Pq???"). Investigation confirmed a design gap, not a rendering bug:
`MockQuestion` (`src/lecture_study/domain/models.py`) had no figure
field, and the exam-question generation path (`exams/generator.py`,
Phase 13) was never wired to any figure at all -- `FigureCaption` and
figure selection existed only for the complete edition's chapters.

`generate_personal_study_guides.py` already computes
`figures_by_pack_sequence: dict[int, tuple[SelectedFigure, ...]]` via
`_select_figures_by_pack_sequence` -- one captioned lecture frame per
learning-unit pack, junk-caption-filtered, longest-caption tiebreak --
to place exactly one figure per unit inline in the complete edition
(ADR for that feature: see the "One figure per unit" rationale
comment on `_select_figures_by_pack_sequence` itself; no dedicated ADR
was written for it, it shipped as part of the same commit that added
that function). `MockQuestion` already carried an optional
`pack_sequence: int | None` field (populated only by the Codex-driven
personal-study exam route, `scripts/generate_codex_mock_exam.py`), tying
each inference-generated question to the same learning unit its figure
selection is keyed on.

## Decision

Reuse `figures_by_pack_sequence` for exam questions instead of building
a second, separate selection: `inference_mock_exam_document`
(`src/lecture_study/study_materials/bundle.py`) takes an optional
`figures_by_pack_sequence` parameter and, for each question, looks up
`figures_by_pack_sequence.get(question.pack_sequence, ())`, taking the
first (and, by construction, only) candidate. No new selection
heuristic, no change to `MockQuestion`'s schema, no change to
`exam.json`'s persisted shape -- the figure is resolved purely at
render time from data both call sites (the complete edition and the
exam) already have.

**Scope: `inference_mock_exam_document` (the "Questões" page) only**,
per explicit operator decision when presented with the alternative of
also illustrating `critical_review_document`'s interleaved review
questions. `mock_exam_document`'s deterministic `StudyQuestion` route
(no `pack_sequence`, no figure catalog involved) is unaffected.

**"First candidate wins", not a second selection pass.**
`_select_figures_by_pack_sequence` already reduces each pack's
candidates to at most one (`max(..., default=None)`); this decision
does not introduce a second heuristic to pick a different figure for
the question than the one already shown in that unit's own chapter --
the operator explicitly chose reuse over a "second-best candidate"
alternative that would risk a question having no figure at all whenever
its unit had exactly one usable frame (the common case).

**`StudyDocumentSection` gains `item_figures`, not a wider `items`
type.** `items: tuple[str, ...]` is rendered by both
`render_study_document_markdown` and `render_study_document_typst`
through a single shared per-item string (`render_academic_list_item`
for Typst escapes it as literal text -- a raw `#figure()` call
embedded in that string would render as escaped text, not an actual
image). Rather than restructure `items` into a richer type across all
14 existing `StudyDocumentSection(...)` construction sites in
`bundle.py`, a new optional `item_figures: tuple[SelectedFigure |
None, ...] | None` field was added, aligned by index to `items`,
defaulting to `None` (which every section but the exam's "Questões"
list keeps). Both renderers place the figure immediately after its own
item, before the next one.

## Alternatives considered

- **Persist the selected figure on `MockQuestion` itself** (schema
  change, `exam.json` shape change). Rejected: the figure is fully
  derivable at render time from `pack_sequence` plus the same catalog
  data already loaded for the complete edition; persisting it would
  duplicate that selection and risk drifting from the chapter's own
  figure if the catalog or heuristic changes between generation runs.
- **A distinct second-best-candidate heuristic for questions**, so a
  question's figure never repeats the one already shown in its
  chapter. Rejected (operator decision): most units have exactly one
  usable candidate frame after junk filtering, so this would leave most
  questions without a figure at all -- the opposite of the goal.
- **Widen `StudyDocumentSection.items` to a richer per-item type**
  (text + optional figure) instead of a parallel `item_figures` array.
  Considered for uniformity, but would have touched all 14 existing
  construction sites in `bundle.py` for a feature scoped to exactly one
  of them; the parallel-array field with an enforced length guard
  (`StudyDocumentSection.__post_init__`) achieves the same per-item
  alignment with a far smaller diff.

## Consequences

- Every other `StudyDocumentSection` caller in `bundle.py` (13 sites)
  is unaffected: `item_figures` defaults to `None`, and both renderers
  skip figure placement entirely when it is `None`.
- A question whose `pack_sequence` has no entry in
  `figures_by_pack_sequence` (unit had no usable candidate frame after
  junk filtering) renders with no figure, exactly as before this
  change -- never an error, matching `_select_figures_by_pack_sequence`'s
  own existing "no figure for this unit" behavior for the chapter case.
- Verified against lecture 10003's real compiled `simulado.pdf`: a
  histology slide renders directly under question 1, captioned "Figura
  1", before question 2 -- the same frame already embedded in that
  lecture's complete-edition chapter for the corresponding unit.
