# 0045. Critic gate for derivative study material, remote-model egress, and site audience

## Status

Accepted. 2026-10-14. Amends ADR 0024 (input source of the uncertified
personal-study route and its audience bound), ADR 0030 (input of the Codex
mock-exam script), ADRs 0001, 0010, 0015, and 0032 (the local-only mandate, for
the LLM phases), ADR 0023 (records that sections 15 and 16 are superseded), and
ADR 0029 (uncertified notice on the site). Supersedes ADR 0037 decision 2 (podcast grounded on `08_drafts`).
Records how ADR citations are ordered (section 6).

## Context

A consolidation of all records found one contradiction and five gaps.

ADR 0024 requires the uncertified personal-study route to build its input from
the Phase 8 canonical document, so that a lecture with an unresolved critic
finding stays blocked. ADR 0037 decision 2, and the Codex mock-exam and
flashcard scripts that preceded it, read `workspace/data/08_drafts` directly
and bypass Phase 8. The code confirmed the consequence: the mock-exam, podcast,
personal-study flashcard, and study-guide scripts never consulted the critic
verdict. A draft rejected by the critic could reach student-facing material.

The same check showed a second defect. The critic's repair loop writes the
repaired draft into `workspace/data/09_validated_units/<lecture>/unit_*.json`
(`ValidatedLearningUnit.content`); `08_drafts` keeps the version before repair.
Filtering `08_drafts` by the verdict would therefore still publish text the
critic never approved.

The five gaps:

- No record governs the confidentiality of lecture content sent to the Codex
  CLI, which runs on a remotely hosted model. ADR 0010 delegates the question
  to ADRs 0030, 0035, and 0038, which do not address it. The local-only mandate
  cited by ADRs 0001, 0010, 0015, and 0032 was never amended.
- ADR 0023 sections 15 and 16 describe a local llama.cpp runtime with three
  independently authenticated models. ADR 0038 deleted that runtime but
  recorded only a stack note.
- ADR 0024 bounds the route to the single student who requested the material.
  The study site (ADRs 0029, 0039, 0041, 0042) delivers that material to an
  allowlisted audience of more than one person.
- Phase 18 (personal-study flashcards) is referenced but never defined.
- ADRs cite each other in both directions, which reads as a dependency cycle.

## Decision

### 1. Derivative material is built only from critic-approved units

Every script that turns drafting output into student-facing material loads its
units through `load_critic_approved_drafts` in
`src/lecture_study/assembly/assembler.py`:

- `scripts/generate_codex_mock_exam.py` (Phase 13),
- `scripts/generate_codex_podcast.py` (Phase 14),
- `scripts/generate_codex_personal_study_flashcards.py` (Phase 18),
- `scripts/generate_personal_study_guides.py` (the study guide).

The function reads `09_validated_units`, not `08_drafts`, and returns the
post-repair content of each unit in `pack_sequence` order. It applies the
Phase 8 completeness rules, and stricter ones, without writing a canonical
document. The whole lecture is blocked, and no material is written for it,
when:

- the lecture has no Phase 6 or Phase 7 directory, or no file in either;
- the sequence has a gap or a duplicate;
- any unit has `review.passed` false;
- the drafts and the units are not the same set of sequence numbers: a draft
  the critic never saw, or a review whose draft no longer exists;
- a draft was modified after its unit was written, meaning the review covers
  an earlier draft;
- a draft's echoed `pack_sequence` disagrees with its file name, meaning the
  critic, which selects the evidence pack by that echo, reviewed it against
  another pack;
- a unit's `lecture_id` or `pack_sequence` disagrees with the lecture and the
  file name it is stored under.

Sequence numbers come from the file names the pipeline writes
(`draft_NNN.json`, `unit_NNN.json`), never from values a model echoed. The
"modified after review" check compares file modification times. A copy that
does not preserve them can block a valid lecture, or hide a stale one.

A blocked lecture is never published partially. Skipping only the rejected
units was rejected because the reader could not tell that the material was
incomplete.

The identity of each returned draft (`lecture_id`, `pack_sequence`) is taken
from the unit, not from the repaired draft. A repaired draft is model output
whose echoed identity may drift; the critic anchors the unit's identity to the
evidence pack.

`08_drafts` is still read, but only for the checks above, never as content. The semantic
ledgers the exam and flashcard scripts read per unit need no extra filter:
the lecture is processed only when every unit passed.

Material generated before this decision was not gated. The mock exams, podcast
scripts and audio, personal-study flashcard decks, and study guides produced
earlier must be regenerated or deleted. The site's mock-exam page and the
study-guide script's personal-study inference merge read those artifacts
without checking how they were produced.

This amends ADR 0024: the input is no longer the Phase 8 document, but the
Phase 8 gate still governs it. It supersedes ADR 0037 decision 2 and amends
the input of the ADR 0030 mock-exam script.

