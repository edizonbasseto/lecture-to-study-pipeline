# Requirements: lecture-to-study-pipeline

**Defined:** 2026-10-14
**Core Value:** No text the critic rejected or never reviewed reaches a reader of the study material, and everything a reader sees is labelled as uncertified.

## Reading Guide

**Provenance.** No PRD or SPEC exists: `intel/requirements.md` holds 0 requirements. Every ID below was assigned by the roadmapper from ADR-stated behaviors and cites its source in parentheses (ADR number and section). Items that come from the repository README or the codebase map instead of an ADR say so. The Phase 6-9 requirements come from the operator-approved restructure of milestone v1.0 and cite "Operator decision 2026-10-14", plus any ADR they act on.

**Status.** `[x]` means built: the ADR is Accepted, the implementing code is present, and the baseline checks were green at commit `4bfbe46` on 2026-10-14 (2262 tests passed; `ruff check` and `ruff format --check` clean; `mypy src` strict clean on 112 files). It does not claim per-requirement re-verification. `[ ]` means open work, planned in Phases 6-9.

**Terminology.** "Stage N" is the pipeline stage numbered "Phase N" in ADRs and code. "Phase N" in this file means a roadmap phase. The prefix `WEB` is used for the site instead of `SITE` to avoid colliding with the external `SITE-0x` identifiers cited in ADR 0029 and in test docstrings.

## v1 Requirements

Committed scope. Each requirement maps to exactly one roadmap phase.

### Pipeline Spine (Phase 1)

- [x] **SPINE-01**: Operator can drive one lecture through the pipeline with `python -m lecture_study.cli.main` using `run-all`, `run-phase` and `run-personal-study`; a registry of phase specs (stages 2-15) selects the phases, and `run-all` stops at the first failing phase and skips every phase whose artifact already exists. (ADR 0016; ADR 0038 section 6; ADR 0044 section 1)
- [x] **SPINE-02**: Every lecture identifier is validated as a SafeIdentifier (`^[A-Za-z0-9_-]+$`) at the top of each entry point and in every handoff model, so an identifier cannot traverse a path. (ADR 0002 section 4; ADR 0016)
- [x] **SPINE-03**: Every subprocess starts from an argument list (never `shell=True`) through bounded-process helpers with a finite time limit, an output cap and an explicit environment allowlist, and quality-trust variables are stripped from child environments. (ADR 0015; ADR 0020; ADR 0026; ADR 0038 section 1)
- [x] **SPINE-04**: Orchestration failures surface as PipelineExecutionError, and the CLI boundary catches only RuntimeError and ValueError. (ADR 0016)
- [x] **SPINE-05**: Stage artifacts are written atomically (temporary file then replace; directory sets through a `.staging` directory and a `.previous` rename swap). (ADR 0002 section 3; ADR 0004; ADR 0005; ADR 0006)
- [x] **SPINE-06**: A failure in one lecture or one pack is recorded on its outcome and does not abort the batch. (ADR 0002 section 2; ADR 0003; ADR 0004; ADR 0005)

### Media to Evidence (Phase 1)

- [x] **INGEST-01**: Operator can register an operator-owned video as a lecture through `scripts/register_local_lecture.py`, which writes the metadata row and the `queue.json` row; the raw video is found by globbing `workspace/data/01_raw/{lecture_id}.*` and exactly one match is required. (ADR 0002 section 1; ADR 0044 section 1)
- [x] **INGEST-02**: Extraction (stage 2) writes the lecture audio and scene-change slide frames (FFmpeg filter `gt(scene,0.3)` with a 37.5-second safety interval) and persists the result to `04_canonical/{lecture_id}.json`. (ADR 0002 section 3; ADR 0021; ADR 0025)
- [x] **INGEST-03**: Slide text is recognized with Apple Vision, fed through pyobjc-framework-Quartz (CGImageSource to CGImageRef to VNImageRequestHandler), with OCR concurrency bounded (default 4). (ADR 0001; ADR 0002 section 4)
- [x] **INGEST-04**: Transcription (stage 3) runs whisper.cpp single-flight, writes `05_transcribed/{lecture_id}_transcription.json`, rejects segments that end before they start, removes its temporary JSON sidecar on every exit path, and fails with a named error on malformed output. (ADR 0003)
- [x] **INGEST-05**: ASR uses a domain vocabulary prompt and a deterministic terminology correction, and every rewrite is recorded in `05_transcribed/{lecture_id}_terminology_corrections.json`, written before the transcript. (ADR 0028)
- [x] **INGEST-06**: Evidence packs (stage 4) are built frame-driven with majority-overlap segment allocation, written to `06_evidence_packs/{lecture_id}/pack_NNN.json` through a staging swap, and fail with named errors for an unbounded lecture end or a degenerate window. (ADR 0004)
- [x] **INGEST-07**: Vision captioning of slide figures stays a local `llama-mtmd-cli` step that writes `17_figure_captions`. (ADR 0038 section 9; codebase `captioning/`)

