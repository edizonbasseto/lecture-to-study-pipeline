---
last_mapped_commit: 0256659c3fcdb7371d6f16cef4aabba42a0a53e9
last_mapped_at: 2026-10-14
---
# Codebase Concerns

**Analysis Date:** 2026-10-14

## Tech Debt

**Oversized modules:**
- Issue: A few modules have grown into multi-thousand-line files that mix several responsibilities (calibration, promotion, SQLite persistence, validation, and reporting in one file).
- Files: `src/lecture_study/quality/calibration_promotion.py` (about 3548 lines), `src/lecture_study/quality/delivery.py` (about 2771 lines), `src/lecture_study/study_materials/bundle.py` (about 1893 lines), `src/lecture_study/quality/expert_corpus.py` (about 1500 lines), `src/lecture_study/quality/visual_observation_sqlite.py` (about 1437 lines), `src/lecture_study/quality/expert_corpus_sqlite.py` (about 1361 lines), `src/lecture_study/quality/human_review_quarantine.py` (about 1090 lines)
- Impact: Changes are hard to review in isolation, and the blast radius of each edit is large. This runs against the single-responsibility and clean-architecture boundaries the project's ADRs describe.
- Fix approach: Split by responsibility along the existing seams (pure decision logic versus SQLite adapter versus CLI/report output). Move SQL out of domain-adjacent modules into the `*_sqlite.py` adapters where they are not already.

**Repeated broad exception handling:**
- Issue: Many pipeline and quality stages catch `Exception` and convert it into a stored error or a log line. Several of these handlers also use `pass`, which can silently discard failures.
- Files: `src/lecture_study/extraction/pipeline.py`, `src/lecture_study/evidence/pipeline.py`, `src/lecture_study/transcription/pipeline.py`, `src/lecture_study/quality/expert_corpus.py` (lines around 594, 625, 666, 708, 1168, 1187), `src/lecture_study/quality/human_review.py` (lines around 560, 617, 812, 837, 969), `src/lecture_study/quality/calibration_promotion.py` (lines around 2101, 2167, 2515, 2567, 2597). A repository-wide search finds roughly 38 lines matching `except Exception`, bare `except:`, or a trailing `pass`.
- Impact: Real defects (schema drift, bad inputs, programming errors) can be recorded as ordinary stage failures, which makes them look like content problems and slows diagnosis.
- Fix approach: Narrow each handler to the domain error types in `src/lecture_study/domain/errors.py`. Let programming errors propagate. Where a broad catch is intentional for a batch loop, log the exception with its traceback and keep the batch failure record.

**Unresolved TODO in the site catalog:**
- Issue: `pending_lectures` cannot attach discipline or topic context to pending entries because callers pass bare lecture ids.
- Files: `src/lecture_study/site/catalog.py` (line near 300)
- Impact: The site cannot show where unpublished lectures belong in the course structure.
- Fix approach: Source `expected_lecture_ids` from the discipline/topic tree in the course configuration (`config/targets.example.yaml` describes the shape), then carry that context into each pending entry.

**Duplicated dependency pins (resolved 2026-10-16):**
- Issue: `Dockerfile.site` installed pinned top-level versions directly with `pip`, so the container resolved transitive dependencies at build time, divergent from `uv.lock`.
- Resolution: `Dockerfile.site` now pins the base image by digest and installs from `infra/site-requirements.txt` (the closure of the five top-level packages with hashes, generated from `uv.lock` by `scripts/export_site_requirements.py`) with `--require-hashes`. A `uv.lock` bump reaches the image by regenerating that file.

**Stray print statements in library code:**
- Issue: A small number of `print(` calls remain under `src/lecture_study`, where structured logging is used elsewhere.
- Files: run `rg -n "print\(" src/lecture_study` to locate the three occurrences.
- Impact: Output bypasses the logging configuration and cannot be filtered in the site or in batch runs.
- Fix approach: Route them through the module logger, or move them to the CLI layer in `src/lecture_study/cli/main.py`.

## Known Bugs

No confirmed functional bugs were reproduced during this analysis. The items below are defects by inspection and should be verified with tests before any fix:

- Symptoms: Broad exception handlers can mark a stage as failed for a reason that is not a content problem, which makes the failure look like bad source material.
- Files: see the broad-exception list under Tech Debt.
- Trigger: A programming or schema error inside a batch loop.
- Workaround: Inspect the stored stage error text and the traceback if one was logged.

## Security Considerations

