# 0012. Deterministic Anki Flashcard Generation (CSV Export)

## Status

Accepted.

## Context

Phases 10-11 left every lecture's clinical facts consolidated in one
`LectureGlobalIndex` (Phase 9) and published as a study guide (Markdown/
HTML). `.prompts/fase12_anki_flashcards.md` asked for a Phase 12 that
converts that same index into an Anki-importable spaced-repetition deck
(CSV), using deterministic Python data transformation -- explicitly no
`llama.cpp`/LLM pass -- to spare Apple Silicon VRAM and guarantee zero
clinical hallucination, since the deck's content is a strict copy of
already-vetted Phase 5 extraction.

A blocking gap surfaced before any flashcard code could be written:
`LectureGlobalIndex.glossary_terms` was `list[str]` (deduplicated
`entity_name` strings only, per ADR 0009) with no `clinical_context`
preserved, but the instruction's glossary card needs Back to be
"[Contexto/Definição original]". The operator was asked and chose to
extend Phase 9's model (`glossary_terms: list[SemanticFact]`) rather than
ship a glossary flashcard with no real definition or skip the glossary
category -- see ADR 0009's Consequences section for that retrofit's own
record.

## Decisions

1. **`Flashcard` domain model** (`domain/models.py`, appended after
   `CanonicalLectureDocument`): `front: str`, `back: str`, `tags:
   list[str]`, `model_config = ConfigDict(strict=True)` -- matching every
   other domain model's strict-validation convention.
2. **`GlobalIndexNotFoundError`** (new, `domain/errors.py`, subclasses
   `LectureNotFoundError`): raised when
   `workspace/data/11_metadata/{lecture_id}/global_index.json` does not
   exist. The instruction names this exception explicitly, even though
   `publishing/pipeline.py` (Phase 11) already guards the same missing-file
   condition by raising `LectureNotFoundError` directly (see that class's
   docstring). Subclassing (rather than a disjoint `RuntimeError` subclass,
   the first-draft shape a `principal-code-reviewer` pass on this ADR
   flagged) honors the instruction's literal class name while keeping
   `except LectureNotFoundError` -- this codebase's established catch
   pattern for every other "lecture hasn't reached this pipeline stage"
   condition -- able to catch Phase 12's check too. See Alternatives
   Considered.
