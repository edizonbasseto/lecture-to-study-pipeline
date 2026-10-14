# Synthesis summary

Entry point for `gsd-roadmapper`. Mode: new. Existing context: none. Precedence: ADR > SPEC > PRD > DOC (no per-document override set). This run re-evaluates the earlier synthesis against ADR 0045 and the edited ADRs 0001, 0010, 0015, 0023, 0024, 0029, 0030, 0032, 0037.

## Documents by type

- ADR: 42 (0001-0016, 0020-0045; 0017-0019 are absent by design, see ADR 0044)
- PRD: 0
- SPEC: 0
- DOC: 0
- Unknown or low confidence: 0 (ADR 0013 is medium confidence; handled as an ADR)

## Decisions

- Locked: 42 of 42 (all Status "Accepted"). Source paths are the files under `docs/decisions/`, listed one per entry in `decisions.md`.
- Ordering is by explicit amends and supersedes statements only (ADR 0045 section 6). The graph of those statements has no cycle.
- Current-state anchors the roadmap should plan from:
  - Local LLM route removed (ADR 0038); LLM phases 5, 6, 7, 13, 14 run as Codex scripts invoked as child processes by the orchestrator; Whisper/ASR, the Kokoro TTS voice (ADR 0032) and llama-mtmd-cli vision captioning stay local.
  - Derivative material (exam, podcast, personal-study flashcards, study guide) is built only from critic-approved units in `09_validated_units` through `load_critic_approved_drafts`; a blocked lecture is never published partially (ADR 0045 section 1).
  - Certified publication path (Phase 10, ADR 0023 section 6) stays unreachable; the uncertified personal-study route (ADR 0024) is the operable path, with the uncertified notice on every site page that renders study material (ADR 0045 section 3).
  - Site: allowlisted audience, signed sessions, hash-based CSP, server-graded quiz (ADRs 0029, 0039, 0040, 0041, 0044); local lectures are private unless published (ADRs 0042, 0043).
  - Phase 18 = `scripts/generate_codex_personal_study_flashcards.py`, standalone, outside the orchestrator (ADR 0045 section 5).

## Requirements

- Extracted: 0 (no PRD input). See `requirements.md`.

## Constraints

- Extracted: 0 (no SPEC input). See `constraints.md`.

## Context topics

- 0 (no DOC input). See `context.md`.

## Conflicts

- 0 blockers
- 3 warnings (competing-variants bucket: 0; the three warnings are ambiguity items that need user approval)
- 19 auto-resolved or informational

Detail: `.planning/INGEST-CONFLICTS.md`

Disposition of the earlier run (1 blocker, 5 warnings):
- Blocker (ADR 0024 versus ADR 0037, Phase 8 versus Phase 6 input): resolved by ADR 0045 section 1 (explicit supersession of ADR 0037 decision 2, amendment of ADR 0024 and ADR 0030). Now INFO.
- Citation cycles: resolved by ADR 0045 section 6; the amends and supersedes graph was checked and has 0 cycles. Now INFO.
- Codex egress confidentiality: governed by ADR 0045 section 2; a narrower warning remains (undefined "LLM gateway" local adapter, rule not enforced by code, provider terms unrecorded).
- ADR 0023 sections 15-16: superseded explicitly; a narrower warning remains for unmarked restating text (unnumbered runtime subsection, implementation-status list, GGUF-bound part of section 6).
- ADR 0024 single-student bound versus the site: audience widened by ADR 0045 section 3; a narrower warning remains for the "batch" and "distribution-facing" wording.
- Phase 18 flashcard route: defined by ADR 0045 section 5. Now INFO.

## Open items stated in the ADRs (not conflicts)

- ADR 0037: the single whole-lecture podcast prompt has no size check and the retry resends the same size (not yet observed on a real lecture; ADR 0037 says to revisit if it is).
- ADR 0028: the 25 transcripts already on disk are not corrected; re-transcription is deferred.
- ADR 0045: material generated before the decision (exams, podcast scripts and audio, personal-study flashcard decks, study guides) must be regenerated or deleted; deployed pages keep the old footer notice until the site is regenerated; the orchestrator does not rerun Phase 7 after a re-draft (recovery is a direct run of `scripts/generate_codex_critic_review.py`); the egress rule is procedural.
- ADR 0044: changing any inline site script or style requires regenerating the site pages because the CSP lists inline code by hash.
- ADR 0036: `generate_codex_critic_review.py` imports a private function of `generate_codex_drafts.py` without a shared package boundary.
- ADR 0021 and ADR 0025: frame sampling cannot guarantee visual recall; lectures extracted before ADR 0025 keep the 150-second density until re-extracted.

## Files

- `decisions.md`, `requirements.md`, `constraints.md`, `context.md` in `.planning/intel/`
- Conflicts report: `.planning/INGEST-CONFLICTS.md`
- Classifications: `.planning/intel/classifications/`
