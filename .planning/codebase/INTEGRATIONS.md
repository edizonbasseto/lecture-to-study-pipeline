---
last_mapped_commit: 98206c18263d7561530c2e9ff7700e155c18d661
last_mapped_at: 2026-10-14
---
# External Integrations

**Analysis Date:** 2026-10-14

## APIs & External Services

**LLM provider (via Codex CLI):**
- OpenAI Codex CLI (`codex`) - all LLM-backed generation: semantic ledger extraction, learning-unit drafts, critic review and repair loop, personal study flashcards, mock exams, podcast scripts
  - Entry points: `scripts/generate_codex_semantic_ledgers.py`, `scripts/generate_codex_drafts.py`, `scripts/generate_codex_critic_review.py`, `scripts/generate_codex_personal_study_flashcards.py`, `scripts/generate_codex_mock_exam.py`, `scripts/generate_codex_podcast.py`
  - Orchestration: `src/lecture_study/cli/orchestrator.py` launches these as child processes (ADR 0038)
  - Auth: managed by the CLI under `~/.codex`; the repository holds no API key and `.env.example` states this explicitly
  - The repository does not import an LLM SDK; no `openai`, `anthropic`, or `google` client package is declared in `pyproject.toml`

**Local model runtimes (no network at run time):**
- whisper.cpp `whisper-cli` - speech-to-text for lecture audio; `src/lecture_study/transcription/whisper_worker.py`
- llama.cpp `llama-mtmd-cli` - vision captioning of slide figures using the Qwen3-VL-8B model pair recorded in the actor inventory; `src/lecture_study/captioning/vlm_worker.py`
- Apple Vision (via pyobjc) - OCR of slide images; `src/lecture_study/extraction/ocr_worker.py`
- MLX Audio with Kokoro-82M - podcast speech synthesis; `src/lecture_study/tts/generator.py`
- Hugging Face model hub - source of model weights, fetched during provisioning (`scripts/01_provision_engines.sh`, `huggingface-cli`)

**Source-control hosting:**
- GitHub - hosts the repository; `.github/workflows/ci.yml` uses `actions/checkout`, `astral-sh/setup-uv`, and `gitleaks/gitleaks-action` (all pinned by commit SHA)
- Native engine sources cloned from `https://github.com/ggerganov/whisper.cpp.git` and `https://github.com/ggerganov/llama.cpp.git` at pinned commits (`scripts/01_provision_engines.sh`)

**Source-material systems:**
- Lecture source material is read from local directories (`src/lecture_study/source_content/filesystem.py`, `src/lecture_study/study_materials/local_lectures.py`). The academic portal catalog is described in `config/targets.example.yaml` with `lms.example.test` placeholder URLs; the pipeline does not call the portal over the network.

## Data Storage

**Databases:**
- SQLite (standard library `sqlite3`) - local stores for quality and calibration state
  - `src/lecture_study/quality/expert_corpus_sqlite.py`
  - `src/lecture_study/quality/calibration_promotion_sqlite.py`
  - `src/lecture_study/quality/visual_observation_sqlite.py`
  - `src/lecture_study/quality/human_review_quarantine.py`
  - Connection: file paths under `workspace/`; no server, no connection string
  - Client: `sqlite3` directly, no ORM

**File Storage:**
- Local filesystem only. Runtime artifacts (raw media, intermediate outputs, `queue.json`, evidence packs, drafts, audio) live under `workspace/` (git-ignored). Paths are defined in `src/lecture_study/ops/` and `src/lecture_study/cli/`.
- Site container reads mounted volumes: study-guides corpus, video library (`VIDEO_LIBRARY_DIR` in `src/lecture_study/site/generator.py`), generated site output (`SITE_OUTPUT_DIR`)

**Caching:**
- None detected as a separate service. The site sets cache-related headers in `src/lecture_study/site/app.py` to avoid shared-cache leakage between authenticated viewers (comments near line 116 and line 225).

## Authentication & Identity

