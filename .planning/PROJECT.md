# lecture-to-study-pipeline

## What This Is

A macOS (Apple Silicon) command-line pipeline plus a small authenticated web site. The pipeline turns an operator-registered lecture video into frames, OCR text, a corrected transcript, evidence packs, critic-reviewed grounded drafts, and student-facing study material (study guide, flashcards, mock exam, narrated podcast). The site serves that material to an allowlisted audience. The product owner is one developer-operator and the implementer is Claude.

The code base is already implemented. This planning setup adopts it as a brownfield baseline (roadmap Phases 1-5, built) and tracks the remaining open work (Phases 6-7). Requirements were derived from 42 Accepted ADRs, because no PRD, SPEC or DOC exists; provenance is cited per requirement.

## Core Value

No text the critic rejected or never reviewed reaches a reader of the study material, and everything a reader sees is labelled as uncertified.

## Requirements

Full text, provenance and traceability: `.planning/REQUIREMENTS.md`.

### Validated

Built, with an Accepted ADR and implementing code present; baseline checks green at commit `4bfbe46` on 2026-10-14 (2262 tests passed, ruff check and format clean, mypy strict clean on 112 source files). Per-requirement re-verification is not claimed.

- Pipeline spine and media-to-evidence (SPINE-01..06, INGEST-01..07) - Phase 1
- Codex-driven grounded generation and critic review (GEN-01..07) - Phase 2
- Assembly, deterministic deliverables, student-facing material, critic gate and the uncertified boundary (ASM-01..04, MAT-01..07, GATE-01..02, BND-01..04) - Phase 3
- Study site (WEB-01..10) - Phase 4
- Publication boundary and public repository (PUB-01..04, REPO-01..04) - Phase 5

### Active

- [ ] ROLL-01: No exam, podcast, flashcard deck or study guide generated before the critic gate remains reachable by the site (regenerated through the gate or deleted)
- [ ] ROLL-02: Site regenerated so every deployed page carries the current uncertified notice and matches the CSP hash list
- [ ] ROLL-03: Stages 8 and 9 executed on a real lecture through `run-personal-study`
- [ ] ROLL-04: The documented recovery from a blocked lecture verified end to end
- [ ] DOC-01: README replaces the "under active extraction" placeholder
- [ ] DOC-02: End-to-end walkthrough from a registered video to a served page, executed once
- [ ] DOC-03: Operating boundaries documented as the ADRs define them

### Out of Scope

- Local-model route for content that may carry patient-identifying data (the "planned local adapter of the LLM gateway") - operator-approved reading of ADR 0045 section 2; no record defines the gateway and ADR 0038 removed the local route; until a record defines it, such content is not processed past stage 4
- Local text-LLM execution (qwen3-32b, llama.cpp text generation) - removed by ADR 0038
- ADR 0023 sections 15 and 16, the "Production semantic runtime and promotion boundary" subsection, the "Implementation status" list and the GGUF-bound parts of section 6 - operator-approved reading; not planning targets
- Certified publication path (certificate issuance, derivative certification, reference corpus, calibration authority) - stays unreachable (ADR 0024, ADR 0045 section 4); a future certified route needs a new record on generator-evaluator independence
- Any claim of clinical, academic or publication authority, or relabeling the material as certified - ADR 0024 clause stays in force
- Technical detection of patient-identifying data before a Codex call - ADR 0045 section 2 makes the rule procedural; accepted
- Serving only certified material, and skipping rejected units to publish a partial lecture - both rejected in ADR 0045
- Portal discovery and acquisition, portal credentials, deploy and hosting tooling - excluded from the public repository (ADR 0044 section 1)
- Application-level encryption of `workspace/data/` - delegated to FileVault (ADR 0010); revisit for a shared workstation, cloud sync or a second operator account
- Captions or a transcript track on the watch page - ADR 0040
- Multi-worker site deployment with shared throttle and session state - single process by design (ADR 0039); revisit before adding workers or replicas
- Revoking one device's session without rotating the password - ADR 0039 addendum records it as impossible