### Codex Generation and Critic (Phase 2)

- [x] **GEN-01**: Semantic ledger extraction (stage 5) runs per pack through `scripts/generate_codex_semantic_ledgers.py`; echoed `lecture_id` and `pack_sequence` are verified against the source pack and `ledger_NNN.json` is written under `07_semantic_ledgers/`. (ADR 0005; ADR 0035)
- [x] **GEN-02**: Drafting (stage 6) runs per pack through `scripts/generate_codex_drafts.py`; the prompt embeds both evidence pack and ledger and forbids clinical data absent from the ledger; `draft_NNN.json` is written under `08_drafts/{lecture_id}/`. (ADR 0006)
- [x] **GEN-03**: Critic review (stage 7) evaluates each draft; on rejection it redrafts with the critic's feedback and re-reviews up to `--max-retries` (default 2); the last (draft, review) pair is always persisted to `09_validated_units/{lecture_id}/unit_NNN.json` with identity anchored to the trusted pack. (ADR 0007; ADR 0036)
- [x] **GEN-04**: Every Codex script uses a self-contained prompt with the schema as prompt text, runs `codex exec` in a fresh empty scratch directory (never the repository checkout) with a read-only sandbox on the ChatGPT subscription, parses output through `ops.llm_response_contract`, and retries once with trusted static feedback instead of the raw exception or model bytes. (ADR 0022 operational amendment; ADR 0030; ADR 0035)
- [x] **GEN-05**: The orchestrator runs the Codex scripts for stages 5, 6, 7, 13 and 14 as bounded child processes (12-hour bound, 16 MiB output cap, explicit environment allowlist), treats the script exit code as the phase contract, and relies on resume predicates for completeness between runs. (ADR 0038 sections 1-3)
- [x] **GEN-06**: A failed child raises PipelineExecutionError naming the stage, script, exit code and output digests, with captured output persisted under `tmp/codex_phase_failures/` and never inlined. (ADR 0038 section 1 and Consequences)
- [x] **GEN-07**: The local text-LLM route (qwen3-32b, llama.cpp) is removed together with `--llm-model-path` and `--llm-model-family`; local model execution remains only for Whisper, the Kokoro voice and `llama-mtmd-cli` vision captioning. (ADR 0038 sections 7-9)

### Assembly and Deterministic Deliverables (Phase 3)

- [x] **ASM-01**: Document assembly (stage 8) builds the canonical lecture document deterministically from `09_validated_units` and rejects a partial one (unknown lecture, a `pack_sequence` that is not exactly 1..n with duplicate and gap distinguished, or any unit with `review.passed` false); output is `10_canonical_documents/{lecture_id}/canonical_document.json`. (ADR 0008)
- [x] **ASM-02**: Metadata aggregation (stage 9) deterministically deduplicates glossary terms, dosages and red flags without an LLM and persists `11_metadata/{lecture_id}/global_index.json`. (ADR 0009; ADR 0011; ADR 0012)
- [x] **ASM-03**: Publishing (stage 11) writes `study_guide.md` and `study_guide.html` to `12_published/{lecture_id}/` with raw HTML disabled and LLM-extracted free text escaped. (ADR 0011)
- [x] **ASM-04**: The Anki deck (stage 12) is generated deterministically to `13_flashcards/{lecture_id}/anki_deck.csv`, with newline conversion and a formula-injection guard (CWE-1236). (ADR 0012)