**Study site (`src/lecture_study/site/`):**
- Custom authentication; no third-party identity provider
  - Login with email and password. Credentials file path from `LECTURE_STUDY_SITE_CREDENTIALS` (default `config/site-credentials.txt`), parsed in `src/lecture_study/site/access.py`
  - Passwords stored as `pbkdf2_sha256` digests with a salt and iteration count (`src/lecture_study/site/access.py`, `hashlib.pbkdf2_hmac`); compared with `hmac.compare_digest`
  - Session cookie: HMAC-SHA256 signed, `__Host-` prefixed, implemented in `src/lecture_study/site/sessions.py`; the session key comes from `LECTURE_STUDY_SITE_SESSION_KEY` (default `config/session-key/site-session-key`)
  - Cookie signing secret read by `src/lecture_study/site/signing.py` (`SIGNING_SECRET_PATH`); a forged cookie needs both the session key and the signing secret
  - Login anti-CSRF nonce cookie: `src/lecture_study/site/login_csrf.py`
  - Login throttling: `src/lecture_study/site/throttle.py`
  - HTTP Basic Auth accepted as a second entry path through the same authorization check (`src/lecture_study/site/app.py`, the function handling "Both doors")
  - Email allowlist enforced before static files are served (`src/lecture_study/site/access.py`, `src/lecture_study/site/app.py`)
- Response hardening: CSP with hashed inline scripts and security headers in `src/lecture_study/site/security_headers.py`

**Quality certificates:**
- HMAC-SHA256 signing of quality-gate certificates with `LECTURE_STUDY_QUALITY_HMAC_KEY_B64` (ADR 0026), `src/lecture_study/quality/trust.py`; the key is read from the process environment, or from `.env` in the working directory only

**Denylist and secret protection:**
- Private denylist path from `LECTURE_STUDY_DENYLIST`; the list is kept outside the repository and is supplied as the `DENYLIST` GitHub repository secret in CI

## Monitoring & Observability

**Error Tracking:**
- None detected (no Sentry or similar SDK in `pyproject.toml`)

**Logs:**
- Python `logging` through Uvicorn; `infra/uvicorn-logging.json` is applied in the site container so authentication records reach container stdout (`infra/docker-entrypoint.sh`)
- CLI progress output via `rich` (`src/lecture_study/cli/`)
- Per-run failure logs under `tmp/codex_phase_failures` (`src/lecture_study/cli/orchestrator.py`)

## CI/CD & Deployment

**Hosting:**
- Study site: container image built from `Dockerfile.site`; no registry or hosting target is defined in the repository
- Pipeline: local macOS execution; no deployment automation

**CI Pipeline:**
- GitHub Actions, `.github/workflows/ci.yml`
  - `secret-scan` job (ubuntu-24.04): `gitleaks` over the repository, and `scripts/denylist_scan.py` over the full git history using the `DENYLIST` secret
  - `test` job (macos-15): installs `ffmpeg typst poppler gitleaks` via Homebrew, runs `uv sync --group dev --locked`, `ruff check`, `ruff format --check`, `mypy src`, and `pytest -q -rs`
- Triggers: push to `main`, and pull requests

**Local git hooks:**
- `.githooks/pre-commit` blocks denylisted terms and secrets in staged content; `.githooks/commit-msg`, `.githooks/pre-merge-commit`, `.githooks/pre-push` installed by `make hooks`

## Environment Configuration

**Required env vars (for full pipeline):**
- `LECTURE_STUDY_QUALITY_HMAC_KEY_B64` - required for quality certification
- `WHISPER_MODEL_PATH` (Makefile variable, not an env var) - path to the Whisper model

**Optional env vars (defaults shown in `.env.example` and code):**
- `LECTURE_STUDY_SITE_CREDENTIALS`, `LECTURE_STUDY_SITE_SESSION_KEY`, `LECTURE_STUDY_SITE_MOCK_EXAMS`, `LECTURE_STUDY_SITE_ACADEMIC_SCHEDULE`
- `LECTURE_STUDY_DENYLIST`

**Secrets location:**
- `.env` in the repository root (git-ignored; read only for the quality HMAC key)
- `config/site-credentials.txt`, `config/session-key/site-session-key`, and the signing-secret file referenced by `src/lecture_study/site/signing.py`. These paths are configured to be mounted in the site container and must be readable by uid 10001 (`Dockerfile.site`)
- `~/.codex` - Codex CLI credentials, managed by the CLI
- GitHub Actions secrets: `GITHUB_TOKEN` (gitleaks), `DENYLIST`

## Webhooks & Callbacks

**Incoming:**
- None. The site exposes HTTP routes for login, logout, landing, quiz, mock exams, and static content, not webhooks (`src/lecture_study/site/app.py`).

**Outgoing:**
- None detected. The code contains no outbound HTTP client calls at run time; `urllib.parse` is used only for URL parsing in the site.

---

*Integration audit: 2026-10-14*