Deferred candidates (conditional, ADR 0023 target remainder, codebase-map hardening) are tracked as v2 in `.planning/REQUIREMENTS.md`.

## Context

**Terminology.** ADRs, code and the CLI use "Phase N" for pipeline stages (for example "Phase 7" is critic review). This planning set uses "stage N" for those and reserves "Phase N" for roadmap phases. Requirement IDs use the prefix `WEB` for the site (not `SITE`) to avoid colliding with the external `SITE-0x` identifiers cited in ADR 0029 and in test docstrings.

**Pipeline stage map** (artifacts under gitignored `workspace/data/`):

| Stage | Name | Output | Route |
|-------|------|--------|-------|
| 2 | extraction | `04_canonical` | local (FFmpeg, Apple Vision) |
| 3 | transcription | `05_transcribed` | local (whisper.cpp) |
| 4 | evidence packs | `06_evidence_packs` | local |
| 5 | semantic ledgers | `07_semantic_ledgers` | Codex child process |
| 6 | drafting | `08_drafts` | Codex child process |
| 7 | critic and repair | `09_validated_units` | Codex child process |
| 8 | assembly | `10_canonical_documents` | in-process |
| 9 | metadata | `11_metadata` | in-process |
| 10 | quality certification | `quality_certificates` | fail-closed, unreachable |
| 11 | publishing | `12_published` | in-process |
| 12 | Anki flashcards | `13_flashcards` | in-process |
| 13 | mock exam | `14_mock_exams` | Codex child process |
| 14 | podcast script | `15_podcast_scripts` | Codex child process |
| 15 | podcast audio | `16_podcast_audio` | local (Kokoro via mlx-audio) |
| 17 | figure captions | `17_figure_captions` | local (llama-mtmd-cli) |
| 18 | personal-study flashcards | `18_personal_study_flashcards` | standalone Codex script |

**Entry points.** `python -m lecture_study.cli.main` (`run-all`, `run-phase`, `run-personal-study`); `lecture_study.site.app:app` (`make site`); operator scripts under `scripts/`. `run-all` stops at stage 10 on the certified path; `run-personal-study` (stages 8, 9, 13, 14, 15) is the operable path.

**Source of this setup.** 42 ADRs (0001-0016, 0020-0045; 0017-0019 absent by design, ADR 0044), all Accepted. Intel: `.planning/intel/`. Conflict report: `.planning/INGEST-CONFLICTS.md` (0 blockers, 3 warnings resolved by the operator-approved readings below, 19 informational). Codebase map: `.planning/codebase/` (mapped at commit `98206c1`).

**Open items stated in the ADRs.** Pre-ADR 0045 derivative material must be regenerated or deleted and deployed pages keep the old notice until regeneration (ADR 0045, Phase 6). Assembly has not run on real data (ADR 0024, ADR 0045; Phase 6). Deferred or conditional: podcast prompt size check (ADR 0037), re-transcription of 25 transcripts (ADR 0028), re-extraction at 37.5 seconds (ADR 0025), critic rerun after re-draft and the critic/drafts script coupling (ADR 0045, ADR 0036). The README still carries a placeholder awaiting the end-to-end demo (Phase 7).

**Codebase concerns** (`.planning/codebase/CONCERNS.md`): oversized modules, broad exception handlers, duplicated Docker pins, signing secret read per request, one catalog TODO, two test gaps. Tracked as v2 hardening candidates. The map's "stray print statements" concern is not carried forward: the two `print` calls in `extraction/ocr_worker.py` are the OCR child process's stdout and stderr protocol.

## Constraints