### Student-Facing Study Material (Phase 3)

- [x] **MAT-01**: The mock exam (stage 13) is generated per unit by `scripts/generate_codex_mock_exam.py`, with the question count scaled by grounded ledger facts per unit (cap `--questions-per-unit-cap`, default 5; total floor `--minimum-total-questions`, default 20); `exam.json` is validated (options A-D, answer within options) and the orchestrator compiles `simulado.md`. (ADR 0013; ADR 0030; ADR 0038 section 4)
- [x] **MAT-02**: An exam question illustrates with the figure already selected for its unit, and a question without a figure renders unchanged. (ADR 0033)
- [x] **MAT-03**: The podcast script (stage 14) is generated by one whole-lecture Codex call with each unit tagged `unit-NNN`, rejects any line whose `source_unit_id` is absent from the lecture, and is compiled to `script.json` and `script_legivel.md`. (ADR 0014; ADR 0037)
- [x] **MAT-04**: Podcast audio (stage 15) is synthesized locally with Kokoro-82M through mlx-audio (Host `pf_dora`, Expert `pm_alex`, joined output), concatenated with ffmpeg to `podcast_final.m4a`, with intermediates removed on every exit path. (ADR 0015; ADR 0032)
- [x] **MAT-05**: Exam, personal-study flashcard and podcast text pass a deterministic student-facing guard (Brazilian Portuguese, no reference to source material, no internal pipeline vocabulary) inside the bounded retry; a second defective attempt fails the run and writes nothing. (ADR 0027)
- [x] **MAT-06**: Personal-study flashcards (stage 18) are generated by the standalone, operator-run `scripts/generate_codex_personal_study_flashcards.py` into `18_personal_study_flashcards/{lecture_id}/`, independent of the stage 12 Anki deck. (ADR 0045 section 5)
- [x] **MAT-07**: The study-guide script (`scripts/generate_personal_study_guides.py`) merges the critical review into one document, defaults to no figure crop (an explicit `WIDTH:HEIGHT:X:Y` is accepted), and groups units into numbered "Parte N" chapters unless `--chapter-plan` supplies a validated plan. (ADR 0030; ADR 0031; ADR 0044 section 6)

### Critic Gate (Phase 3)

- [x] **GATE-01**: The exam, podcast, personal-study flashcard and study-guide scripts load units only through `load_critic_approved_drafts`, which reads post-repair content from `09_validated_units` in `pack_sequence` order, takes sequence numbers from file names and unit identity from the unit. (ADR 0045 section 1)
- [x] **GATE-02**: A lecture is blocked, and no material is written for it, when a stage 6 or stage 7 directory or file is missing, the sequence has a gap or duplicate, any unit has `review.passed` false, drafts and units differ as sets, a draft changed after its unit, a draft's echoed `pack_sequence` disagrees with its file name, or a unit's identity disagrees with its storage location; a blocked lecture is never published partially. (ADR 0045 section 1)

### Uncertified Boundary (Phase 3)

- [x] **BND-01**: `run-personal-study` is the explicit, opt-in entry point that runs stages 8, 9, 13, 14 and 15 and never calls `require_publication_ready`; `run-all` stops at the stage 10 certification gate. (ADR 0024; ADR 0038 section 6)
- [x] **BND-02**: Stage 10 `QualityCertificateIssuer.issue` keeps raising `QualityCertificationError` unconditionally, the personal-study route never calls `QualityCertificateVerifier.verify` and never forges a certificate, and the certified publication path stays unreachable. (ADR 0024; ADR 0023 section 6; ADR 0045 section 4)
- [x] **BND-03**: Every artifact the Codex scripts produce carries the `UNCERTIFIED_PERSONAL_STUDY` marker in its own payload. (ADR 0024; ADR 0038 section 5)
- [x] **BND-04**: The quality trust key is read from the process environment, else only from the working directory's own `.env` through `dotenv_values` (never parent directories, never written to `os.environ`), and resolution fails closed with `QualityTrustError`. (ADR 0026; ADR 0044 section 5)

### Study Site (Phase 4)

