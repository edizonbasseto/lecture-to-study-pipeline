# Roadmap: lecture-to-study-pipeline

## Overview

The pipeline and the study site are already implemented. Phases 1-5 reconstruct that built baseline from the Accepted ADRs and are marked complete; Phases 6-9 are the open work. The repository is a public portfolio artifact, and the milestone ends when it is switched to public after a final gate. Phase 6 places every LLM-backed stage behind one `LlmGateway` port, with the current Codex behavior as the default adapter and a local llama.cpp adapter with no network egress, chosen by configuration; a new ADR amends ADR 0038 and closes ADR 0045 section 2. Phase 7 builds a synthetic demo lecture authored for the repository, runs it end to end to a served page (stages 8 and 9 and the blocked-lecture recovery included) and reports the pipeline's quality with measured numbers. Phase 8 replaces the placeholder README with a portfolio README, a demo walkthrough and the operating boundaries the ADRs impose. Phase 9 scans the full history, closes a final whole-repository review and switches the repository to public. Milestone: v1.0 (baseline adoption, pluggable LLM gateway, synthetic end-to-end demo with measured results, portfolio README, pre-public gate).

**Reading notes.** "Stage N" is the pipeline stage that ADRs and code call "Phase N" (for example stage 7 is critic review); "Phase N" in this file is a roadmap phase. Baseline status means: ADR Accepted, implementing code present, and baseline checks green at commit `4a90b61` on 2026-10-14 (2262 tests passed; ruff check and format clean; mypy strict clean). Baseline phases carry no GSD plans because they were built before GSD adoption. Granularity is the standard default (no `config.json` existed); five of the nine phases are retrospective, so planned work is four phases. Phases 6-9 replace the former Phases 6-7 by operator decision (2026-10-14): former ROLL-01 and ROLL-02 left the milestone, former ROLL-03 and ROLL-04 are absorbed by DEMO-02 and DEMO-04.

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
- [x] **Phase 6: Pluggable LLM gateway** - Every LLM-backed stage calls one `LlmGateway` port; a Codex adapter keeps current behavior and a local llama.cpp adapter runs with no network egress, chosen by configuration (completed 2026-10-15)
- [x] **Phase 7: Synthetic demo lecture and measured results** - A lecture authored for the repository runs end to end to a served page, and the pipeline's quality is reported with real numbers (completed 2026-10-16)
- [x] **Phase 8: Portfolio README and documentation** - A technical reader understands the problem, architecture, decisions and results, and the demo walkthrough runs as written (completed 2026-10-16)
- [ ] **Phase 9: Pre-public gate and release** - The full history passes the leak scans, a final whole-repository review closes, and the repository is switched to public

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

### Phase 6: Pluggable LLM gateway

**Goal**: Every LLM-backed stage (5 semantic ledgers, 6 drafts, 7 critic, 13 exam, 14 podcast, 18 personal-study flashcards) calls one `LlmGateway` port; a `CodexCliAdapter` keeps current behavior and a `LlamaCppAdapter` runs a local model with no network egress; the adapter is chosen by configuration
**Depends on**: Phase 5
**Requirements**: GW-01, GW-02, GW-03, GW-04, GW-05, GW-06
**Success Criteria** (what must be TRUE):
  1. Switching the configuration from codex to llama-cpp runs stages 5, 6 and 7 on a lecture with no Codex invocation
  2. An unknown adapter name exits non-zero before any model call
  3. The contract tests pass for both adapters in CI
  4. The new ADR is Accepted, and ADR 0038 and ADR 0045 carry amendment notes pointing to it

**Plans**: 12/12 plans complete in 6 waves

Plans:
- [x] 06-01-PLAN.md — `llm/` port, typed errors, `complete_json`, `CodexCliAdapter`, stub harness, contract suite (codex), layering test (wave 1)
- [x] 06-02-PLAN.md — `LlamaCppAdapter` on `llama-completion`/`llama-tokenize`, no-egress guards, contract suite over both adapters (wave 2)
- [x] 06-03-PLAN.md — Gateway configuration (YAML plus env override, per-stage) and metadata-only run record (wave 2)
- [x] 06-04-PLAN.md — `remote_llm_allowed` egress policy and registry flag; OPEN-2 checkpoint (wave 2)
- [x] 06-05-PLAN.md — Factory, composition wiring, stage 5 semantic pilot migration (wave 3)
- [x] 06-06-PLAN.md — Stages 6 and 7 (drafts, critic with two gateways), critic repair loop through both adapters (wave 4)
- [x] 06-07-PLAN.md — Stages 13 and 18 (mock exam, personal-study flashcards) (wave 4)
- [x] 06-08-PLAN.md — Stage 14 podcast, typed call errors without retry, timeout override (wave 4)
- [x] 06-09-PLAN.md — Orchestrator preflight and env forwarding; criteria 1 and 2 end to end (wave 5)
- [x] 06-10-PLAN.md — Provisioning: gateway binaries and opt-in example model with SHA-256 (wave 1)
- [x] 06-11-PLAN.md — ADR 0046, amendments to ADR 0038/0045, GEN-07, GW-03 and GW-05 clarification; OPEN-1 checkpoint; D-07 manual run record and follow-ups (wave 6)
- [x] 06-12-PLAN.md — Forward notes on ADRs 0020, 0022, 0034, 0035, 0036, 0037 and PROJECT.md decisions, GW-05 and egress lines (wave 6)

### Phase 7: Synthetic demo lecture and measured results