- **Platform**: macOS on Apple Silicon, CPython 3.12, uv, hatchling - OCR (Apple Vision via pyobjc), MLX TTS and Metal-backed engines are macOS-only; CI runs on `macos-15`
- **Local compute**: only Whisper/ASR, the Kokoro TTS voice, Apple Vision OCR and `llama-mtmd-cli` vision captioning run locally; all LLM stages use the Codex CLI (ChatGPT subscription, not the API) as bounded child processes (ADR 0038)
- **Egress**: the Codex route is used only for lecture content the operator has verified to carry no patient-identifying data; the rule is procedural and the provider's retention and training terms are unchecked here (ADR 0045 section 2)
- **Certification boundary**: stage 10 keeps raising unconditionally; every artifact is uncertified and carries the payload marker; no standing toward certification (ADR 0024)
- **Site image isolation**: `Dockerfile.site` copies only `site/` and `domain/models.py`; the site package may import only `lecture_study.site` and `lecture_study.domain`
- **Inline site code**: the CSP lists inline scripts and styles by hash; any change to them requires regenerating site pages (ADR 0044)
- **Data and secrets**: no secrets, credentials, PII or key-shaped literals in any file outside `.env`; all lecture identifiers synthetic; `workspace/`, `engines/` and `config/*.local.yaml` stay git-ignored
- **Code quality**: ruff clean, `ruff format` clean, mypy strict, pytest green (`make lint`, `make test`); tests use `tmp_path` and never write to `workspace/` or `engines/`
- **Leak gate**: local hooks plus CI secret scan and denylist scan; the denylist lives outside the repository
- **Process (operator rule R0.4)**: a phase that changes code or executable operational artifacts runs Code Review and Code Secure on the phase-final diff before closing. The repository defines no phase-level mechanism of its own; the GSD workflows `gsd-code-review` and `gsd-secure-phase` are available and need confirmation as canonical before the first code-changing phase closes
- **Milestones**: one active milestone per owning boundary; this setup opens v1.0 for the repository

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Model the built baseline as Phases 1-5 marked Complete and the open work as Phases 6-7 | Roadmap must reflect built versus open work; no PRD exists, so phases follow ADR delivery boundaries | Pending |
| Accept ADR 0045 section 2 egress rule as written; plan no local-LLM phase | Operator-approved reading (2026-10-14) | Good |
| Treat ADR 0023 sections 15, 16, the runtime subsection, the implementation-status list and GGUF-bound section 6 as not planning targets | Operator-approved reading; superseded by ADR 0038 and ADR 0045 section 4 | Good |
| Uncertified personal-study mode usable by the allowlisted audience; "no standing toward certification" stays in force | Operator-approved reading; ADR 0045 section 3 widens the audience | Good |
| Keep ADR 0023 target remainder and codebase-map findings as v2 candidates, not committed phases | Intel supports them only as targets or observations; promotion needs an operator decision | Pending |
| Prefix site requirements `WEB`, not `SITE` | Avoid collision with external `SITE-0x` IDs cited in ADR 0029 and tests | Good |

<decisions>
## Locked Decisions (ADR-sourced)

All 42 records have Status "Accepted" and are locked. Precedence follows only the explicit amends and supersedes statements in each Status section (ADR 0045 section 6). Paths written `data/...` in ADRs 0002-0006 are rooted at `workspace/data/` (ADR 0007 section 1). Decision IDs use the form `D-ADR<number>-<n>`; the IDs D-10, D-14 and D-23 cited inside ADRs 0025 and 0030 belong to external documents and are not reused.

### Operator-approved readings (ingest, 2026-10-14)
- **D-READ-01:** ADR 0045 section 2 egress rule is accepted as written. The Codex route is used only for content the operator has verified to carry no patient-identifying data; content that may carry it is not processed past stage 4. The rule is procedural. No local-LLM or "LLM gateway" adapter phase is planned from it.
- **D-READ-02:** ADR 0023 sections 15 and 16, the unnumbered "Production semantic runtime and promotion boundary" subsection, the "Implementation status" list and the GGUF-bound parts of section 6 are not planning targets.
- **D-READ-03:** The uncertified personal-study mode is usable by the allowlisted audience. The "no standing toward certification" clause of ADR 0024 stays in force.