**Personal data at rest is unencrypted by application code:**
- Risk: Lecture transcripts, study materials, and review records contain personal data and are stored on disk without application-level encryption.
- Files: `docs/decisions/0010-local-pii-encryption-risk-acceptance.md`, `src/lecture_study/domain/human_review.py`, `src/lecture_study/quality/human_review.py`
- Current mitigation: The risk is formally accepted in ADR 0010, which relies on macOS FileVault for disk encryption. Public artifacts pass a denylist gate (`scripts/denylist_scan.py`, `.githooks/_denylist.sh`, and the CI step in `.github/workflows/ci.yml`).
- Recommendations: Revisit ADR 0010 if the data ever leaves a FileVault-protected machine, or if backups or sync are added. Confirm that the denylist covers any new output format before adding one.

**Single-process, in-memory abuse limits:**
- Risk: The password-check limiter and per-address attempt delays keep their state in process memory. A restart clears the counters, and a second worker would have its own counters.
- Files: `src/lecture_study/site/throttle.py` (`AttemptDelay`, `PasswordCheckLimiter`, `MAX_TRACKED_ACCOUNTS = 1024`), `src/lecture_study/site/app.py`, `src/lecture_study/site/sessions.py`
- Current mitigation: The deployment entrypoint `infra/docker-entrypoint.sh` starts a single uvicorn process with no `--workers` flag, and the Makefile does the same for local runs. Login and Basic Auth attempts are bounded by the limiter's concurrency and queue caps.
- Recommendations: Keep the single-worker constraint documented next to the entrypoint. If the site ever runs with multiple workers, move the limiter state to shared storage. Document what happens to attempt counters on restart.

**Signing secret read path:**
- Risk: The server signing secret is read from a file on each request path that needs it (`_read_secrets`, `signing_secret`). A malformed or missing secret file is handled by fail-closed behavior, but the read frequency adds filesystem load and makes a bad rotation visible only at request time.
- Files: `src/lecture_study/site/signing.py`, `src/lecture_study/site/app.py` (lines near 363, 374, 444)
- Current mitigation: `src/lecture_study/site/sessions.py` never touches the filesystem and fails closed when the allowlist is missing.
- Recommendations: Validate the secret once at startup and fail fast, then keep a re-read path only for deliberate rotation.

**Subprocess execution of external LLM and media tools:**
- Risk: Several workers launch external executables (`codex exec`, llama.cpp, ffmpeg, mlx_audio, the OCR and VLM workers) and build argument lists from pipeline data.
- Files: `scripts/generate_codex_podcast.py` (around line 268), `scripts/generate_codex_mock_exam.py`, `src/lecture_study/captioning/vlm_worker.py` (around line 241 to 270), `src/lecture_study/extraction/media_workers.py`, `src/lecture_study/extraction/ocr_supervisor.py`
- Current mitigation: Arguments are passed as lists (`create_subprocess_exec`, `subprocess.run` without `shell=True`), and error descriptions avoid echoing the full command.
- Recommendations: Keep `shell=True` out of these call sites. Validate lecture ids and file paths before they reach argument lists.

## Performance Bottlenecks

**Large single-pass computations over the lecture corpus:**
- Problem: Calibration, promotion, and expert-corpus steps load complete ledgers and corpora into memory and rescan them per stage.
- Files: `src/lecture_study/quality/calibration_promotion.py`, `src/lecture_study/quality/expert_corpus.py`, `src/lecture_study/evidence/chunker.py`
- Cause: The pipeline is designed for full-course batch runs, so each stage reads whole artifacts instead of streaming them.
- Improvement path: Measure first with a single-lecture run and a full-course run. If memory or time becomes a limit, index the SQLite stores on lecture id and stage, and iterate rows instead of building full lists.

**Serial LLM and transcription calls:**
- Problem: Transcription, OCR, and LLM drafting run as external processes, and the gain from parallel execution depends on the local machine.
- Files: `src/lecture_study/transcription/pipeline.py`, `src/lecture_study/extraction/ocr_supervisor.py`, `scripts/generate_codex_drafts.py`
- Cause: The Apple Silicon target (ADR 0001) and the MLX and llama.cpp runtimes compete for the same unified memory.
- Improvement path: Keep concurrency bounded per runtime. ADR 0023 records the parallel execution decision; extend it rather than adding ad hoc pools.

## Fragile Areas

**Calibration and promotion logic:**
- Files: `src/lecture_study/quality/calibration.py`, `src/lecture_study/quality/calibration_promotion.py`, `src/lecture_study/quality/calibration_promotion_sqlite.py`
- Why fragile: The file is very large, and promotion decisions depend on SQLite state written by an earlier stage. A change in one table shape breaks the promotion path without a type error.
- Safe modification: Change the SQL schema and the decision code in the same commit, and extend `tests/test_calibration_promotion_sqlite.py` for each new column.
- Test coverage: Unit tests exist (`tests/test_calibration_promotion.py`, `tests/test_calibration_promotion_sqlite.py`), but tests for the full promotion flow across real SQLite files are not evident.

