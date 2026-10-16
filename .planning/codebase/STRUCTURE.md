---
last_mapped_commit: 0256659c3fcdb7371d6f16cef4aabba42a0a53e9
last_mapped_at: 2026-10-14
---
# Codebase Structure

**Analysis Date:** 2026-10-14

## Directory Layout

```
lecture-to-study-pipeline/
├── src/lecture_study/        # Python package (all production code)
│   ├── cli/                  # argparse entry point and phase orchestrator
│   ├── domain/               # pure models, errors, versioning
│   ├── quality/             # certification, delivery, trust, calibration
│   ├── ops/                  # process lifecycle, LLM contracts, attestation
│   ├── site/                 # FastAPI app, static site generator, access gate
│   ├── study_materials/      # bundles for academic and personal modes
│   ├── extraction/           # phase 2: slide/frame extraction and OCR
│   ├── transcription/        # phase 3: whisper.cpp transcription, ASR terms
│   ├── evidence/             # phase 4: evidence packs and chunking
│   ├── semantic/             # phase 5: semantic ledger groundedness
│   ├── drafting/             # phase 6: draft depth reports
│   ├── assembly/             # phase 8: assemble validated units
│   ├── metadata/             # phase 9: metadata aggregation
│   ├── publishing/           # phase 11: markdown compilation and publish
│   ├── flashcards/           # phase 12: deterministic flashcards
│   ├── exams/                # phase 13: mock exams, echo scan, option shuffle
│   ├── podcast/              # phase 14: podcast script compiler
│   ├── tts/                  # phase 15: local TTS audio
│   ├── captioning/           # figure captioning (VLM worker, catalog store)
│   └── source_content/       # source resolution, manifests, lecture builder
├── scripts/                  # Codex generation, registration, audits, provisioning
├── tests/                    # pytest suite (flat, one file per module area)
├── docs/decisions/           # ADRs 0001 to 0044
├── config/                   # targets.example.yaml (copy to local, not committed)
├── infra/                    # container entrypoint and uvicorn logging config
├── .githooks/                # versioned git hooks (pre-commit, commit-msg, pre-push, ...)
├── .github/workflows/        # ci.yml
├── .planning/codebase/       # generated codebase maps (this document set)
├── Dockerfile.site           # image for the read-only site
├── Makefile                  # test, lint, run, site, clean, hooks
├── pyproject.toml            # project metadata, pytest, mypy (strict) config
├── uv.lock                   # locked dependencies
└── .python-version           # Python interpreter pin (>=3.12 per pyproject)
```

Runtime-only directories referenced by code but not tracked (see `.gitignore`): `workspace/` (`workspace/data/01_raw` through `workspace/data/17_figure_captions`, `workspace/site`, `workspace/site-videos`, `workspace/study-guides*`), `engines/` (whisper.cpp and llama.cpp checkouts from `scripts/01_provision_engines.sh`), `tmp/` (failed-phase logs).

## Directory Purposes

**`src/lecture_study/cli/`:**
- Purpose: Operator entry point and phase scheduling.
- Contains: `main.py`, `orchestrator.py`, `__init__.py`.
- Key files: `src/lecture_study/cli/main.py` (subcommands), `src/lecture_study/cli/orchestrator.py` (`PhaseSpec` table, phase runners, `_phase_N_artifact_exists` predicates).

**`src/lecture_study/domain/`:**
- Purpose: Framework-free models and rules shared by all packages.
- Contains: `models.py`, `errors.py`, `versioning.py`, `source_universe.py`, `content_ledger.py`, `semantic_observation.py`, `calibration.py`, `expert_corpus.py`, `human_review.py`, `clause_reconciliation.py`, `source_content.py`.
- Key files: `src/lecture_study/domain/models.py` (copied into `Dockerfile.site` image).

**`src/lecture_study/quality/`:**
- Purpose: Gates, certificates, delivery manifests, reviews, calibration, and audit stores.
- Contains: about 40 modules, including `*_sqlite.py` adapters.
- Key files: `certification.py`, `issuer.py`, `delivery.py`, `trust.py`, `gate_evidence.py`, `student_facing_text.py`, `numeric_source_binding.py`.

**`src/lecture_study/ops/`:**
- Purpose: Cross-cutting runtime helpers.
- Contains: process spawning and lifecycle, environment building, LLM response contract and instructions, model attestation.
- Key files: `process_lifecycle.py`, `llm_response_contract.py`, `model_attestation.py`.

