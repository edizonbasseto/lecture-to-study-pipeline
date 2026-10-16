---
last_mapped_commit: 0256659c3fcdb7371d6f16cef4aabba42a0a53e9
last_mapped_at: 2026-10-14
---
<!-- refreshed: 2026-10-14 -->

# Architecture

**Analysis Date:** 2026-10-14

## System Overview

```text
┌──────────────────────────────────────────────────────────────────────┐
│ Entry points                                                          │
│  `src/lecture_study/cli/main.py` (run-all, run-phase, run-personal-  │
│   study)   `src/lecture_study/cli/orchestrator.py` (phase graph)      │
│  `src/lecture_study/site/app.py` (FastAPI read-only site)            │
│  `scripts/*.py` (Codex / generation / catalog / audit helpers)       │
└───────────────┬───────────────────────────────┬──────────────────────┘
                │                               │
                ▼                               ▼
┌───────────────────────────────────┐  ┌──────────────────────────────┐
│ Processing stages (numbered)      │  │ Presentation                  │
│  extraction, transcription,       │  │  `site/` (generator, renderer,│
│  evidence, semantic, drafting,    │  │   access, quiz, sessions)     │
│  critic, assembly, metadata,      │  │  reads `workspace/site/`      │
│  publishing, flashcards, exams,   │  └──────────────────────────────┘
│  podcast, tts, captioning         │
└───────────────┬───────────────────┘
                │ read/write files under workspace/data/NN_*
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Quality & trust layer: `src/lecture_study/quality/`                  │
│  certification, issuer, delivery, gate evidence, human review,       │
│  calibration, numeric/evidence auditors, student-facing text guard   │
└───────────────┬──────────────────────────────────────────────────────┘
                ▼
┌──────────────────────────────────────────────────────────────────────┐
│ Pure domain model: `src/lecture_study/domain/`                       │
│  models, errors, versioning, source universe, content ledger        │
└──────────────────────────────────────────────────────────────────────┘
        ▲ shared infrastructure helpers: `src/lecture_study/ops/`
          (process lifecycle, LLM response contract, model attestation,
           process environment, catalog bridge)
```

## Pattern Overview

**Overall:** Layered, file-based batch pipeline with a numbered phase graph, plus a separate read-only web presentation layer. Stages communicate through artifacts on disk, not in-memory objects.

**Key Characteristics:**
- Each pipeline stage is a package under `src/lecture_study/<stage>/` with a `pipeline.py` or `generator.py` entry and an `__init__.py` re-export surface.
- Stage boundaries are filesystem directories named `workspace/data/NN_<stage>/` (for example `workspace/data/05_transcribed`, `workspace/data/06_evidence_packs`). Idempotence is checked by whether a stage's output artifact exists for a lecture (`_phase_N_artifact_exists` in `src/lecture_study/cli/orchestrator.py`).
- Relative path constants (`Path("workspace/data/...")`) are declared at module top level and resolved against the current working directory; the orchestrator is therefore expected to run from the repository root.
- Heavy LLM/ML work is delegated to subprocesses (Codex CLI scripts under `scripts/`, whisper.cpp / local TTS workers) and supervised through `src/lecture_study/ops/process_lifecycle.py`.
- Quality is a gate, not an afterthought: certification and delivery manifests (`src/lecture_study/quality/certification.py`, `src/lecture_study/quality/delivery.py`) are required before publication.
- Architectural decisions are recorded as ADRs in `docs/decisions/` (0001 to 0044); code comments and tests reference them.

## Layers

**Domain (pure models and rules):**
- Purpose: Typed models, error taxonomy, versioning, source-universe and content-ledger rules.
- Location: `src/lecture_study/domain/`
- Contains: Pydantic models (`models.py`), `errors.py` (`PipelineExecutionError` and siblings), `versioning.py`, `semantic_observation.py`, `source_universe.py`, `content_ledger.py`, `calibration.py`, `expert_corpus.py`, `human_review.py`, `clause_reconciliation.py`.
- Depends on: Standard library and Pydantic only (imports from other `lecture_study` packages were not found in `domain/`).
- Used by: Almost every other package (`quality/`, `site/`, `cli/`, `study_materials/`, stage packages).

**Quality and trust:**
- Purpose: Decide whether a produced artifact is certified, issue certificates, build delivery manifests, and audit evidence.
- Location: `src/lecture_study/quality/`
- Contains: `certification.py`, `issuer.py`, `delivery.py` (about 2,770 lines, largest module in the repo), `gate_evidence.py`, `evidence_auditor.py`, `numeric_source_binding.py`, `proposition_coverage.py`, `student_facing_text.py`, `human_review*.py`, `calibration*.py`, and SQLite-backed stores (`*_sqlite.py`).
- Depends on: `lecture_study.domain` (about 40 imports), `lecture_study.quality` itself, and `lecture_study.source_content` (one import).
- Used by: `cli/orchestrator.py`, `publishing/pipeline.py`, `study_materials/`, `scripts/`.

**Ops (shared runtime support):**
- Purpose: Process spawning and lifecycle, LLM response contracts, model attestation, environment construction, and bridging to the captioning catalog.
- Location: `src/lecture_study/ops/`
- Contains: `process_lifecycle.py`, `process_environment.py`, `llm_response_contract.py`, `llm_instructions.py`, `model_attestation.py`, `semantic_ledger_support.py`, `catalog_bridge.py`.
- Depends on: `lecture_study.domain`, `lecture_study.quality`.
- Used by: `cli/orchestrator.py`, `study_materials/`, `scripts/attest_local_actors.py`.

**Processing stages:**
- Purpose: Turn a raw lecture video into study artifacts, one stage per numbered directory.
- Location: `src/lecture_study/{extraction,transcription,evidence,semantic,drafting,assembly,metadata,publishing,flashcards,exams,podcast,tts,captioning}/`
- Contains: Stage pipelines and workers, for example `transcription/whisper_worker.py`, `transcription/terminology.py` (ASR vocabulary correction), `extraction/ocr_worker.py`, `extraction/ocr_supervisor.py`, `evidence/chunker.py`, `drafting/depth_report.py`, `exams/echo_scan.py`, `exams/option_shuffle.py`, `tts/generator.py`.
- Depends on: `domain`, `quality`, `ops`.
- Used by: `cli/orchestrator.py`.

**Study materials:**
- Purpose: Build the bundles that the site and publishing consume (academic and personal-study modes, local lectures, video-only bundles, temporal coverage).
- Location: `src/lecture_study/study_materials/`
- Contains: `compiler.py`, `bundle.py` (about 1,900 lines), `academic_catalog.py`, `academic_typst.py`, `local_lectures.py`, `video_only_bundle.py`, `temporal_coverage.py`.
- Depends on: `domain`, `ops`, `exams`, `evidence`.
- Used by: `cli/main.py` (`run-personal-study`), `scripts/generate_personal_study_guides.py`.

**Site (presentation):**
- Purpose: Generate a static HTML site from pipeline output and serve it behind an access gate.
- Location: `src/lecture_study/site/`
- Contains: `generator.py` (writes `workspace/site/`), `renderer.py` (Jinja2 and markdown-it), `app.py` (FastAPI application factory `create_app`), `access.py` (credentials-based gate), `sessions.py`, `signing.py`, `login_csrf.py`, `throttle.py`, `security_headers.py`, `quiz.py` (server-graded mock exam), `catalog.py`.
- Depends on: `lecture_study.site` internally and `lecture_study.domain` (2 imports). It deliberately does not import the processing stages. The `Dockerfile.site` copies only `src/lecture_study/site/` and `domain/models.py` into the image.
- Used by: `uvicorn lecture_study.site.app:app` (`make site`), `Dockerfile.site`.

**CLI and orchestration:**
- Purpose: Operator entry point and phase scheduler.
- Location: `src/lecture_study/cli/`
- Contains: `main.py` (argparse with subcommands `run-all`, `run-phase`, `run-personal-study`), `orchestrator.py` (`PhaseSpec` table, queue handling in `workspace/data/queue.json`, subprocess dispatch to `scripts/generate_codex_*.py`).
- Depends on: Every stage package, `quality`, `ops`, `publishing`.
- Used by: Operator via `make run` (`python -m lecture_study.cli.main run-all ...`).

## Data Flow

### Primary Request Path (batch pipeline, `run-all`)

1. `main.py` parses `run-all <lecture>` and builds an `OrchestratorContext` (`src/lecture_study/cli/orchestrator.py`, `_build_single_lecture_queue` writes a one-lecture queue file under `workspace/data/queue.json`).
2. `orchestrator.py` walks the `PhaseSpec` list (the `PhaseSpec` tuple near line 553) in order. For each phase it calls `_phase_N_artifact_exists`; a phase is skipped when its artifact is present.
3. Each phase runs its stage module (for example `extraction/pipeline.py` for phase 2, `transcription/pipeline.py` for phase 3, `evidence/pipeline.py` for phase 4) or a generation script in `scripts/` through a subprocess, with output in `workspace/data/NN_<stage>/`.
4. LLM-backed phases (semantic 5, drafting 6, critic 7, mock exams 13, podcast 14) go through `scripts/generate_codex_*.py`, with contracts from `ops/llm_response_contract.py`.
5. Assembly (8), metadata (9), and publishing (11) produce canonical documents in `workspace/data/10_canonical_documents/` and `workspace/data/12_published/`; certification and delivery (`quality/certification.py`, `quality/delivery.py`) gate publication.
6. Flashcards (12), exams (13), podcast scripts (14), and TTS audio (15, `tts/generator.py` writing `workspace/data/16_podcast_audio/`) are downstream products of the same lecture.
7. `scripts/generate_*` and `site/generator.py` (`SITE_OUTPUT_DIR = workspace/site`) emit the static site.

### Site Request Path

1. Request enters `create_app` in `src/lecture_study/site/app.py`.
2. Security middleware and routing apply `security_headers.py`, `throttle.py`, and the `_AccessGate` / `_QuizGate` classes in `app.py`.
3. Unauthenticated routes are limited to the login flow (`_is_unauthenticated_route`); other paths require a session from `site/sessions.py` checked against `site/access.py`.
4. Static pages are served from `workspace/site/`; quiz submissions are graded server-side in `site/quiz.py` using mock-exam files under `MOCK_EXAMS_DIR`.

### Personal study mode

1. `run-personal-study` in `cli/main.py` uses `study_materials/local_lectures.py` (registry at `config/local-lectures.local.yaml`, raw videos under `workspace/data/01_raw`).
2. Output goes to `workspace/study-guides-local/` (private) or `workspace/study-guides/` (public), gated by `quality/delivery.py` and ADR 0042.

**State Management:**
- Persistent state lives in files: `workspace/data/*` stage directories, `workspace/data/queue.json`, `workspace/data/quality_certificates/`, `workspace/data/delivery_manifests/`.
- Some quality stores use SQLite (`quality/calibration_promotion_sqlite.py`, `quality/expert_corpus_sqlite.py`, `quality/visual_observation_sqlite.py`).
- The site uses signed tokens and session handling (`site/sessions.py`, `site/signing.py`); the storage backend for sessions was not verified during this mapping.

## Key Abstractions

**PhaseSpec / OrchestratorContext:**
- Purpose: Describe one pipeline phase (number, name, required flag, artifact predicate, runner) and the shared run context.
- Examples: `src/lecture_study/cli/orchestrator.py` (lines around 139 to 175 and 553 to 572)
- Pattern: Declarative table of phases plus per-phase predicate and runner functions.

**Stage pipeline modules:**
- Purpose: Each stage exposes an orchestrating `pipeline.py` (or `generator.py`) that reads the previous `workspace/data/NN_*` directory and writes its own.
- Examples: `src/lecture_study/transcription/pipeline.py`, `src/lecture_study/evidence/pipeline.py`, `src/lecture_study/publishing/pipeline.py`, `src/lecture_study/tts/generator.py`, `src/lecture_study/flashcards/generator.py`
- Pattern: Path constants at module top (`*_INPUT_DIR`, `*_OUTPUT_DIR`) plus a pipeline class or function.

**Domain errors:**
- Purpose: Typed failures that the orchestrator and CLI map to exit behavior.
- Examples: `src/lecture_study/domain/errors.py` (`PipelineExecutionError`)
- Pattern: Exceptions raised inside stages, caught at the orchestrator boundary (`_find_outcome_error`, `_persist_failed_phase_output` in `cli/orchestrator.py`).

**Trust and certificates:**
- Purpose: Record that an artifact passed a gate, with its evidence.
- Examples: `src/lecture_study/quality/issuer.py`, `src/lecture_study/quality/trust.py`, `src/lecture_study/quality/gate_evidence.py`
- Pattern: Issuer produces certificates into `workspace/data/quality_certificates/`; delivery reads them before publishing.

**LLM response contract:**
- Purpose: Validate structured model output and fix the instruction prompt to stable prefixes.
- Examples: `src/lecture_study/ops/llm_response_contract.py`, `src/lecture_study/ops/llm_instructions.py`, `src/lecture_study/quality/prompt_budget.py`
- Pattern: Fixed instruction and schema first, variable evidence last.

## Entry Points

**Pipeline CLI:**
- Location: `src/lecture_study/cli/main.py` (module entry via `python -m lecture_study.cli.main`; a `[project.scripts]` table was not found in `pyproject.toml`)
- Triggers: Operator command, `make run`
- Responsibilities: Argument parsing and dispatch to `run-all`, `run-phase`, `run-personal-study`.

**Orchestrator:**
- Location: `src/lecture_study/cli/orchestrator.py`
- Triggers: Called from `main.py`
- Responsibilities: Phase graph, idempotence checks, subprocess dispatch, failure logs under `tmp/codex_phase_failures/`.

**Web application:**
- Location: `src/lecture_study/site/app.py` (`app` object created by `create_app`)
- Triggers: `make site` (uvicorn with `--reload`, port 8000); container via `Dockerfile.site` and `infra/docker-entrypoint.sh`
- Responsibilities: Authentication, access control, quiz grading, static content serving.

**Operational scripts:**
- Location: `scripts/` (for example `scripts/generate_codex_drafts.py`, `scripts/register_local_lecture.py`, `scripts/denylist_scan.py`, `scripts/01_provision_engines.sh`)
- Triggers: Orchestrator subprocesses, operator, git hooks (`.githooks/pre-commit`, `.githooks/pre-push`) and CI (`.github/workflows/ci.yml`)
- Responsibilities: Single-stage generation, provisioning, catalog and audit helpers.

## Architectural Constraints

- **Threading:** The orchestrator uses `asyncio` (`cli/orchestrator.py`, `cli/main.py`) with subprocesses for heavy work. The site is an async FastAPI app (`site/app.py`).
- **Global state:** Module-level path constants (`workspace/...`, `config/...`) in every stage package; `_PROJECT_ROOT` in `quality/delivery.py` derived from `__file__`. Stage paths are relative to the current working directory, not to the package.
- **Circular imports:** None observed in the import sampling. `domain` does not import other project packages.
- **Dependency isolation:** `site/` must not import processing stages; `Dockerfile.site` only copies `site/` and `domain/models.py`, so any new import from another package breaks the container build.
- **Private data boundary:** Private lectures and study guides are kept under separate roots and checked by `scripts/denylist_scan.py` and `scripts/list_withheld_lecture_ids.py` (ADRs 0042 and 0044).

## Anti-Patterns

### Hard-coded relative workspace paths in stage modules

**What happens:** Each stage module defines `Path("workspace/data/...")` constants (for example `src/lecture_study/transcription/pipeline.py` lines 36 to 37, `src/lecture_study/evidence/pipeline.py` lines 25 to 26).
**Why it's wrong:** Behavior depends on the current working directory; running from elsewhere silently reads and writes the wrong tree, and tests must `chdir` or monkeypatch the constants.
**Do this instead:** Inject the workspace root through the stage's constructor or the `OrchestratorContext`, and keep the existing constants only as defaults. Reference `src/lecture_study/cli/orchestrator.py` for how the context is passed.

### Orchestrator importing every stage

**What happens:** `src/lecture_study/cli/orchestrator.py` imports `assembly`, `evidence`, `exams`, `extraction`, `flashcards`, `metadata`, `publishing`, `quality`, `transcription`, `tts` at module load (lines 40 to 78).
**Why it's wrong:** Any change to a stage's imports makes the whole CLI heavier and couples unrelated phases; this is the main reason the site image had to be kept separate.
**Do this instead:** Move stage imports into the runner functions so that importing the orchestrator does not load every stage.

### Quality logic in one very large module

**What happens:** `src/lecture_study/quality/delivery.py` (about 2,770 lines), `quality/calibration_promotion.py` (about 3,550 lines), and `study_materials/bundle.py` (about 1,890 lines) combine several responsibilities.
**Why it's wrong:** Hard to test in isolation and to review for gate regressions.
**Do this instead:** Split by responsibility (manifest building, certificate verification, persistence) with `*_sqlite.py` adapters kept at the edge, as already done for `quality/expert_corpus_sqlite.py`.

## Error Handling

**Strategy:** Typed domain exceptions raised in stages, converted to phase outcomes at the orchestrator; failed-phase output is persisted for diagnosis.

**Patterns:**
- Raise `PipelineExecutionError` (from `src/lecture_study/domain/errors.py`) inside stages.
- The orchestrator inspects outcomes with `_find_outcome_error` and persists partial output with `_persist_failed_phase_output` (`src/lecture_study/cli/orchestrator.py`, failure logs under `tmp/codex_phase_failures/`).
- The site returns explicit refusal responses (`_FormRefusal`, `_denial_for`, `_method_not_allowed_response` in `src/lecture_study/site/app.py`) rather than raising to the client.

## Cross-Cutting Concerns

**Logging:** Standard `logging` (imported in `cli/main.py` and `cli/orchestrator.py`); uvicorn logging configured by `infra/uvicorn-logging.json`.
**Validation:** Pydantic models in `domain/models.py` and `ops/llm_response_contract.py`; `TypeAdapter` used in the orchestrator.
**Authentication:** Site only: credentials in `site/access.py` (path set by `CREDENTIALS_PATH`), signed sessions (`site/sessions.py`, `site/signing.py`), CSRF on login (`site/login_csrf.py`), request throttling (`site/throttle.py`). The pipeline itself has no user auth.
**Secrets and environment:** Loaded from `.env` by the dotenv resolution described in ADR 0026 (`.env.example` documents keys; values are never recorded here).

---

*Architecture analysis: 2026-10-14*
