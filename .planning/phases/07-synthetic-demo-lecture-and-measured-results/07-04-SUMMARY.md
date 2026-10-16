---
phase: 07-synthetic-demo-lecture-and-measured-results
plan: 04
subsystem: demo
tags: [dry-run, site, signed-in-check, tls, access-material, stage-8, stage-9]
requires:
  - phase: 07-01
    provides: "lecture_study.demo package"
provides:
  - "Opt-in fixture dry run: stage 8, stage 9, study-guide script and site generator to a real signed-in page view"
  - "scripts/demo_site.py: SignedInView, lecture_page_path, check_signed_in_view, prepare-access, generate, check"
  - "lecture_study.demo.site_access: demo site paths, credentials line, secrets, loopback certificate command"
  - "demo/site/academic-calendar.yaml: versioned empty schedule"
affects: [07-06, 07-07, 07-08]
tech-stack:
  added: []
  patterns:
    - "pure logic in src/lecture_study/demo, file and process effects in scripts/demo_site.py"
    - "opt-in tool-dependent test gated by LECTURE_STUDY_DEMO_TOOLS=1 that fails, not skips, when a tool is missing"
key-files:
  created:
    - demo/site/academic-calendar.yaml
    - scripts/demo_site.py
    - src/lecture_study/demo/site_access.py
    - tests/demo/test_demo_dry_run.py
    - tests/demo/test_demo_site_access.py
    - tests/demo/conftest.py
  modified: []
key-decisions:
  - "The guide child process runs with the temporary root as cwd, because the script points Typst --root at cwd"
  - "The sign-in check disables redirect following on every request so the anonymous refusal is observable"
  - "The shared demo_site test fixture lives in tests/demo/conftest.py instead of a loader copy per test file"
patterns-established:
  - "Late-stage fixtures: one evidence pack with duration_exception, a 4-fact ledger, one draft older than its validated unit"
requirements-completed: [DEMO-02]
duration: 40 min
completed: 2026-10-15
status: complete
commits: 2
plan_head_before: dbaec70f1067f25841c09e1a09cbb35175b5a0ca
plan_head_after: 8ca6191319ad56e99532171e31f5260daa41eeb5
actuals:
  tokens: 8400
  tasks: 2
  commits: 2
---

# Phase 7 Plan 04: Fixture dry run to a signed-in view and demo site access Summary

Stages 8 and 9, the study-guide script and the site generator ran once on schema-valid fixtures for scratch lecture 925199 and ended in a real CSRF-protected sign-in over HTTPS; demo credentials, session secret and a two-day loopback certificate are now generated at run time under `workspace/demo/site-access/`.

## Tasks

| Task | Name | Commit |
| ---- | ---- | ------ |
| 1 | Tracer: dry run from fixtures through stage 8, stage 9, the guide and the site to a signed-in page view | 92eadca |
| 2 | Demo site access material and the prepare-access, generate and check commands | 8ca6191 |

The `commits: 2` value counts this plan's own commits. Plans 07-02, 07-03 and 07-05 committed concurrently on the same branch, so the raw `plan_head_before..HEAD` count is higher; the two hashes above are the only ones authored by this plan.

## What was built

- `tests/demo/test_demo_dry_run.py` (skipped unless `LECTURE_STUDY_DEMO_TOOLS=1`; once opted in, a non-macOS platform or a missing typst, say, ffmpeg or ffprobe fails the test). It builds a 20-second mp4 with ffmpeg lavfi sources, registers it as local lecture 925199 (publish true, remote LLM false), writes schema-valid fixtures (one evidence pack covering the whole video with the `lecture_shorter_than_minimum_unit` duration exception, a four-fact ledger spanning anatomy, diagnosis, treatment and alert categories, one non-degenerate draft and one validated unit with `review.passed` true whose draft mtime is ten seconds older), runs stage 8 and stage 9 with their directory constants monkeypatched into `tmp_path`, runs `scripts/generate_personal_study_guides.py` as a child without `--personal-study-inference` (no figure catalog is forged), runs `generate_site` with every directory inside `tmp_path` and the demo schedule, and finishes with `check_signed_in_view` through `TestClient(app, base_url="https://testserver")`.
- `scripts/demo_site.py`: `SignedInView.passed()` (anonymous not 200, login 303, page 200, notice present), `lecture_page_path`, `check_signed_in_view` (GET page anonymously, GET `/entrar`, parse the hidden `csrf_token`, POST credentials with `next`, GET page again; no Basic Auth, no forged cookie), `prepare_access`, `read_sign_in_values`, and the `prepare-access`, `generate` and `check` subcommands.
- `src/lecture_study/demo/site_access.py`: constants, `SiteAccessPaths`, `credentials_line`, `new_demo_password`, `new_session_secret`, `environment_file_text`, `certificate_command`, `require_inside`. Pure logic only; no `httpx` import under `src`.
- `demo/site/academic-calendar.yaml`: `entries: []`.