**Stage packages (`extraction/`, `transcription/`, `evidence/`, `semantic/`, `drafting/`, `assembly/`, `metadata/`, `publishing/`, `flashcards/`, `exams/`, `podcast/`, `tts/`, `captioning/`):**
- Purpose: One numbered pipeline phase each. Output goes to `workspace/data/NN_<stage>/`.
- Pattern: `__init__.py` (re-exports), a `pipeline.py` or `generator.py` entry, and helpers.
- Key files: `transcription/whisper_worker.py`, `transcription/terminology.py`, `extraction/ocr_worker.py`, `extraction/media_workers.py`, `evidence/chunker.py`, `exams/compiler.py`, `exams/echo_scan.py`, `tts/generator.py`, `captioning/vlm_worker.py`.

**`src/lecture_study/study_materials/`:**
- Purpose: Bundle compilation for academic and personal-study output.
- Key files: `compiler.py`, `bundle.py`, `local_lectures.py`, `video_only_bundle.py`, `academic_typst.py`, `temporal_coverage.py`.

**`src/lecture_study/site/`:**
- Purpose: Static site generation and the authenticated web server.
- Key files: `app.py` (`create_app`), `generator.py` (`SITE_OUTPUT_DIR`), `renderer.py` (Jinja2 and markdown-it), `access.py`, `sessions.py`, `signing.py`, `quiz.py`, `security_headers.py`, `throttle.py`, `login_csrf.py`.

**`src/lecture_study/source_content/`:**
- Purpose: Resolve and manifest source lectures before extraction.
- Key files: `resolver.py`, `manifest.py`, `lecture_builder.py`, `filesystem.py`.

**`scripts/`:**
- Purpose: Executable generation and operations scripts, many launched by the orchestrator as subprocesses.
- Key files: `generate_codex_drafts.py`, `generate_codex_semantic_ledgers.py`, `generate_codex_critic_review.py`, `generate_codex_mock_exam.py`, `generate_codex_podcast.py`, `generate_codex_personal_study_flashcards.py`, `generate_academic_catalog.py`, `generate_progress_dashboard.py`, `register_local_lecture.py`, `caption_lecture_figures.py`, `attest_local_actors.py`, `denylist_scan.py`, `list_withheld_lecture_ids.py`, `01_provision_engines.sh`.
- Note: `scripts/__pycache__/` exists locally and is ignored.

**`tests/`:**
- Purpose: Flat pytest suite; one `test_<area>.py` per module area, with shared fixtures in `tests/conftest.py`.
- Pattern: `test_generate_codex_*.py` mirror `scripts/generate_codex_*.py`; `test_site_*.py` cover `site/`; `test_git_hooks.py` covers `.githooks/`.
- Configured by `[tool.pytest.ini_options]` in `pyproject.toml` (`testpaths = ["tests"]`).

**`docs/decisions/`:**
- Purpose: Architecture Decision Records. Numbering is sequential with gaps (no 0017, 0018, 0019 present).
- Naming: `NNNN-kebab-case-title.md`.

**`.githooks/`:**
- Purpose: Versioned hooks. `_denylist.sh` is shared; `make hooks` installs thin wrappers into `.git/hooks`.

## Key File Locations

**Entry Points:**
- `src/lecture_study/cli/main.py`: pipeline CLI (`run-all`, `run-phase`, `run-personal-study`).
- `src/lecture_study/cli/orchestrator.py`: phase graph and runners.
- `src/lecture_study/site/app.py`: web application object `app`.

**Configuration:**
- `pyproject.toml`: dependencies, pytest, mypy strict, ruff is run by `make lint` (its settings were not inspected).
- `config/targets.example.yaml`: template for targets; copy to a local file, do not commit values.
- `.env.example`: environment keys (the real `.env` is ignored and must not be read or quoted).
- `infra/uvicorn-logging.json`: uvicorn logging.
- `infra/docker-entrypoint.sh`: container start.

**Core Logic:**
- `src/lecture_study/quality/delivery.py`: manifests and publication gate.
- `src/lecture_study/quality/certification.py`: certification rules.
- `src/lecture_study/transcription/terminology.py`: deterministic ASR vocabulary correction.
- `src/lecture_study/ops/llm_response_contract.py`: LLM output contract.