### 2. Lecture content sent to the remote model

The Codex route sends evidence packs, transcripts, ledgers, and drafts to a
remotely hosted model. The resulting risk is accepted under one rule:

- The Codex route is used only for lecture content the operator has verified
  to carry no patient-identifying data. That covers the spoken narrative and
  the slide text extracted by OCR (case records, imaging labels). Owning the
  content does not by itself qualify it; synthetic content qualifies.
- Content that may carry patient-identifying data is processed only by a
  local model route. The planned local adapter of the LLM gateway provides
  that route. Until it exists, such content is not processed past Phase 4.

Applying this rule is the operator's responsibility; no technical control
detects patient-identifying data before a call. The retention and training
terms of the remote provider are not recorded here and must be checked against
the account in use. This amends the local-only
mandate cited by ADRs 0001, 0010, 0015, and 0032 for the LLM phases, and
answers the question ADR 0010 delegated.

### 3. Site audience and the uncertified notice

ADR 0024's audience bound is widened from the single requesting student to
the allowlisted site audience defined by ADRs 0029 and 0039. The material
stays uncertified, and every site page that renders study material says so.

The notice is defined once in `src/lecture_study/site/renderer.py`
(`UNCERTIFIED_NOTICE`, `uncertified_notice_html`). It appears at the top of
the generated home, lecture, reading, and watch pages and of both mock-exam
pages (`src/lecture_study/site/quiz.py`). The login page carries none. The
text is:

> Material de estudo gerado automaticamente a partir da aula, sem certificação
> nem revisão clínica humana. Confira sempre com a fonte original.

Files the site serves unchanged (PDF, Markdown sources, audio, video) cannot
carry an HTML notice. They are reached only from the pages above.

### 4. ADR 0023 sections 15 and 16

Sections 15 and 16 were superseded by ADR 0038; this record states it
explicitly. They are not a planning target.
They describe a runtime that no longer exists: the three authenticated local
actors with distinct model hashes, the descriptor-backed launch, and the
invocation registry. Section 6 still defines the certified publication path,
which stays unreachable (ADR 0024).

Any future certified route must decide in a new record how a generator and
its evaluator are shown to be independent. This matters when both are remote
models, or when the local adapter of the LLM gateway serves one of them.

### 5. Phase 18: personal-study flashcards

Phase 18 is `scripts/generate_codex_personal_study_flashcards.py`. It is a
standalone script that the operator runs per lecture; the orchestrator does
not invoke it. Its deck in `workspace/data/18_personal_study_flashcards`
feeds the study guide. It is independent of the deterministic Phase 12 Anki
deck (ADR 0012), which stays in `run-all`. Like the other derivative scripts,
it is subject to section 1.

### 6. How ADR citations are ordered

When an older record cites a newer one, the citation is an amendment or
erratum note that points forward. The older text stays as history. Precedence
follows only the explicit amends and supersedes statements in each record's
Status section, and those form an acyclic order. A citation in both
directions is therefore not a dependency cycle.

## Alternatives considered

- **Require Phase 8 assembly before Phases 13 to 15** (keep ADR 0024
  literally). Rejected: assembly has not run on real data, and the gate
  semantics are what matter, not the canonical file.
- **Document the bypass as accepted.** Rejected: rejected drafts would keep
  reaching student-facing material.
- **Filter `08_drafts` by the verdict.** Rejected: it publishes the version
  before repair, which the critic never approved.
- **Skip rejected units instead of blocking the lecture.** Rejected: it yields
  incomplete material with no visible signal.
- **Serve only certified material on the site.** Rejected: no material is
  certified today, so the site would be empty.

## Consequences

- **Positive:** in material generated after this decision, no text the
  critic rejected or never saw reaches the exam, podcast, flashcards, or study
  guide.
- **Positive:** the published text is the text the critic approved.
- **Positive:** readers of the site see that the material is uncertified.
- **Negative, accepted:** one rejected unit blocks the whole lecture until the
  critic approves it. Recovery after a rejection or a re-draft: delete the
  lecture's units that no longer have a draft, then run
  `scripts/generate_codex_critic_review.py` for the lecture directly. The
  orchestrator decides whether to skip Phase 7 by file sequence numbers
  alone, so it does not rerun the critic after a re-draft, and the
  personal-study command never runs Phase 7.
- **Negative, accepted:** the study-guide script stops at the first blocked
  lecture in a multi-lecture run. This follows its existing convention for
  per-lecture errors; lectures earlier in the run are already published.
- **Negative, accepted:** the egress rule in section 2 is procedural, not
  enforced by code.
- **Negative, accepted:** PDFs and other files served unchanged carry no site
  notice.
- **Operational:** derivative artifacts generated before this decision must
  be regenerated or deleted (section 1).
- **Operational:** pages already deployed keep the old footer notice until the
  site is regenerated.
