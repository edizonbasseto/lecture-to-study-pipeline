# Roadmap: lecture-to-study-pipeline

## Overview

The pipeline and the study site are already implemented. Phases 1-5 reconstruct that built baseline from the Accepted ADRs and are marked complete; Phases 6-7 are the open work. Phase 6 closes the rollout of the critic gate (ADR 0045): pre-gate derivative material is regenerated or deleted, the site is regenerated so every page carries the uncertified notice, stages 8 and 9 run on real lecture data for the first time, and the blocked-lecture recovery is verified. Phase 7 replaces the placeholder README with operator documentation and an end-to-end walkthrough that states the operating boundaries the ADRs impose. Milestone: v1.0 (baseline adoption and ADR 0045 rollout).

**Reading notes.** "Stage N" is the pipeline stage that ADRs and code call "Phase N" (for example stage 7 is critic review); "Phase N" in this file is a roadmap phase. Baseline status means: ADR Accepted, implementing code present, and baseline checks green at commit `4bfbe46` on 2026-10-14 (2262 tests passed; ruff check and format clean; mypy strict clean). Baseline phases carry no GSD plans because they were built before GSD adoption. Granularity is the standard default (no `config.json` existed); five of the seven phases are retrospective, so planned work is two phases.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Pipeline spine and media-to-evidence** - Registered video becomes frames, OCR text, a corrected transcript and evidence packs under one resumable CLI (baseline)
- [x] **Phase 2: Codex generation and critic review** - Evidence packs become semantic ledgers, drafts and critic-reviewed units through bounded Codex child processes (baseline)
- [x] **Phase 3: Assembly, study material and the uncertified boundary** - Critic-approved units become canonical documents and uncertified student-facing material, blocked per lecture by the critic gate (baseline)
- [x] **Phase 4: Study site** - Allowlisted readers sign in, read, watch and take server-graded mock exams on a hardened site (baseline)
- [x] **Phase 5: Publication boundary and public repository** - Local lectures stay private until published and the repository leaks no portal details, credentials or private terms (baseline)
- [ ] **Phase 6: Critic-gated rollout on real data** - Everything the audience can reach passed the critic gate and carries the current notice; stages 8-9 run on real data
- [ ] **Phase 7: Operator documentation and end-to-end demo** - A new operator can follow the README from a registered video to a served page and knows the boundaries

## Phase Details

### Phase 1: Pipeline spine and media-to-evidence
**Goal**: An operator-registered lecture video becomes frames, OCR text, a corrected transcript and evidence packs, driven by one CLI that skips finished work, isolates failures and never trusts an identifier as a path
**Depends on**: Nothing (first phase)
**Requirements**: SPINE-01, SPINE-02, SPINE-03, SPINE-04, SPINE-05, SPINE-06, INGEST-01, INGEST-02, INGEST-03, INGEST-04, INGEST-05, INGEST-06, INGEST-07
**Success Criteria** (what must be TRUE):
  1. After registering a video with `scripts/register_local_lecture.py`, running stages 2, 3 and 4 produces `04_canonical/{id}.json`, `05_transcribed/{id}_transcription.json` with its terminology-corrections record, and `06_evidence_packs/{id}/pack_NNN.json`
  2. Re-running `run-all` skips every stage whose artifact already exists, and a failing lecture or pack is reported without aborting the others
  3. A lecture identifier outside `^[A-Za-z0-9_-]+$` is refused before any path is built
  4. Slide frames are selected at scene changes with no gap above 37.5 seconds, and slide text comes from Apple Vision OCR
**Plans**: 0 plans (baseline built before GSD adoption)

### Phase 2: Codex generation and critic review
**Goal**: Evidence packs become semantic ledgers, grounded drafts and critic-reviewed validated units through the Codex CLI run as bounded child processes, with no local text-LLM route
**Depends on**: Phase 1
**Requirements**: GEN-01, GEN-02, GEN-03, GEN-04, GEN-05, GEN-06, GEN-07
**Success Criteria** (what must be TRUE):
  1. Running stages 5, 6 and 7 for a lecture writes a ledger, a draft and a validated unit for every evidence pack
  2. A draft the critic rejects is redrafted with the critic's feedback up to two times, and the last draft-and-review pair is persisted even if still failing
  3. A failing Codex child surfaces as an error naming the stage, script and exit code, with captured output saved under `tmp/codex_phase_failures/` and no model bytes in the message
  4. No local text-LLM code path or `--llm-model-*` flag exists; Whisper, Kokoro and `llama-mtmd-cli` are the only local model runtimes
**Plans**: 0 plans (baseline built before GSD adoption)

### Phase 3: Assembly, study material and the uncertified boundary
**Goal**: Critic-approved units become the canonical document, metadata, published guide, Anki deck and the student-facing exam, podcast, audio, flashcards and study guide, all marked uncertified and all blocked for any lecture the critic has not fully approved
**Depends on**: Phase 2
**Requirements**: ASM-01, ASM-02, ASM-03, ASM-04, MAT-01, MAT-02, MAT-03, MAT-04, MAT-05, MAT-06, MAT-07, GATE-01, GATE-02, BND-01, BND-02, BND-03, BND-04
**Success Criteria** (what must be TRUE):
  1. `run-personal-study` runs stages 8, 9, 13, 14 and 15 for a lecture without calling `require_publication_ready`, while `run-all` stops at stage 10 and certificate issuance still raises `QualityCertificationError`
  2. For a lecture with a rejected, missing, stale or mismatched unit, the exam, podcast, personal-study flashcard and study-guide scripts exit without writing any artifact
  3. Every Codex-generated artifact carries the `UNCERTIFIED_PERSONAL_STUDY` marker in its payload, and exam, flashcard and podcast text is Brazilian Portuguese with no source-material references, failing without output after a second defective attempt
  4. The canonical document, global index, `study_guide.md` and `.html`, and `anki_deck.csv` are produced deterministically with Markdown and CSV-formula injection neutralized
  5. Podcast audio is produced with the Kokoro voices `pf_dora` and `pm_alex` and joined into `podcast_final.m4a`