- [x] **WEB-01**: The site generator writes static pages from processed lectures under `workspace/site/`, lists unprocessed lectures as pending, and serves lecture videos from `workspace/site-videos/` at `/videos` behind the same authentication. (ADR 0029; ADR 0040)
- [x] **WEB-02**: Access is limited to an email allowlist with per-person password digests mounted from outside the image (allowlist directory mode 700 and files 600, signing secret in a separate directory); Basic Auth remains for scripts, the health check and deploy. (ADR 0029; ADR 0039)
- [x] **WEB-03**: Sign-in yields a signed session whose key mixes a dedicated server secret with the person's stored password hash and is validated against the allowlist as read now; `GET /sair` clears the cookie; every authorized response sends `Cache-Control: private, no-store`; the denial redirect carries a validated `next`. (ADR 0039)
- [x] **WEB-04**: Password checking is throttled per attempted address with a growing wait (never a lockout), a capped in-flight queue and a global concurrency limiter that sheds load with 429; request bodies are bounded; quiz submissions are throttled per authenticated email and lecture. (ADR 0039; ADR 0041)
- [x] **WEB-05**: Access is decided on the decoded served path; a path containing `?`, `#`, a backslash, NUL or a dot segment is refused with 400; unauthenticated exemptions match method and path exactly. (ADR 0044 section 4)
- [x] **WEB-06**: Every response carries security headers from the outermost middleware, with a hash-based Content-Security-Policy and no `unsafe-inline`; the session cookie is `__Host-pos_session`; login uses a signed double-submit CSRF token; authentication logs carry only pseudonymized account references; the container runs as uid/gid 10001. (ADR 0044 section 4)
- [x] **WEB-07**: Each lecture with a video has a generated `assistir.html` full-page watch page reached through an "Assistir em tela cheia" link; the page reuses `/videos/{lecture}/{video}` with the same authentication and cache headers, ships no captions, and a lecture without video gets no page and no link. (ADR 0040)
- [x] **WEB-08**: `GET /simulado/{lecture_id}` renders only questions stripped of `correct_answer` and `explanation`, and `POST` grades on the server by position against a fresh read of that lecture's `exam.json`, read from an environment-configured path and never copied into the served tree; the lecture page offers "Responder simulado" only when `exam.json` exists at generation time. (ADR 0041)
- [x] **WEB-09**: Every page that renders study material (home, lecture, reading, watch and both mock-exam pages) shows `UNCERTIFIED_NOTICE` at the top; the login page carries none. (ADR 0045 section 3; ADR 0029)
- [x] **WEB-10**: A lecture that has only a video shows "Vídeo sem material de estudo gerado." instead of study material. (ADR 0043)

### Publication Boundary (Phase 5)

- [x] **PUB-01**: Local lectures use synthetic ids 925100-925199 (semester 1) and 925200-925299 (semester 2); registration is transactional and idempotent under a register-versus-register file lock; re-registration may change only the publish flag; demotion is refused while a public bundle exists. (ADR 0042)
- [x] **PUB-02**: A local lecture is private by default: it compiles into `--private-output-root` (default `workspace/study-guides-local`), a private root nested inside the public root is refused, publishing needs `--publish` (or `publish: true`) plus a recompile, and the academic catalog lists only published local lectures. (ADR 0042)
- [x] **PUB-03**: `scripts/list_withheld_lecture_ids.py` lists unpublished reserved ids (and, for exams, published video-only ids) so the external deploy step can exclude them from the study-guide and mock-exam sync. (ADR 0042; ADR 0043)
- [x] **PUB-04**: A video-only lecture (`--video-only`) registers a raw-video link and metadata row without a `queue.json` row, compiles without reading evidence packs, ledgers, drafts or queue titles into a bundle of exactly `indice.md`, `.bundle.json` and a hard-linked `video/aula.mp4` checked before atomic publication, and has no exam. (ADR 0043)

### Public Repository (Phase 5)