**Versioned LLM response contracts:**
- Files: `src/lecture_study/ops/llm_response_contract.py`, `src/lecture_study/ops/llm_instructions.py`, `src/lecture_study/quality/prompt_budget.py`, `src/lecture_study/semantic/ledger_groundedness.py`
- Why fragile: Prompt text, JSON schema, and parser must agree. ADR 0022 and ADR 0034 record earlier workarounds for structured-output and repeat-penalty failures, so model behavior is a live dependency.
- Safe modification: Change the instruction, the schema, and the parser together, then run `tests/test_llm_response_contract.py` and `tests/test_generate_codex_semantic_ledgers.py`.
- Test coverage: Parser behavior is covered. Live model output is not covered, and no test checks prompt-length regression.

**Deployment-time ADR coupling:**
- Files: `infra/docker-entrypoint.sh`, `infra/uvicorn-logging.json`, `Dockerfile.site`, `docs/decisions/0044-public-extraction-and-site-access-hardening.md`
- Why fragile: The site hardening decisions depend on exact uvicorn logging and proxy behavior. A version bump can change either without a test failing.
- Safe modification: Re-run `tests/test_site_app.py` and `tests/test_site_security_headers.py` after any change to uvicorn or the image.

## Scaling Limits

**Single site process:**
- Current capacity: One uvicorn process, `MAX_CONCURRENT_CHECKS = 2` password checks at a time, `MAX_QUEUED_CHECKS = 8` queued, `MAX_WAITING_ATTEMPTS = 24` waiting attempts (`src/lecture_study/site/throttle.py`).
- Limit: Throughput is bounded by the PBKDF2 cost per check and by the single process. Sessions and counters are not shared across processes.
- Scaling path: Move throttle and session state to a shared store before adding workers or replicas. Keep the PBKDF2 cost budget in mind when tuning the concurrency caps.

**Local-only compute:**
- Current capacity: The pipeline runs on one Apple Silicon machine (ADR 0001).
- Limit: Long lecture batches are bounded by local CPU, GPU, and memory.
- Scaling path: Resumable stage manifests (`src/lecture_study/quality/stage_manifest.py`) already allow restarts. Keep stages idempotent so partial reruns stay safe.

## Dependencies at Risk

**Fast-moving runtime dependencies:**
- Risk: MLX, mlx_audio, Kokoro (ADR 0032), Whisper-class ASR, llama.cpp, and the `codex` CLI are all pre-1.0 or externally versioned tools whose flags and output change between releases (ADR 0020 and ADR 0038 record invocation corrections).
- Impact: A tool upgrade can break an invocation or change output format silently.
- Migration plan: Record the tested version of each external tool in `scripts/01_provision_engines.sh` and check it in `src/lecture_study/ops/model_attestation.py` before each run.

## Missing Critical Features

**Automated check for the progress dashboard:**
- Problem: `scripts/generate_progress_dashboard.py` has no test that imports or runs it. Other scripts have matching tests (for example `tests/test_generate_codex_drafts.py`).
- Blocks: Regressions in the dashboard output are found only by manual inspection.

## Test Coverage Gaps

**Untested script:**
- What's not tested: `scripts/generate_progress_dashboard.py`.
- Files: `scripts/generate_progress_dashboard.py`
- Risk: Dashboard output can break without any test failing.
- Priority: Low

**Calibration and visual observation SQLite stores:**
- What's not tested: End-to-end behavior across real SQLite files with migrations or schema changes, beyond the unit tests in `tests/test_calibration_promotion_sqlite.py` and `tests/test_visual_observation_sqlite.py`.
- Files: `src/lecture_study/quality/calibration_promotion_sqlite.py`, `src/lecture_study/quality/visual_observation_sqlite.py`
- Risk: A schema change can pass unit tests and fail on an existing database file.
- Priority: Medium

**Live model and tool behavior:**
- What's not tested: Output of live LLM, Whisper, OCR, and TTS runs. The tests use fakes for these workers.
- Files: `tests/test_transcription.py`, `tests/test_extraction.py`, `tests/test_tts.py`, `tests/test_generate_codex_podcast.py`
- Risk: Upstream model or tool changes reach the pipeline unnoticed until a real run.
- Priority: Medium

---

*Concerns audit: 2026-10-14*