3. **`AnkiDeckGenerator.generate_deck`** (`flashcards/generator.py`):
   - Validates `lecture_id` as a `SafeIdentifier` before any `Path`
     splice, matching the `metadata`/`publishing`/`assembly`/`critic`/
     `semantic`/`drafting` convention.
   - Reads `workspace/data/11_metadata/{lecture_id}/global_index.json`
     directly (not via `MetadataAggregator`), matching `publishing/
     pipeline.py`'s "read the prior phase's persisted artifact" precedent.
   - Maps each `LectureGlobalIndex` list to one `Flashcard` per entry, in
     `glossary_terms` / `dosages` / `red_flags` order (matching
     `markdown_compiler.py`'s appendix section order):
     - Glossary: Front `"O que é {entity_name}?"`, Back `clinical_context`,
       tag `glossario`.
     - Dosages: Front `"Qual a diretriz/dosagem para {entity_name}?"`,
       Back `clinical_context`, tag `dosagem`.
     - Red flags: Front `"🚨 ALERTA CLÍNICO: Quais as
       contraindicações/alertas para {entity_name}?"`, Back
       `clinical_context`, tag `red_flag`.
   - Persists to `workspace/data/13_flashcards/{lecture_id}/anki_deck.csv`
     via Python's native `csv` module, write-to-`.tmp`-then-`replace`
     (Phase 9/11's atomic-write convention), no header row, three columns
     (`front`, `back`, space-joined `tags`).
4. **CSV sanitization** (`AnkiDeckGenerator._sanitize_field`):
   - Embedded `\r\n`/`\n`/`\r` are replaced with `<br>` (Anki's own
     multi-line-field convention) before the field reaches `csv.writer` --
     `csv.writer` has no notion of "this is one logical Anki field", so an
     unconverted raw newline would either split one card across CSV rows
     (unquoted) or survive as a literal newline Anki's own CSV importer
     does not expect as an intra-field line break.
   - Comma-containment and double-quote-escaping are *not* hand-rolled:
     `csv.writer` already quotes any field containing a comma or a double
     quote and doubles embedded quotes per RFC 4180. Re-implementing that
     escaping manually before handing the field to `csv.writer` would
     double-escape it.
   - A field starting with `=`, `+`, `-`, `@`, or a tab is prefixed with a
     single quote (`'`) -- checked both on the raw field and on the field
     with leading spaces/tabs stripped, so `" =SOMA(...)"` and
     `"\t=SOMA(...)"` are caught too, not just a trigger character in the
     literal first position. `front`/`back` originate in Phase 5's LLM-
     extracted lecture free text (OCR'd slides, transcripts), so a
     malicious/corrupted source could plant a leading `=SOMA(...)`-style
     string, with or without leading whitespace; if this CSV is ever
     opened in a spreadsheet application instead of imported into Anki, an
     unprefixed leading trigger would be evaluated as a formula (CSV/
     formula injection, CWE-1236) rather than read as literal text. The
     instruction's own text only calls out newline/quote handling, but
     this codebase's Phase 10-11 review board already closed an HTML-
     injection and an embedded-newline-block-injection finding on the same
     class of LLM-free-text-into-a-structured-output-format risk; this
     decision applies that same scrutiny proactively rather than waiting
     for a finding. A `security-reviewer` pass on this ADR's first draft
     found the original `=`/`+`/`-`/`@`-only, raw-`startswith`-only check
     bypassable by a leading tab or leading whitespace before the trigger
     character; both gaps are closed by the current implementation.

## Rationale

- **Why Python string templating instead of a second `llama.cpp` pass.**
  Identical framing to ADR 0009/0011: the source facts are already
  extracted and deduplicated; a flashcard is a fixed textual
  transformation of fields that already exist, not a new inference. An
  LLM call here would spend Apple Silicon GPU/VRAM re-deriving text that
  is already present verbatim in `LectureGlobalIndex`, for zero
  additional clinical value and a real risk of paraphrase-induced
  hallucination the instruction explicitly wants to avoid.
- **Why `csv.writer` instead of manual string joining with commas.** The
  instruction asks for "não quebrar o arquivo .csv"; hand-joining
  `f"{front},{back},{tags}"` would break the moment any field contains a
  comma (a near-certainty in clinical free text, e.g. `"100mg, VO"`).
  `csv.writer` is the standard-library tool built exactly for this, and
  using it removes an entire class of hand-rolled-escaping bugs.
- **Why the formula-injection guard includes `\t`/tab and a leading-
  whitespace-stripped check, not just `=`/`+`/`-`/`@` on the raw field.**
  OWASP's CSV/formula-injection guidance (CWE-1236) lists tab and carriage
  return alongside `=`/`+`/`-`/`@` as trigger characters some spreadsheet
  parsers act on; `\r` cannot reach this check (already converted to
  `<br>` beforehand), but a raw leading tab, or plain whitespace preceding
  any trigger character, is a real bypass of a bare `startswith()` on the
  four punctuation characters alone -- confirmed as a finding during this
  ADR's own review (see Decision 4).

## Consequences

- `domain/models.py` now holds a fifth lecture-scoped output model
  (`Flashcard`) alongside `LectureMetadata`, `LectureGlobalIndex`,
  `CanonicalLectureDocument`; its docstring states its owning phase
  explicitly, matching the file's established convention for avoiding
  conflation between similarly-scoped classes.
- `domain/errors.py`'s `LectureNotFoundError` and its new subclass
  `GlobalIndexNotFoundError` both fire for the same missing-
  `global_index.json` condition, at two different call sites
  (`publishing/pipeline.py` raises the base class directly,
  `flashcards/generator.py` raises the subclass). A future reader must not
  assume the two call sites raise the exact same type for that file's
  absence, but `except LectureNotFoundError` does catch both; both
  docstrings cross-reference each other to close that ambiguity.
- `metadata/aggregator.py` and `publishing/markdown_compiler.py` were
  touched by this phase's prerequisite retrofit (see ADR 0009's
  Consequences), even though the Phase 12 instruction itself only scoped
  `domain/models.py`, `domain/errors.py`, and a new `flashcards/`
  package -- a consequence of the glossary Back gap discovered before
  any Phase 12 code could be written.
- The deck is regenerated in full on every `generate_deck` call (no
  incremental/merge logic against a prior `anki_deck.csv`); re-running
  Phase 12 for a lecture whose `global_index.json` changed always
  produces a fresh, fully consistent deck rather than a partially stale
  one, at the cost of re-importing the whole deck into Anki after any
  upstream change (accepted: Anki's own CSV import is idempotent per-note
  when front text is stable).

## Alternatives Considered

- **Back = the term itself (no context) for glossary cards, keeping
  `glossary_terms: list[str]` unchanged.** Rejected by the operator:
  "quality is non-negotiable, a flashcard with no context is useless."
  See Context.
- **Skip the glossary category in this phase, ship only dosages/red_flags
  now.** Considered as a lower-risk alternative to touching Phase 9's
  model. Rejected for the same reason as above -- the instruction's three-
  category mapping is the phase's actual scope, and silently dropping one
  category would under-deliver against it without being asked to.
- **A single shared exception for the missing-`global_index.json`
  condition (reuse `LectureNotFoundError` directly here too, matching
  Phase 11, with no new class at all).** Considered, not taken as the
  final shape: the instruction explicitly names `GlobalIndexNotFoundError`
  as a new class to add. Note this is the opposite situation from ADR
  0009's own Decision 1, which is a *deviation from* an instruction's
  literal class name (the instruction there said `LectureMetadata`; the
  operator chose `LectureGlobalIndex` instead, to avoid shadowing the
  pre-existing Phase 1 class) -- not a precedent for honoring literal
  instruction text over consistency, as an earlier draft of this ADR
  mischaracterized it. The final shape taken here is a middle path a
  `principal-code-reviewer` pass on this ADR's first draft proposed:
  `GlobalIndexNotFoundError(LectureNotFoundError)`, a subclass. That
  satisfies the instruction's literal requirement (the named class exists
  and can be raised/caught by name) while `except LectureNotFoundError`
  still catches it, closing the catch-compatibility gap a fully disjoint
  `RuntimeError` subclass would have left for any future caller that
  handles every other "lecture hasn't reached this pipeline stage"
  condition through that one base class.
- **Hand-rolled quote-doubling/comma-detection instead of `csv.writer`.**
  Rejected: `csv.writer` is the standard-library tool for exactly this
  problem; reimplementing RFC 4180 escaping by hand only reintroduces the
  bug class the module already closes.