### Pipeline contracts, stages 2-9
- **D-ADR0001-01:** Slide PNGs are decoded with CGImageSourceCreateWithURL and CGImageSourceCreateImageAtIndex into a CGImageRef passed to VNImageRequestHandler(cgImage=...). pyobjc-framework-CoreImage is not a dependency. The local-only mandate no longer binds the LLM phases (amended by ADR 0045).
- **D-ADR0002-01:** The raw video is found by globbing `01_raw/{lecture_id}.*` with exactly one match required (RawVideoNotFoundError otherwise). A per-lecture exception is recorded on the outcome and does not abort the batch. LectureExtractionResult is persisted tmp-then-replace to `04_canonical/{lecture_id}.json`. Identifiers use SafeIdentifier (`^[A-Za-z0-9_-]+$`). OCR runs under its own semaphore (default 4).
- **D-ADR0003-01:** Stage 3 reads `04_canonical/{lecture_id}.json` and writes `05_transcribed/{lecture_id}_transcription.json`, single-flight (concurrency 1). A segment with end before start is rejected (zero length allowed); the `-oj` sidecar is deleted on every exit path; malformed whisper JSON raises WhisperTranscriptionError. Output contract extended by ADR 0028.
- **D-ADR0004-01:** Evidence packs are written to `06_evidence_packs/{lecture_id}/pack_NNN.json` through a `.staging` directory and a `.previous` rename swap; `05_evidence_packs/` must not be resurrected. Each frame opens a window to the next frame; cross-boundary segments go whole to the window with majority overlap (ties to the earlier). UnboundedLectureEndTimeError and DegenerateWindowError are named failures.
- **D-ADR0005-01:** SemanticFact and SemanticLedger are the stage 5 contract; echoed identifiers are verified against the source pack; ledgers are written atomically as `ledger_NNN.json`. The contracts survive in `ops/llm_instructions.py` and `scripts/generate_codex_semantic_ledgers.py`; the local execution route is superseded by ADR 0038.
- **D-ADR0006-01:** LearningUnitDraft is grounded on both the evidence pack and the ledger and forbids clinical data absent from the ledger. The residual hallucination risk is accepted, with the critic (ADR 0007) as the automated control. The local execution route is superseded by ADR 0038.
- **D-ADR0007-01:** Execution data lives under gitignored `workspace/data/`. CriticReview and ValidatedLearningUnit are the stage 7 contracts; the last (draft, review) pair is always persisted to `09_validated_units/{lecture_id}/unit_NNN.json`. The repair loop lives on in `scripts/generate_codex_critic_review.py` (ADR 0036).
- **D-ADR0008-01:** CanonicalLectureDocument is assembled deterministically; validation order is SafeIdentifier, LectureNotFoundError, MissingSequenceError (exactly 1..n), UnresolvedReviewError. A partially assembled document is rejected. The three errors live in `domain/errors.py`.
- **D-ADR0009-01:** Metadata deduplication is deterministic (no LLM): normalized text, first-seen wins in pack order, keyword category routing. Deep semantic paraphrases are not merged (accepted risk). Superseded in part by ADR 0012 (glossary shape) and ADR 0011 (persistence).