**Testing:**
- `tests/conftest.py`: shared fixtures.
- `tests/test_cli.py`, `tests/test_site_app.py`, `tests/test_quality.py`: broad coverage of the main entry points.

## Naming Conventions

**Files:**
- Python modules: `snake_case.py` (for example `calibration_promotion_sqlite.py`).
- Stage workers and entries: `*_worker.py`, `pipeline.py`, `generator.py`, `compiler.py`, `*_sqlite.py` for SQLite-backed stores.
- Scripts: `scripts/generate_codex_<artifact>.py` for LLM-backed generation; `scripts/01_provision_engines.sh` for numbered setup.
- ADRs: `docs/decisions/NNNN-kebab-title.md`.
- Tests: `tests/test_<module_area>.py`.

**Directories:**
- Python packages: lowercase, underscores where needed (`source_content`, `study_materials`).
- Runtime data: `workspace/data/NN_<stage>/` with two-digit phase prefix (`01_raw` to `17_figure_captions`).

**Identifiers in code:**
- Path constants: `UPPER_SNAKE` ending in `_DIR`, `_PATH`, or `_ROOT` (for example `TRANSCRIBED_OUTPUT_DIR`).
- Phase predicates: `_phase_<N>_artifact_exists`; runners: `_run_phase_<N>`.

## Where to Add New Code

**New pipeline stage:**
- Implementation: `src/lecture_study/<stage>/pipeline.py` (or `generator.py`) with an `__init__.py` re-export.
- Output directory: add a `workspace/data/NN_<stage>` constant in the stage module, and register a `PhaseSpec` plus `_phase_N_artifact_exists` and `_run_phase_N` in `src/lecture_study/cli/orchestrator.py`.
- Tests: `tests/test_<stage>.py`.
- Decision record: add `docs/decisions/00NN-<title>.md` (next free number after 0044).

**New LLM-backed generation step:**
- Script: `scripts/generate_codex_<artifact>.py`, invoked through the orchestrator's subprocess helper.
- Contract: validate output with `src/lecture_study/ops/llm_response_contract.py`.
- Tests: `tests/test_generate_codex_<artifact>.py`.

**New quality gate or certificate type:**
- Implementation: `src/lecture_study/quality/<gate>.py`; wire into `quality/issuer.py` and `quality/delivery.py`.
- Tests: `tests/test_quality*.py` or a dedicated `tests/test_<gate>.py`.

**New domain model or error:**
- Models: `src/lecture_study/domain/models.py` (or a new module under `domain/` that imports no other project package).
- Errors: `src/lecture_study/domain/errors.py`.

**New site page or route:**
- Route and handler: `src/lecture_study/site/app.py` (`_build_application`); rendering in `site/renderer.py`; generation in `site/generator.py`.
- Keep imports inside `lecture_study.site` and `lecture_study.domain` only, so the `Dockerfile.site` image still builds.
- Tests: `tests/test_site_<area>.py`.

**New CLI subcommand:**
- Implementation: `src/lecture_study/cli/main.py` (add to the subcommand table) and the runner in `src/lecture_study/cli/orchestrator.py`.

**Utilities:**
- Shared runtime helpers: `src/lecture_study/ops/`.
- Pure helpers with no I/O: `src/lecture_study/domain/`.

**New operator script:**
- `scripts/<name>.py`, plus a test in `tests/` that imports it (see `tests/test_denylist_scan.py` as a pattern).

## Special Directories

**`workspace/`:**
- Purpose: All stage inputs and outputs, site build, private study guides.
- Generated: Yes.
- Committed: No (ignored by `.gitignore`).

**`engines/`:**
- Purpose: Cloned whisper.cpp and llama.cpp checkouts provisioned by `scripts/01_provision_engines.sh`. Excluded from pytest collection by `testpaths` in `pyproject.toml`.
- Generated: Yes.
- Committed: No.

**`tmp/`:**
- Purpose: Failed-phase diagnostics (`tmp/codex_phase_failures/`).
- Generated: Yes.
- Committed: No.

**`.planning/`:**
- Purpose: GSD planning artifacts; `.planning/codebase/` holds these maps.
- Generated: Yes (by mapping commands).
- Committed: Per project policy.

**`src/lecture_study/**/__pycache__/`:**
- Generated: Yes. Committed: No.

---

*Structure analysis: 2026-10-14*