- [x] **REPO-01**: The repository excludes portal discovery and acquisition, portal credentials and deploy tooling; lectures enter only through `scripts/register_local_lecture.py`; `config/targets.example.yaml` documents the catalog shape; every identifier in code, tests and documentation is synthetic. (ADR 0044 sections 1-2)
- [x] **REPO-02**: Versioned hooks (`pre-commit`, `pre-merge-commit`, `commit-msg`, `pre-push`), installed as thin wrappers by `make hooks`, reject denylisted terms and secrets in staged content, messages, ref names, identities and pushed history, and are exercised end to end by `tests/test_git_hooks.py`. (ADR 0044 section 3)
- [x] **REPO-03**: The denylist scanner keeps the private denylist outside the repository, folds text before comparing, reports a match by term position and never by the term, and fails on a missing, unreadable or empty denylist. (ADR 0044 section 3)
- [x] **REPO-04**: CI runs `secret-scan`, `denylist-scan` (failing closed when the `DENYLIST` secret is absent) and `test` (ruff, mypy, pytest on macOS) on every push to `main` and every pull request, with actions pinned by SHA, a read-only token and no persisted checkout credentials. (ADR 0044 section 3)

### Pluggable LLM Gateway (Phase 6) - open

- [x] **GW-01**: An `LlmGateway` port is defined inward of the scripts (application layer); the six LLM-backed stages (5 semantic ledgers, 6 drafts, 7 critic, 13 exam, 14 podcast, 18 personal-study flashcards) call it instead of spawning the Codex CLI directly. (Operator decision 2026-10-14; ADR 0038 section 1; ADR 0045 section 2)
- [ ] **GW-02**: `CodexCliAdapter` preserves current Codex behavior: bounded child process, sanitized environment, read-only sandbox, and failure capture under `tmp/codex_phase_failures/`. (Operator decision 2026-10-14; ADR 0035; ADR 0038 section 1)
- [ ] **GW-03**: `LlamaCppAdapter` runs a local GGUF model through llama.cpp, restored from the repository history before ADR 0038 and adapted to the port, with no network egress. (Operator decision 2026-10-14; ADR 0038 section 8; ADR 0045 section 2)
- [x] **GW-04**: The adapter is selected by configuration (globally, optionally per stage), defaults to Codex, and an unknown or misconfigured adapter fails closed before any call. (Operator decision 2026-10-14)
- [ ] **GW-05**: A new ADR amends ADR 0038 (the local text-LLM route is reinstated as a gateway adapter) and closes ADR 0045 section 2 (content that may carry patient-identifying data runs only on the local adapter). Baseline requirement GEN-07 is amended by this record. (Operator decision 2026-10-14; ADR 0038; ADR 0045 section 2)
- [ ] **GW-06**: Both adapters pass the same contract tests through the shared JSON-contract parser and the critic repair loop; CI exercises the llama.cpp adapter with a stub binary. (Operator decision 2026-10-14; ADR 0022 operational amendment; ADR 0036; ADR 0044 section 3)

### Synthetic Demo Lecture and Measured Results (Phase 7) - open

Former ROLL-03 (stages 8 and 9 on lecture data) is absorbed by DEMO-02 and former ROLL-04 (blocked-lecture recovery) by DEMO-04; both IDs are retired.