### Storage, publishing and derivatives
- **D-ADR0010-01:** At-rest confidentiality of `workspace/data/` is delegated to macOS FileVault; no code encrypts it. Revisit for a shared workstation, cloud sync or a second operator account. Scoped to local storage by ADR 0044; content sent to remote services is governed by ADR 0045 section 2.
- **D-ADR0011-01:** MetadataAggregator persists `11_metadata/{lecture_id}/global_index.json`; publishing writes `study_guide.md` and `study_guide.html` through MarkdownIt("commonmark", {"html": False}); LLM-extracted free text is escaped. PDF export is out of scope for that stage.
- **D-ADR0012-01:** Anki cards are generated without an LLM into `13_flashcards/{lecture_id}/anki_deck.csv`; glossary terms are SemanticFact; fields starting with `=`, `+`, `-`, `@` or tab are neutralized (CWE-1236).
- **D-ADR0013-01:** MockQuestion (options exactly A-D, answer within options) and MockExam survive with the deterministic ExamCompiler. The local generator is deleted (ADR 0038); exams come from `scripts/generate_codex_mock_exam.py`.
- **D-ADR0014-01:** PodcastLine, PodcastScript and PodcastScriptCompiler survive with unit-NNN traceability. The local generator is deleted (ADR 0038); scripts come from `scripts/generate_codex_podcast.py` (ADR 0037).
- **D-ADR0015-01:** TTS uses argument lists with shell=False (never shell=True), cleans intermediates in a `finally` block, and uses ScriptNotFoundError and AudioGenerationError. The voice mapping is revised by ADR 0032; the local-only mandate is amended by ADR 0045.
- **D-ADR0032-01:** Kokoro-82M (mlx-community/Kokoro-82M-bf16) through the mlx-audio CLI replaces `say`: Host `pf_dora`, Expert `pm_alex`, `--join_audio` mandatory, finite timeout, bounded output, sanitized environment. ADR 0015 decisions 1-3 still govern.
- **D-ADR0027-01:** Student-facing generators demand Brazilian Portuguese, forbid referring to the source material and label input "Lecture material". `quality/student_facing_text.py` reports three defect classes inside the bounded repair retry; a second defective attempt fails and no defective artifact is written.
- **D-ADR0030-01:** One merged critical review document replaces the critical summary and the SUMMARY edition; exam question count scales with grounded ledger facts (per-unit cap default 5, total floor default 20); `scripts/generate_codex_mock_exam.py` calls `codex exec` per unit in a fresh empty scratch directory. Input is critic-approved units (amended by ADR 0045); dual-route reachability superseded by ADR 0038.
- **D-ADR0031-01:** Figure cropping in personal study guides is an explicit `WIDTH:HEIGHT:X:Y` rectangle; the default is now no crop (amended by ADR 0044).
- **D-ADR0033-01:** Exam questions reuse the figure already selected for their unit; no new selection heuristic and no schema change; a question without a figure renders as before.