**Plans**: 0 plans (baseline built before GSD adoption)

### Phase 4: Study site
**Goal**: Allowlisted readers can sign in and read lectures, watch videos and take server-graded mock exams on a hardened site that labels all study material as uncertified
**Depends on**: Phase 3
**Requirements**: WEB-01, WEB-02, WEB-03, WEB-04, WEB-05, WEB-06, WEB-07, WEB-08, WEB-09, WEB-10
**Success Criteria** (what must be TRUE):
  1. An allowlisted person signs in with email and password, stays signed in on a phone, and sees the library of processed lectures with unprocessed ones listed as pending; a non-allowlisted address is refused
  2. The home, lecture, reading, watch and both mock-exam pages show the uncertified notice at the top, and the login page does not
  3. The mock-exam page never contains correct answers or explanations, submitting answers returns a server-computed grade, and attempts are throttled per email and lecture
  4. A request path containing `?`, `#`, a backslash, NUL or a dot segment returns 400, every response carries the security headers, repeated wrong passwords slow down without lockout, and overload returns 429
  5. A lecture with a video has a full-screen watch page, and a video-only lecture shows "Vídeo sem material de estudo gerado."
**Plans**: 0 plans (baseline built before GSD adoption)
**UI hint**: yes

### Phase 5: Publication boundary and public repository
**Goal**: Local lectures stay private until explicitly published, and the repository can be public without leaking portal details, credentials or private terms
**Depends on**: Phase 4
**Requirements**: PUB-01, PUB-02, PUB-03, PUB-04, REPO-01, REPO-02, REPO-03, REPO-04
**Success Criteria** (what must be TRUE):
  1. A newly registered local lecture compiles into the private output root, is absent from the public root and the academic catalog, and appears in the withheld-ids list; `--publish` plus a recompile moves it to the public root
  2. Re-registering a local lecture changes only its publish flag, and demotion is refused while a public bundle exists
  3. A video-only lecture compiles to a bundle containing exactly `indice.md`, `.bundle.json` and `video/aula.mp4`
  4. A commit or push containing a denylisted term or a secret is rejected by the local hooks, and CI fails closed when the `DENYLIST` secret is absent
**Plans**: 0 plans (baseline built before GSD adoption)

### Phase 6: Critic-gated rollout on real data
**Goal**: Everything the allowlisted audience can reach was produced through the critic gate and carries the current uncertified notice, and stages 8 and 9 have run on real lecture data
**Depends on**: Phase 5
**Requirements**: ROLL-01, ROLL-02, ROLL-03, ROLL-04
**Success Criteria** (what must be TRUE):
  1. After the pass, no exam, podcast script or audio file, personal-study flashcard deck or study guide generated before the critic gate remains in the trees the site serves: each was regenerated through the gate or deleted, and a lecture whose exam was deleted no longer offers "Responder simulado"
  2. After the site is regenerated, the home, lecture, reading, watch and mock-exam pages carry the current uncertified notice and the browser reports no Content-Security-Policy violation for inline scripts or styles
  3. `run-personal-study` completes stages 8 and 9 on at least one real lecture, and `canonical_document.json` and `global_index.json` exist for it
  4. For a lecture held by a rejected, stale or unreviewed unit, the four derivative scripts write nothing; after the documented recovery (delete units that no longer have a draft, run `scripts/generate_codex_critic_review.py` for the lecture) they produce the exam, podcast, flashcards and study guide
**Plans**: TBD

### Phase 7: Operator documentation and end-to-end demo
**Goal**: A new operator can follow the repository documentation from a registered video to a served, notice-bearing page, and knows the boundaries the ADRs impose
**Depends on**: Phase 6
**Requirements**: DOC-01, DOC-02, DOC-03
**Success Criteria** (what must be TRUE):
  1. The README no longer carries the "under active extraction" placeholder and lists the prerequisites, the make targets, the CLI commands and the stage-to-directory map
  2. A documented walkthrough taking a video from `register_local_lecture.py` through `run-personal-study` and site generation to a signed-in page view has been executed once, and every command in it exists as written
  3. The documentation states the Codex egress rule and the unchecked provider terms, the uncertified status and notice, the absence of any standing toward certification, the blocked-lecture recovery, page regeneration after an inline script or style change, the single-worker site constraint and the FileVault revisit triggers
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7. Phases 1-5 are complete baseline; the next action is planning Phase 6.

**Phase completion gates:** a phase that changes code or executable operational artifacts runs Code Review and Code Secure on the phase-final diff before it closes (operator rule R0.4); see PROJECT.md Constraints for the open question on the canonical mechanism.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Pipeline spine and media-to-evidence | 0/0 | Complete (baseline) | Pre-GSD |
| 2. Codex generation and critic review | 0/0 | Complete (baseline) | Pre-GSD |
| 3. Assembly, study material and the uncertified boundary | 0/0 | Complete (baseline) | Pre-GSD |
| 4. Study site | 0/0 | Complete (baseline) | Pre-GSD |
| 5. Publication boundary and public repository | 0/0 | Complete (baseline) | Pre-GSD |
| 6. Critic-gated rollout on real data | 0/TBD | Not started | - |
| 7. Operator documentation and end-to-end demo | 0/TBD | Not started | - |