- [ ] **DEMO-01**: A synthetic veterinary lecture authored for the repository (own script, own slides, Kokoro narration, ffmpeg composition), containing no third-party content and no patient data, is reproducible from versioned sources by a make target. (Operator decision 2026-10-14; ADR 0032; ADR 0044 section 2)
- [ ] **DEMO-02**: The demo lecture runs through `run-all` and `run-personal-study`, stages 8 and 9 included, to a signed-in page view on the locally served site. (Operator decision 2026-10-14; absorbs former ROLL-03; ADR 0024 Context; ADR 0045 Alternatives)
- [ ] **DEMO-03**: The pipeline's quality is reported with measured numbers: ASR word error rate against the known script before and after the terminology dictionary, schema pass rate per LLM stage, critic rejection rate, and per-stage wall time, for each adapter that is run. (Operator decision 2026-10-14; ADR 0028; ADR 0036)
- [ ] **DEMO-04**: The blocked-lecture recovery documented in ADR 0045 is demonstrated on the demo lecture: a rejected or stale unit blocks all four derivative scripts; after the recovery (delete the lecture's units that no longer have a draft, then run `scripts/generate_codex_critic_review.py` for the lecture directly) they produce the exam, podcast, flashcards and study guide. (Operator decision 2026-10-14; absorbs former ROLL-04; ADR 0045 section 1 and Consequences)

### Portfolio README and Documentation (Phase 8) - open

- [ ] **DOC-01**: An English README written for a technical reader or recruiter replaces the "under active extraction" placeholder and presents the problem, an architecture diagram (Mermaid), the stage map, the design decisions with links to their ADRs, the demo results from DEMO-03, and a quick start. (Operator decision 2026-10-14; repository README; ADR 0044 Consequences)
- [ ] **DOC-02**: An end-to-end walkthrough of the demo lecture has been executed once, and every command in it exists as written. (Operator decision 2026-10-14; repository README "end-to-end demo"; ADR 0044 Consequences)
- [ ] **DOC-03**: The documentation states the operating boundaries as the ADRs define them: the egress rule and the unchecked provider terms (ADR 0045 section 2), the uncertified status and notice (ADR 0024; ADR 0045 section 3), the absence of certification standing (ADR 0024), blocked-lecture recovery (ADR 0045), page regeneration after any inline script or style change (ADR 0044), the single-worker site constraint (ADR 0039), and the FileVault revisit triggers (ADR 0010).

### Pre-Public Gate and Release (Phase 9) - open

- [ ] **PUBG-01**: A denylist scan and a gitleaks scan over every ref and the full history pass with no finding. (Operator decision 2026-10-14; ADR 0044 section 3)
- [ ] **PUBG-02**: A final whole-repository code review and security review (`gsd-code-review` and `gsd-secure-phase`) close with no open blocking finding. (Operator decision 2026-10-14; operator rule R0.4)
- [ ] **PUBG-03**: After the operator's final check, the repository visibility is switched to public; the CI `DENYLIST` secret and the leak-gate hooks stay in force. (Operator decision 2026-10-14; ADR 0044 section 3)

## v2 Requirements

Deferred. Tracked, not in the current roadmap. None is an ADR-committed work item: each is either explicitly deferred or conditional in an ADR, a not-yet-implemented part of the ADR 0023 target architecture, or an observation from the codebase map. Promotion into a roadmap phase needs an operator decision.

### Conditional or Deferred by an ADR

- **COND-01**: Size check for the single whole-lecture podcast prompt, with a size-aware retry. (ADR 0037; revisit if an oversize prompt is observed on a real lecture)
- **COND-02**: Re-transcribe the 25 transcripts on disk that predate the terminology correction. (ADR 0028; deferred)
- **COND-03**: Re-extract lectures extracted before ADR 0025 at the 37.5-second frame interval. (ADR 0025; they keep 150-second density until re-extracted)
- **COND-04**: Orchestrator reruns the critic after a re-draft instead of deciding by file sequence numbers alone. (ADR 0045 Consequences; accepted today)
- **COND-05**: Shared module boundary for the helper that `generate_codex_critic_review.py` imports privately from `generate_codex_drafts.py`. (ADR 0036; recorded coupling)
- **COND-06**: Before the site or the study-guide merge reads an exam, podcast or flashcard artifact, check that it was produced under the critic gate. (ADR 0045 section 1 notes both read artifacts "without checking how they were produced"; no decision recorded)

### ADR 0023 Target Architecture Remainder

Not implemented, and not listed in the readings that exclude parts of ADR 0023. Sections 5, 6 and 10 (evaluation, certificates, reference corpus) are omitted because they serve the certified path, which stays unreachable until a new record exists (roadmapper judgment); sections 9 and 11-13 are omitted as a method rule and as contracts already present in code (roadmapper judgment). The operator may promote any of them.

- **TARGET-01**: Deterministic completeness manifests and prompt-budget preflight, and conceptual Learning Units (target five to ten minutes) replacing frame-driven evidence packs. (ADR 0023 sections 1 and 3)
- **TARGET-02**: Atomic source content ledger with timestamped evidence, hierarchical refinement, and an exhaustive study-material deliverable. (ADR 0023 sections 2 and 4)
- **TARGET-03**: Resource-aware DAG execution with bounded pools (LLM generation 1, Whisper 1). (ADR 0023 section 7)
- **TARGET-04**: Lecture-centric workspace layout after a tested, non-destructive migration. (ADR 0023 section 8)
- **TARGET-05**: Complementary lecture documents as a later bounded phase. (ADR 0023 section 14)

### Codebase-Map Hardening Candidates

Provenance: `.planning/codebase/` (CONCERNS.md, STACK.md, ARCHITECTURE.md), not the ADR intel.

- **HARD-01**: Split the oversized modules along existing seams (`quality/calibration_promotion.py`, `quality/delivery.py`, `study_materials/bundle.py` and others in CONCERNS.md). Decide first whether the certified-path quality modules should be split or pruned, since that path stays unreachable.
- **HARD-02**: Narrow broad exception handlers to domain errors outside the batch-isolation points ADR 0002 requires. CONCERNS.md counts about 38 matching lines and CONVENTIONS.md counts 9 broad catches; re-count before planning.
- **HARD-03**: Build the site image's dependency set from `uv.lock` instead of the hand-maintained pins in `Dockerfile.site`, keeping the `site/` plus `domain/models.py` import isolation.
- **HARD-04**: Validate the site signing secret once at startup (fail fast) rather than first at request time. (ADR 0039)
- **HARD-05**: Attach discipline and topic context to pending lectures on the site (the `TODO` in `site/catalog.py`; ADR 0029 pending list).
- **HARD-06**: Add a test for `scripts/generate_progress_dashboard.py` and end-to-end tests of the SQLite quality stores across schema changes.
- **HARD-07**: Record the tested version of each external tool and check it before each run (`scripts/01_provision_engines.sh`, `ops/model_attestation.py`).
- **HARD-08**: Inject the workspace root instead of cwd-relative path constants, and defer stage imports in the orchestrator.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Former ROLL-01: regenerating through the critic gate, or deleting, every exam, podcast script and audio file, personal-study flashcard deck and study guide generated before the gate | Concerns the private source deployment and its served site, which hold the real data; this repository has none. Tracked outside this repository |
| Former ROLL-02: regenerating the deployed site so every page carries the current `UNCERTIFIED_NOTICE` and matches the Content-Security-Policy hash list | Concerns the private source deployment and its served site, which hold the real data; this repository has none. Tracked outside this repository |
| Local text-LLM execution outside the LLM gateway (the route ADR 0038 removed, with `--llm-model-path` and `--llm-model-family`) | Removed by ADR 0038; local text generation returns only as the Phase 6 `LlamaCppAdapter` behind the `LlmGateway` port (GW-03); baseline requirement GEN-07 will be amended by GW-05 |
| ADR 0023 sections 15 and 16, the "Production semantic runtime and promotion boundary" subsection, the "Implementation status" list, GGUF-bound parts of section 6 | Operator-approved reading; superseded by ADR 0038 and ADR 0045 section 4 |
| Certified publication path (certificate issuance, derivative certification, reference corpus, calibration authority) | Stays unreachable (ADR 0024; ADR 0045 section 4); a future certified route needs a new record on generator-evaluator independence |
| Any claim of clinical, academic or publication authority, or relabeling material as certified | ADR 0024 clause stays in force |
| Technical detection of patient-identifying data before a Codex call | ADR 0045 section 2 makes the rule procedural; accepted |
| Serving only certified material | Rejected in ADR 0045: no material is certified, so the site would be empty |
| Skipping rejected units to publish a partial lecture | Rejected in ADR 0045 section 1: the reader could not tell the material was incomplete |
| Portal discovery and acquisition, portal credentials, deploy and hosting tooling | Excluded from the public repository (ADR 0044 section 1) |
| Application-level encryption of `workspace/data/` | ADR 0010 delegates to FileVault; revisit for a shared workstation, cloud sync or a second operator account |
| Captions or a transcript track on the watch page | ADR 0040 |
| Multi-worker site deployment with shared throttle and session state | Single process by design (ADR 0039); revisit before adding workers or replicas |
| Revoking one device's session without rotating the password | ADR 0039 addendum records it as impossible |

## Traceability

Which phases cover which requirements. Each requirement maps to exactly one phase.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SPINE-01 | Phase 1 | Complete |
| SPINE-02 | Phase 1 | Complete |
| SPINE-03 | Phase 1 | Complete |
| SPINE-04 | Phase 1 | Complete |
| SPINE-05 | Phase 1 | Complete |
| SPINE-06 | Phase 1 | Complete |
| INGEST-01 | Phase 1 | Complete |
| INGEST-02 | Phase 1 | Complete |
| INGEST-03 | Phase 1 | Complete |
| INGEST-04 | Phase 1 | Complete |
| INGEST-05 | Phase 1 | Complete |
| INGEST-06 | Phase 1 | Complete |
| INGEST-07 | Phase 1 | Complete |
| GEN-01 | Phase 2 | Complete |
| GEN-02 | Phase 2 | Complete |
| GEN-03 | Phase 2 | Complete |
| GEN-04 | Phase 2 | Complete |
| GEN-05 | Phase 2 | Complete |
| GEN-06 | Phase 2 | Complete |
| GEN-07 | Phase 2 | Complete |
| ASM-01 | Phase 3 | Complete |
| ASM-02 | Phase 3 | Complete |
| ASM-03 | Phase 3 | Complete |
| ASM-04 | Phase 3 | Complete |
| MAT-01 | Phase 3 | Complete |
| MAT-02 | Phase 3 | Complete |
| MAT-03 | Phase 3 | Complete |
| MAT-04 | Phase 3 | Complete |
| MAT-05 | Phase 3 | Complete |
| MAT-06 | Phase 3 | Complete |
| MAT-07 | Phase 3 | Complete |
| GATE-01 | Phase 3 | Complete |
| GATE-02 | Phase 3 | Complete |
| BND-01 | Phase 3 | Complete |
| BND-02 | Phase 3 | Complete |
| BND-03 | Phase 3 | Complete |
| BND-04 | Phase 3 | Complete |
| WEB-01 | Phase 4 | Complete |
| WEB-02 | Phase 4 | Complete |
| WEB-03 | Phase 4 | Complete |
| WEB-04 | Phase 4 | Complete |
| WEB-05 | Phase 4 | Complete |
| WEB-06 | Phase 4 | Complete |
| WEB-07 | Phase 4 | Complete |
| WEB-08 | Phase 4 | Complete |
| WEB-09 | Phase 4 | Complete |
| WEB-10 | Phase 4 | Complete |
| PUB-01 | Phase 5 | Complete |
| PUB-02 | Phase 5 | Complete |
| PUB-03 | Phase 5 | Complete |
| PUB-04 | Phase 5 | Complete |
| REPO-01 | Phase 5 | Complete |
| REPO-02 | Phase 5 | Complete |
| REPO-03 | Phase 5 | Complete |
| REPO-04 | Phase 5 | Complete |
| GW-01 | Phase 6 | Complete |
| GW-02 | Phase 6 | Pending |
| GW-03 | Phase 6 | Pending |
| GW-04 | Phase 6 | Complete |
| GW-05 | Phase 6 | Pending |
| GW-06 | Phase 6 | Pending |
| DEMO-01 | Phase 7 | Pending |
| DEMO-02 | Phase 7 | Pending |
| DEMO-03 | Phase 7 | Pending |
| DEMO-04 | Phase 7 | Pending |
| DOC-01 | Phase 8 | Pending |
| DOC-02 | Phase 8 | Pending |
| DOC-03 | Phase 8 | Pending |
| PUBG-01 | Phase 9 | Pending |
| PUBG-02 | Phase 9 | Pending |
| PUBG-03 | Phase 9 | Pending |

Retired IDs: ROLL-01 and ROLL-02 moved to Out of Scope; ROLL-03 and ROLL-04 were absorbed by DEMO-02 and DEMO-04 (operator decision 2026-10-14).

**Coverage:**
- v1 requirements: 71 total (55 built, 16 open)
- Mapped to phases: 71
- Unmapped: 0

---
*Requirements defined: 2026-10-14*
*Last updated: 2026-10-14 after the operator-approved restructure of milestone v1.0 (Phases 6-9)*