### Execution route, orchestrator and the uncertified boundary
- **D-ADR0016-01:** `cli/orchestrator.py` is the composition root; PHASE_REGISTRY drives `run-all` (stops at the first failing phase, skips phases whose artifact exists) and `run-phase`; main() catches only RuntimeError and ValueError. The `discover` command is removed (ADR 0044); `--llm-model-family` is removed (ADR 0038). No cross-process lock on Metal-bound inference (accepted).
- **D-ADR0020-01:** Historical record (ADR 0038). The bounded-subprocess discipline survives in `ops/process_lifecycle.py`, used by every surviving subprocess caller.
- **D-ADR0022-01:** Historical record (ADR 0038). Operational amendment still in force: retry prompts carry trusted static guidance from the shared response contract, never the raw exception or model bytes; the Codex scripts parse JSON under `ops/llm_response_contract.py`.
- **D-ADR0034-01:** Historical record (ADR 0038); the retry repeat-penalty workers are deleted with the local route.
- **D-ADR0035-01:** Stages 5 and 7 run through the Codex CLI: one `codex exec` per pack or draft, `--sandbox read-only`, scratch cwd, shared response contract, one retry. Dual-route reachability superseded by ADR 0038.
- **D-ADR0036-01:** The Codex critic redrafts on rejection and re-reviews up to `--max-retries` (default 2), persists the last result even if still failing, anchors identity to the trusted pack, and separates wording from substance in its instruction. The critic imports a private function of the drafts script (recorded coupling).
- **D-ADR0037-01:** Podcast scripts come from one whole-lecture `codex exec` call with units tagged `unit-NNN`, written through PodcastScriptCompiler, with the ADR 0027 guard in the retry-once path. Decision 2 (grounding on `08_drafts`) is superseded by ADR 0045. No prompt size check exists yet (open).
- **D-ADR0038-01:** `_run_phase_5/6/7/13/14` invoke the Codex scripts as child processes through `run_bounded_process` (12-hour bound, 16 MiB output cap, explicit environment allowlist); the script exit code is the phase contract; failures persist output under `tmp/codex_phase_failures/`. The local LLM route is deleted entirely; Whisper/ASR, the Kokoro voice and llama-mtmd-cli vision captioning stay local; `--llm-model-path` and `--llm-model-family` are removed.
- **D-ADR0038-02:** `OrchestratorContext.execution_mode` is removed; the certified versus uncertified distinction lives in the entry point: `run-personal-study` selects stages 8, 9, 13, 14, 15 and never calls `require_publication_ready`. Certified-route exam and podcast quality gates retire with the local route.
- **D-ADR0021-01:** Frame selection uses `select='isnan(prev_selected_t)+gt(scene,0.3)+gte(t-prev_selected_t,150)',showinfo`; the 0.3 threshold replaced 0.03 (an on-screen clock). The safety interval is not a visual-recall guarantee. The interval value is amended by ADR 0025.
- **D-ADR0025-01:** `_MAXIMUM_VISUAL_FRAME_GAP_SECONDS` is 37.5 seconds, bound by a test; the scene threshold, frame naming and showinfo parsing are unchanged. Lectures extracted earlier keep 150-second density until re-extracted.
- **D-ADR0023-01:** Target architecture, implementation incremental. Completeness is a blocking safety property (unknown completeness is failure); canonical content keeps verbatim evidence and an atomic ledger; Learning Units are conceptual (five to ten minutes); long-form generation uses evidence-aware hierarchical refinement; evaluation is bidirectional and plural, and the same model may not be sole generator and sole approver; publication requires authenticated certificates; execution is a resource-aware DAG (LLM generation 1, Whisper 1); a lecture-centric layout migrates only after a tested migration. Section 6 still defines the certified path, which stays unreachable. Sections 15 and 16 and the blocks named in D-READ-02 are not planning targets.
- **D-ADR0024-01:** Uncertified personal-study mode is explicit, opt-in and separately named; stage 10 keeps raising QualityCertificationError unconditionally; `require_publication_ready` keeps gating the certified path; the mode never calls QualityCertificateVerifier.verify and never forges a certificate; every artifact carries UNCERTIFIED_PERSONAL_STUDY in its own payload. The mode grants no clinical, academic or publication authority and confers no standing toward certification. Input is critic-approved units and the audience is the allowlisted site audience (amended by ADR 0045).
- **D-ADR0026-01:** HmacQualityTrust.from_environment() reads LECTURE_STUDY_QUALITY_HMAC_KEY_B64 from the process environment, else only from the working directory's own `.env` through dotenv_values (never parent directories, never writing os.environ), else fails closed with QualityTrustError. Child processes keep stripping LECTURE_STUDY_QUALITY_* variables.
- **D-ADR0028-01:** Whisper receives DOMAIN_VOCABULARY_PROMPT with `--carry-initial-prompt`; `transcription/terminology.py` rewrites known defects deterministically and records every rewrite in `05_transcribed/{id}_terminology_corrections.json`, written before the transcript. Existing transcripts are not rewritten; re-transcription is deferred.