**Goal**: A lecture authored for this repository runs end to end to a served page, and the pipeline's quality is reported with real numbers
**Depends on**: Phase 6
**Requirements**: DEMO-01, DEMO-02, DEMO-03, DEMO-04
**Success Criteria** (what must be TRUE):
  1. A make target rebuilds the demo lecture video from versioned sources (script, slides, Kokoro narration, ffmpeg composition), and those sources contain no third-party content and no patient data
  2. The demo lecture runs through `run-all` and `run-personal-study`, stages 8 and 9 included, and a signed-in session views its page on the locally served site
  3. A results report gives the ASR word error rate against the known script before and after the terminology dictionary, the schema pass rate per LLM stage, the critic rejection rate and the per-stage wall time, for each adapter that was run
  4. With a rejected or stale unit, the exam, podcast, personal-study flashcard and study-guide scripts write nothing for the demo lecture; after the ADR 0045 recovery they produce the exam, podcast, flashcards and study guide

**Plans**: 8/8 plans complete in 5 waves

Plans:
- [x] 07-01-PLAN.md — Shared Kokoro helper, `lecture_study.demo` package, DemoScript model and lint, authored pt-BR lecture script with planted terms (wave 1)
- [x] 07-02-PLAN.md — Typst slides, build driver, `make demo-video`, build accepted by the pipeline's own extraction and OCR (wave 2)
- [x] 07-03-PLAN.md — Measurement logic: WER, ASR before/after the dictionary, run-record stage metrics, critic rejection rate, per-stage wall time, report renderer (wave 2)
- [x] 07-04-PLAN.md — Dry run of stages 8 and 9, the guide and the site on fixtures to a signed-in view; demo site access material and live check (wave 2)
- [x] 07-05-PLAN.md — Blocked-lecture recovery tooling: inductions, filesystem snapshot, scenario driver with harvest guard (wave 2)
- [x] 07-06-PLAN.md — Registration of both ids, local-adapter run to a signed-in page on the served site, harvest of local measurements (wave 3)
- [x] 07-07-PLAN.md — Codex run behind a no-egress preflight and a blocking operator egress checkpoint, harvest or not-run record (wave 4)
- [x] 07-08-PLAN.md — Live ADR 0045 recovery demonstration, committed results report, ADR 0047 (wave 5)

### Phase 8: Portfolio README and documentation

**Goal**: A technical reader or recruiter understands the problem, the architecture, the design decisions and the measured results from the README, the demo walkthrough runs as written, and the operating boundaries the ADRs impose are stated
**Depends on**: Phase 7
**Requirements**: DOC-01, DOC-02, DOC-03
**Success Criteria** (what must be TRUE):
  1. The README is in English, no longer carries the "under active extraction" placeholder, and presents the problem, a Mermaid architecture diagram, the stage map, the design decisions with links to their ADRs, the DEMO-03 results and a quick start
  2. The end-to-end walkthrough of the demo lecture has been executed once, and every command in it exists as written
  3. The documentation states the egress rule and the unchecked provider terms, the uncertified status and notice, the absence of any standing toward certification, the blocked-lecture recovery, page regeneration after an inline script or style change, the single-worker site constraint and the FileVault revisit triggers

**Plans**: 3/3 plans complete in 3 waves

Plans:
- [x] 08-01-PLAN.md — Documentation checker `scripts/check_documentation.py` with tests, `make docs-check` and a real-repository test: commands, options, paths, links, ADR numbers, verbatim ADR quotes, no machine paths (wave 1)
- [x] 08-02-PLAN.md — `docs/operations.md`: the seven DOC-03 operating boundaries quoted or summarised from ADRs 0010, 0024, 0039, 0044, 0045, 0046 and 0047 (wave 2)
- [x] 08-03-PLAN.md — Portfolio `README.md` (problem, Mermaid diagram, stage map, design decisions, results, quick start, end-to-end demo) and the executed walkthrough with evidence (wave 3)

### Phase 9: Pre-public gate and release

**Goal**: The repository is switched to public only after its full history passes the leak scans and a final whole-repository review closes with no blocking finding, with the leak gate still in force afterwards
**Depends on**: Phase 8
**Requirements**: PUBG-01, PUBG-02, PUBG-03
**Success Criteria** (what must be TRUE):
  1. A denylist scan and a gitleaks scan over every ref and the full history report no finding
  2. A whole-repository `gsd-code-review` and `gsd-secure-phase` close with no open blocking finding
  3. After the operator's final check, the repository visibility is public, the CI `DENYLIST` secret is still configured, and the leak-gate hooks are still in force

**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9. Phases 1-5 are complete baseline; the next action is planning Phase 7.

**Phase completion gates:** a phase that changes code or executable operational artifacts runs `/gsd-code-review` and `/gsd-secure-phase` on the phase-final diff before it closes, and re-runs them after any material fix (operator rule R0.4; canonical mechanism set by operator decision 2026-10-14).

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Pipeline spine and media-to-evidence | 0/0 | Complete (baseline) | Pre-GSD |
| 2. Codex generation and critic review | 0/0 | Complete (baseline) | Pre-GSD |
| 3. Assembly, study material and the uncertified boundary | 0/0 | Complete (baseline) | Pre-GSD |
| 4. Study site | 0/0 | Complete (baseline) | Pre-GSD |
| 5. Publication boundary and public repository | 0/0 | Complete (baseline) | Pre-GSD |
| 6. Pluggable LLM gateway | 12/12 | Complete    | 2026-10-15 |
| 7. Synthetic demo lecture and measured results | 8/8 | Complete    | 2026-10-16 |
| 8. Portfolio README and documentation | 3/3 | Complete    | 2026-10-16 |
| 9. Pre-public gate and release | 0/TBD | In progress — gate closed, public switch pending operator | 2026-10-16 |