## First-run defects

None found. Stage 8 (`DocumentAssembler`), stage 9 (`MetadataAggregator`), the study-guide script (Typst PDFs, flashcards, temporal coverage, deterministic podcast through `say` voice Luciana and ffmpeg, hard-linked video, bundle validation) and the site generator all ran on the first attempt with the fixtures above. No module was changed and no regression test was added in stage 8, stage 9, the guide or the site.

## Deviations from Plan

**1. [Rule 3 - Blocking] Guide child process runs with the temporary root as cwd**
- **Found during:** Task 1
- **Issue:** the plan asked for the repository root as cwd, but `_compile_typst_atomically` passes `--root` equal to the working directory. With the staged sources under `tmp_path` (outside the repository), Typst refuses the source (exit 1), reproduced by switching cwd back to the repository root.
- **Fix:** the child runs with `cwd=tmp_path`; config and schedule paths are passed as absolute paths. This is a property of the test location only: the live run (07-06) stages under `workspace/` inside the repository, where the repository root remains the right cwd.
- **Files modified:** `tests/demo/test_demo_dry_run.py`
- **Commit:** 92eadca

**2. [Rule 2 - Correctness] Redirects disabled on every request of the sign-in check**
- **Found during:** Task 1 design
- **Issue:** `TestClient` follows redirects by default, which would turn a redirecting anonymous refusal into a 200 login form and break the "anonymous request is refused" criterion.
- **Fix:** `follow_redirects=False` on all four requests. The live app answers the anonymous request with 401.
- **Commit:** 92eadca

**3. [Rule 3 - Blocking] Shared test fixture for loading `scripts/demo_site.py`**
- **Issue:** both new test files needed the same by-path script loader.
- **Fix:** one `demo_site` fixture in `tests/demo/conftest.py` instead of two loader copies.
- **Commit:** 8ca6191

**4. [Rule 2 - Hardening] Certificate trust through an `ssl` context**
- **Issue:** httpx 0.28 deprecates passing a path string as `verify`.
- **Fix:** the `check` subcommand builds `ssl.create_default_context(cafile=certificate)`.
- **Commit:** 8ca6191

Otherwise the plan was executed as written.

## Verification

- `LECTURE_STUDY_DEMO_TOOLS=1 uv run pytest tests/demo/test_demo_dry_run.py -q -rs`: 1 passed (about 4 s). Without the variable: 1 skipped with the reason naming the variable and tools.
- `uv run pytest tests/demo/test_demo_site_access.py -q -rs`: 7 passed.
- `uv run pytest -q` (full suite): 2859 passed, 1 skipped (the opt-in dry run).
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean.
- `git check-ignore -q workspace/demo/site-access/.env`: ignored.
- Acceptance greps: no `config/site-credentials` in `scripts/demo_site.py` or `site_access.py`; no `httpx` under `src`; no `personal-study-inference` in the dry-run test.
- Live TLS smoke (scratch script, not committed): `prepare_access` into a temporary directory under `workspace/`, uvicorn on 127.0.0.1 with the generated certificate, `check_signed_in_view` through an `httpx.Client` trusting that certificate returned `anonymous_status=401 login_status=303 page_status=200 notice_present=True`; certificate and key 0o600, directory 0o700. The temporary directory was removed.

## Hand-off for 07-06 and later

- Live server: bind 127.0.0.1 only; use `workspace/demo/site-access/cert.pem` and `key.pem`, `DEMO_SITE_URL` is `https://127.0.0.1:8443`.
- Environment for the app: `LECTURE_STUDY_SITE_CREDENTIALS=workspace/demo/site-access/site-credentials.txt`, `LECTURE_STUDY_SITE_SESSION_KEY=workspace/demo/site-access/site-session-key`; sign-in values are in the `.env` of that directory.
- The certificate lives two days; rerun `prepare-access` (which regenerates every file) when it lapses.
- `check` resolves the page path from `workspace/study-guides` and requires the lecture to be published there.

## Known Stubs

None.

## Threat Flags

None beyond the plan's register. T-07-11 (files 0o600, directory 0o700, password only in `.env`, ignore rules verified), T-07-12 (real CSRF POST flow), T-07-13 (argument list, trusted executable, bounded process) and T-07-14 (every dry-run root inside `tmp_path`) are implemented as planned.

## Self-Check: PASSED