### Study site, publication boundary and public repository
- **D-ADR0029-01:** Lecture videos are published with the lecture: `workspace/site/` and `workspace/site-videos/` are separate trees behind the same authentication. Unprocessed lectures are listed as pending. Access is an email allowlist with a per-person password from a credentials file mounted from outside the image. Every page rendering study material shows the uncertified notice (amended by ADR 0045).
- **D-ADR0039-01:** Every authorized response sends `Cache-Control: private, no-store`; session tokens are signed with a key mixing a dedicated server secret with the person's stored password hash and validated against the allowlist as read now; the allowlist directory (mode 700, files 600) and the signing secret live in separate directories; password checks are throttled per attempted address with growing waits, never a lockout, a capped queue and 429 load shedding; Basic Auth stays for scripts, health check and deploy. Single-device revocation is impossible without rotating the password.
- **D-ADR0040-01:** `assistir.html` is generated per lecture next to `index.html`; the embedded player stays and gains an "Assistir em tela cheia" link; the page reuses the existing `/videos` route with the same authentication and cache headers; no captions ship; a lecture without video gets no page and no link.
- **D-ADR0041-01:** Mock-exam grading is entirely server-side: `site/quiz.py` public_view strips `correct_answer` and `explanation`; `GET /simulado/{lecture_id}` renders public questions only; `POST` grades by position against a fresh read of `exam.json` from an environment-configured path, never copied into the served tree; submissions are throttled per email and lecture; the lecture page links "Responder simulado" only when `exam.json` exists at generation time.
- **D-ADR0042-01:** Local lectures use synthetic ids 925100-925199 and 925200-925299; `publish: false` by default; unpublished lectures compile into `--private-output-root` (default `workspace/study-guides-local`), never synced by deploy, and a private root nested in the public root is refused; registration is transactional and idempotent with a file lock; `scripts/list_withheld_lecture_ids.py` excludes unpublished reserved ids from the study-guide and mock-exam sync.
- **D-ADR0043-01:** `kind: video-only` lectures compile without reading evidence packs, ledgers, drafts or queue titles into a bundle of exactly `indice.md`, `.bundle.json` and a hard-linked `video/aula.mp4`; registration creates no `queue.json` row; no exam; the publication gate is unchanged. The critic gate of ADR 0045 does not apply to them.
- **D-ADR0044-01:** The public repository excludes portal discovery and acquisition, portal credentials and deploy tooling; lectures enter through `scripts/register_local_lecture.py`; identifiers are synthetic. Leak gate: four versioned hooks plus a private denylist outside the repository, gitleaks, and CI jobs `secret-scan`, `denylist-scan` (fails closed without the `DENYLIST` secret) and `test`, with actions pinned by SHA.
- **D-ADR0044-02:** Site hardening: access decided on the decoded served path (400 on `?`, `#`, backslash, NUL, dot segments); method-and-path-exact unauthenticated exemptions; SecurityHeadersMiddleware outermost; hash-based CSP without `unsafe-inline`; `__Host-pos_session` cookie; signed double-submit login CSRF; pseudonymized authentication logging; non-root container uid/gid 10001. Chapter plans come from an optional `--chapter-plan` YAML (default numbered "Parte N").

### Critic gate, egress and audience
- **D-ADR0045-01:** Exam, podcast, personal-study flashcard and study-guide scripts load units only through `load_critic_approved_drafts`, which reads post-repair content from `09_validated_units` and blocks the whole lecture on any of seven listed conditions; a blocked lecture is never published partially; identity comes from the unit. Material generated before this decision must be regenerated or deleted. Supersedes ADR 0037 decision 2.
- **D-ADR0045-02:** The Codex route is used only for lecture content the operator has verified to carry no patient-identifying data; content that may carry it is processed only by a local route and, until that exists, not past stage 4. Operator responsibility, not enforced by code; provider retention and training terms must be checked against the account (see D-READ-01).
- **D-ADR0045-03:** The audience bound of ADR 0024 widens to the allowlisted site audience; the material stays uncertified; `UNCERTIFIED_NOTICE` and `uncertified_notice_html` (`site/renderer.py`) appear at the top of the home, lecture, reading and watch pages and both mock-exam pages, and not on the login page.
- **D-ADR0045-04:** ADR 0023 sections 15 and 16 are superseded by ADR 0038 and are not a planning target; section 6 still defines the certified path, which stays unreachable; a future certified route needs a new record on generator-evaluator independence.
- **D-ADR0045-05:** Stage 18 is `scripts/generate_codex_personal_study_flashcards.py`, a standalone operator-run script outside the orchestrator, writing to `workspace/data/18_personal_study_flashcards` and independent of the stage 12 Anki deck; it is subject to the critic gate.
- **D-ADR0045-06:** Citations between records are ordered only by explicit amends and supersedes statements in each Status section; those form an acyclic order; a citation in both directions is not a dependency cycle.
</decisions>

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition:**
1. Requirements invalidated? Move to Out of Scope with the reason
2. Requirements validated? Move to Validated with the phase reference
3. New requirements emerged? Add to Active
4. Decisions to log? Add to Key Decisions
5. "What This Is" still accurate? Update if drifted

**After each milestone:**
1. Full review of all sections
2. Core Value check - still the right priority?
3. Audit Out of Scope - reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-10-14 after ADR ingest and roadmap creation*
