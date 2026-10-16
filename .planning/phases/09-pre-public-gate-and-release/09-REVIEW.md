---
phase: 09-pre-public-gate-and-release
reviewed: 2026-10-16T12:00:00Z
depth: deep
files_reviewed: 470
files_reviewed_list:
  - "all 470 tracked files at HEAD 78e255e (git ls-files); see Scope and method"
findings:
  critical: 2
  warning: 6
  info: 7
  total: 15
status: issues_found
---

# Phase 9: Code Review Report (PUBG-02, whole repository, pre-public)

**Reviewed:** 2026-10-16
**Depth:** deep (publication-oriented sweep of every tracked file, plus line-level review of the study-site access path, git hooks, CI, container, provisioning script and LLM egress factory)
**Files Reviewed:** 470 tracked files (HEAD 78e255e, branch main)
**Status:** issues_found

Severity mapping used in this report (caller scale to the GSD tiers): blocker = none found; High -> `CR-` (critical tier); Medium -> `WR-` (warning tier); Low -> `IN-` (info tier). Each finding states its caller-scale severity.

## Summary

Publication-blocking content leaks: none found. The repository content is clean of secrets, personal paths, private hostnames, patient data and real identifiers, in the tracked tree and in all 148 commits. Two High findings remain (an unauthenticated unhandled-exception path in the site and personal e-mail address in every commit header), plus six Medium and seven Low items. Neither High finding is a content leak of secrets or patient data; the first is a code defect, the second is a decision the operator has to take explicitly.

The repository on GitHub is currently `private` (confirmed with `gh api`), so nothing here has been exposed yet.

### Scope and method

- Scope: all 470 files from `git ls-files` (src 140 modules, scripts 20, tests, docs, `.planning`, CI, Makefile, `Dockerfile.site`, `infra/`, `.githooks/`, `config/`, `demo/`). No structural-findings block and no external-reviewer evidence were provided.
- Mechanical leak scans, all run against the real private denylist without printing any term:
  - `scripts/denylist_scan.py` over every tracked file individually: no match.
  - `scripts/denylist_scan.py` over the full history (`git log -p --all --diff-merges=separate`, author, committer and message fields) and over all refs/tags: no match.
  - `gitleaks git --log-opts=--all` (148 commits, 6.5 MB): no leaks.
  - Pattern sweeps of tracked files for `/Users/`, `/home/`, `~/`, e-mail addresses, IPv4 addresses and internal hostnames, URLs, CPF and phone formats, key/token shapes, `password=`-style assignments, `shell=True`, `eval`, `pickle`, `yaml.load`, `verify=False`: only synthetic or reserved values (`example.com`, `example.test`, `*.invalid`, `127.0.0.1`, test fixtures).
- Fresh-clone check: `git clone` of HEAD into a scratch directory with an empty `HOME`, `uv sync --group dev --locked --offline`, then `pytest`: 2988 passed, 1 skipped (the macOS toolchain dry run). `ruff check`, `ruff format --check`, `mypy src scripts/check_documentation.py` and `scripts/check_documentation.py` all pass on the working tree. Tests therefore do not depend on gitignored local state or on the denylist.
- CI supply chain verified online: gitleaks 8.30.1 `linux_x64` SHA-256 in `ci.yml` equals the digest published for the release asset; the `actions/checkout` and `astral-sh/setup-uv` SHAs resolve to real tags (`v4` and `v6.8.0`).
- Not done (stated so the gap is not read as a pass): no dependency CVE audit was run; the very large quality modules (`quality/calibration_promotion.py`, `quality/delivery.py`, about 3,500 and 2,800 lines) were scanned by pattern but not read line by line; ADR 0023 (about 2,000 lines) and the `.planning/phases/06` to `08` artifacts were pattern-scanned and sampled, not read in full.

## Critical Issues

### CR-01: Unauthenticated request with a crafted cookie or Authorization header raises an unhandled exception (HTTP 500) in the site access gate

**Severity (caller scale):** High
**File:** `src/lecture_study/site/sessions.py:87`, `src/lecture_study/site/app.py:896`, `src/lecture_study/site/app.py:558-563`
**Issue:** Two parsers in the sign-in path accept input they then fail to convert, and `admit_authorized_person` catches only `CredentialsError` and `TooManyAttempts`. Both were reproduced against the real app with Starlette's `TestClient` (valid credentials file and session key, no authentication presented):

1. `sessions._parse_token` guards the expiry with `str.isdigit()` and then calls `int()`. `isdigit()` is true for superscript digits and for any run of ASCII digits longer than 4,300 characters, and `int()` raises `ValueError` for both (CPython 3.12 integer-string limit). A cookie `__Host-pos_session=<base64 of any text>.<5000 ASCII digits>.x` returns HTTP 500 (observed). A single non-ASCII digit such as `²` does the same at the function level.
2. `app._parse_basic_auth` catches `binascii.Error` and `UnicodeDecodeError`, but `base64.b64decode` on a `str` containing non-ASCII characters raises a plain `ValueError`. `Authorization: Basic \xe9\xe9` (bytes in the header, legal obs-text for HTTP/1.1 parsers) returns HTTP 500 (observed).

Impact: no authentication bypass (the request still gets no content, and the outer `SecurityHeadersMiddleware` still adds headers), but any anonymous client can trigger an unhandled exception and a stack trace in the logs on the one endpoint the repository exists to expose, on a project whose ADR 0044 advertises "fail closed, fail loudly" hardening. No test covers either input. The same `isdigit()`/`int()` pattern is used for `Content-Length` at `app.py:881-882`; it is shielded in practice by the ASGI server's own header validation, but it should be fixed with the others.
**Fix:**
```python
# sessions.py::_parse_token
if email is None or not (expiry_text.isascii() and expiry_text.isdigit() and len(expiry_text) <= 12):
    return None

# app.py::_parse_basic_auth
try:
    decoded = base64.b64decode(header.removeprefix("Basic "), validate=True).decode("utf-8")
except (ValueError, UnicodeDecodeError):   # binascii.Error is a ValueError
    return None

# app.py::_read_bounded_body
if declared_length is not None and declared_length.isascii() and declared_length.isdigit():
```
Add regression tests to `tests/test_site_sessions.py` and `tests/test_site_app.py` for: oversized digit run, non-ASCII digit, non-ASCII Basic payload, each expecting 401 (or a redirect to the login form for HTML clients).

### CR-02: Every one of the 148 commits carries the author's personal e-mail address, and no gate covers it

**Severity (caller scale):** High (decision gate; becomes a blocker unless the operator explicitly accepts it)
**File:** git history (`git log --all --format='%an|%ae|%cn|%ce'` yields a single identity: `Edizon Basseto|<operator-personal-email>` for author and committer); `LICENSE:3`
**Issue:** The tracked tree contains no e-mail address of the operator (verified by grep), but making the repository public publishes the personal address in all 148 commit headers, permanently and harvestable. The leak gate does not catch it: the denylist scan of authors, committers and messages returned no match for the history, and gitleaks does not flag addresses. `LICENSE` and `.planning/phases/06-pluggable-llm-gateway/06-SECURITY.md:124-128`, `07-VERIFICATION.md:55` also carry the operator's full name. The name in `LICENSE` is normal for an MIT repository; the e-mail address is the open question. The global rule R0.1 treats PII in commits as prohibited.
**Fix:** The operator must decide before the first push; this cannot be settled by the reviewer. Options: (a) accept the exposure explicitly and record it in the Phase 9 plan; (b) rewrite history to the GitHub no-reply address before the first public push (`git filter-repo --mailmap` with a mailmap mapping the address to `<id>+<user>@users.noreply.github.com`, then re-run the three leak layers on the rewritten history and force-push only to the new public remote); (c) publish a squashed single-commit history. If (b) or (c), rerun the full-history denylist and gitleaks scans afterwards, because a rewrite changes every hash that `.planning` and the results report cite.

## Warnings

### WR-01: GPL-3.0 dependencies under an MIT-licensed repository with no third-party notice

**Severity (caller scale):** Medium
**File:** `pyproject.toml:4,10-24`, `LICENSE`, `README.md:310-312`
**Issue:** `pyproject.toml` declares `license = "MIT"` and the README says "MIT, see LICENSE". Two direct runtime dependencies are copyleft: `phonemizer-fork` (installed metadata: `GNU General Public License v3 or later`) and `espeakng-loader`, which vendors the `espeak-ng` library (GPL-3.0) inside its wheel (ADR 0032 lines 80-83 record the vendoring). The repository does not redistribute either, so the MIT claim for the repository's own code is not wrong, but anyone who ships an environment, image or bundle containing the TTS stack must comply with GPL-3.0, and the README gives no hint. Models (Whisper large-v3-turbo, Kokoro-82M, Qwen2.5-14B, license recorded only for Qwen in ADR 0046) are downloaded, not redistributed, but their licenses are likewise undocumented for the first two.
**Fix:** Add a short "Third-party software and models" section to the README (or a `THIRD_PARTY.md`) listing the GPL-3.0 packages, the model licenses (Whisper, Kokoro, Qwen2.5) and the statement that they are installed or downloaded by the operator and not redistributed. Confirm the Kokoro and Whisper license text once and cite it.

### WR-02: `.planning/` is published wholesale and is stale, inconsistent and personal in places

**Severity (caller scale):** Medium
**File:** `.planning/STATE.md:4-8,26-33`, `.planning/PROJECT.md:9`, `.planning/phases/06-*/*-PLAN.md` (`@~/.claude-google/gsd-core/...` lines, for example `06-01-PLAN.md:71-72`), `.planning/phases/08-portfolio-readme-and-documentation/08-02-PLAN.md`, `.planning/phases/08-portfolio-readme-and-documentation/08-REVIEW.md`
**Issue:** The planning tree is tracked and would be published with the code. No secrets or host paths are in it, but:
- `STATE.md` contradicts itself: frontmatter says phase 9 is current, while the body says "Current focus: Phase 7", "Next action: `/gsd-plan-phase 6`" and "5 of 9" phases complete. A reader of a portfolio repository sees an unreliable status file.
- `PROJECT.md:9` states the repository is "a public portfolio artifact for an AI-engineering job search". That is the operator's personal intent; confirm it is meant to be public.
- The plan files reference the operator's tool installation (`~/.claude-google/gsd-core/...`), and two files have broken relative links (checked with a link walker over all 134 tracked Markdown files: `08-02-PLAN.md` to `decisions/0045-...md`, `08-REVIEW.md` to `README.md`).
- `.planning/codebase/CONCERNS.md` documents unresolved weaknesses (oversized modules, broad `except Exception`, signing-secret re-read per request). That is honest, but it is a security-relevant to-do list for a public audience.
**Fix:** Decide explicitly whether `.planning/` ships. If it does, refresh `STATE.md`, repair the two links, and review `PROJECT.md:9` and `CONCERNS.md` for wording the operator is willing to publish. If it does not, remove it from the public tree (history still needs the same scrutiny, since the earlier commits contain it) or publish from a squashed export.

### WR-03: CI denylist job fails by design for any contributor without the secret, and exposes the secret to branch code

**Severity (caller scale):** Medium
**File:** `.github/workflows/ci.yml:44-66`
**Issue:** `denylist-scan` fails closed when `secrets.DENYLIST` is empty. ADR 0044 records this for fork pull requests ("fails ... by design"), but the consequence for a public repository is that every external pull request shows a red check that cannot be fixed by the contributor, and the first push to a new public repository is red until the secret is created. Separately, on same-repository branches the job runs the workflow file and `scripts/denylist_scan.py` from the pull-request branch while the secret is in scope; anyone with push access to the repository could change either file to print the secret. For a single-owner repository that is acceptable, but it should be a conscious statement.
**Fix:** Create the `DENYLIST` repository secret before the first push. Document in the README/CONTRIBUTING that the denylist check is maintainer-only, and either gate the job with `if: github.event.pull_request.head.repo.fork != true` plus a separate, clearly named required check for maintainers, or leave as is and state it. Keep branch protection so only the owner can push branches.

### WR-04: "No real lecture data" claim versus test fixtures and ADR text derived from real lectures

**Severity (caller scale):** Medium
**File:** `README.md:5,280`, `docs/decisions/0044-public-extraction-and-site-access-hardening.md:54-57`, `docs/decisions/0023-content-completeness-quality-gates-and-parallel-execution.md:2008-2037`, `tests/test_ledger_groundedness.py:79-106`, `tests/test_draft_depth_report.py:70-90`
**Issue:** The README states "Synthetic data only: the repository holds the demo lecture and no real lecture data" and ADR 0044 section 2 states that no real lecture identifier appears. Evidence in the tree points the other way in small ways:
- `tests/test_ledger_groundedness.py` carries a timestamped classroom dialogue (colloquial speech such as "tá?", "Peguem um nylon dois zeros", with second-resolution timestamps) described as "synthetic dialogue modeled on an observed defect". `tests/test_draft_depth_report.py:72-73` says the draft "mirrors the shape of the real ~90-word draft the research traced for lecture 10010".
- ADR 0023 "Operational diagnostic evidence" reports measurements from "a real lecture" (1,952 transcript segments, 92 frames, a 235.68 second podcast).
No personal data and no obvious verbatim passage was found, so this is a provenance and claim-accuracy issue, not a leak. But if any fixture line is a verbatim excerpt of a recorded third-party lecture, the claim is false and the instructor's speech is republished without permission.
**Fix:** Confirm with the operator that every fixture utterance is an original paraphrase. Reword README line 280 to "no real lecture recordings, transcripts or media" (or equivalent) and add a sentence in ADR 0023 that the figures are aggregate counts from a private run. Remove the phrase "real ... lecture 10010" from the docstring or rephrase it as a synthetic construction.

### WR-05: Unpinned, drifting site container; plain-HTTP bind with Secure-only cookies; no health check

**Severity (caller scale):** Medium
**File:** `Dockerfile.site:21-27,50-53`, `infra/docker-entrypoint.sh:30-32`, `Makefile:43-44`
**Issue:** The image is built `FROM python:3.12-slim` (a moving tag, no digest) and installs five packages with `pip` by version but without hashes, as a hand-maintained list that duplicates `uv.lock` (acknowledged in `.planning/codebase/CONCERNS.md`). It listens on `0.0.0.0:8000` over plain HTTP, and the session and CSRF cookies are `Secure` with the `__Host-` prefix (ADR 0044), so the site only works behind a TLS-terminating proxy; neither the README nor `docs/operations.md` says so, and the README does not describe building or running the image at all (`Dockerfile.site` is referenced only in the repository layout list and in `operations.md`). There is no `HEALTHCHECK` although the app exposes an unauthenticated health route. `make site` starts `uvicorn --reload` on plain HTTP, where Safari refuses the `Secure` cookie and sign-in appears broken.
**Fix:** Pin the base image by digest, generate the pip requirements from `uv.lock` (`uv export --no-dev --frozen --hash`) and install with `--require-hashes`, add a `HEALTHCHECK` against the health route, and add a short "Deploying the site" paragraph: HTTP inside the container, TLS must terminate in front, single worker. Mark `make site` as development-only or point it at the TLS demo recipe.

### WR-06: Example gateway configuration defaults to the remote adapter and the quick start copies it unchanged

**Severity (caller scale):** Medium (mitigated by a fail-closed per-lecture flag, so not High)
**File:** `config/llm-gateway.example.yaml:21`, `README.md:159-162`
**Issue:** `default_adapter: codex` makes the remote provider the default for every stage, and the quick start tells the reader to `cp config/llm-gateway.example.yaml config/llm-gateway.local.yaml`. The code refuses the remote adapter for any lecture not flagged `remote_llm_allowed` (verified in `llm/factory.py:36-45`, refusal before the executable is resolved), so there is no silent egress. The default is nevertheless the wrong direction for a repository whose stated stance is local-first egress: a new user who flags a lecture to try the remote adapter, or who reads the example as the recommended setting, sends content to a third party by default. The same file also ships the full relative path of a ~9 GB model that the README says is optional.
**Fix:** Change the example default to `llama-cpp` (the demo already passes the adapter explicitly) or add a one-line README warning next to the `cp` command that the example selects the remote adapter and that the per-lecture flag is the only guard.

## Info

### IN-01: `.gitignore` relies on the operator's global ignore for `.claude/`

**Severity (caller scale):** Low
**File:** `.gitignore` (no `.claude/` entry); the local `.claude/settings.local.json` is ignored only by `~/.config/git/ignore` (`**/.claude/settings.local.json`)
**Issue:** On any other machine, or after a clone into a different user account, `git add -A` stages `.claude/settings.local.json`, which can carry absolute paths and permission rules. The media-extension list also omits formats the code handles (`.webm`, `.m4v`, `.aac`, `.ogg`, `.flac`, `.csv`), relying on `workspace/` being ignored.
**Fix:** Add `.claude/` and the missing media extensions to `.gitignore`.

### IN-02: Three local `gsd/phase-*` branches exist

**Severity (caller scale):** Low
**File:** git refs `gsd/phase-06-pluggable-llm-gateway`, `gsd/phase-07-synthetic-demo`, `gsd/phase-08-portfolio-readme`
**Issue:** All three are fully merged into `main` (0 commits ahead each), so they add nothing but would be pushed by a `git push --all` and show as stale branches on a public page.
**Fix:** Push only `main` (`git push -u origin main`) or delete the three branches first.

### IN-03: Provisioning script installs a tool it never uses and omits a model the code needs

**Severity (caller scale):** Low
**File:** `scripts/01_provision_engines.sh:63-67,9-10`, `README.md:156,166`
**Issue:** `install_system_dependencies` installs `huggingface-cli` via Homebrew, but every download in the script uses `curl` or whisper.cpp's own downloader; the install is an unneeded dependency and its formula name is not verified here. The header comment notes that the Qwen3-VL-8B vision-captioning model "was provisioned outside this script", so a clean machine following the README can run the demo but cannot use the captioning stage, and the README does not say that.
**Fix:** Drop the `huggingface-cli` install (or use it), and add one sentence to the README that figure captioning needs a separately provisioned vision model and is outside the demo path.

### IN-04: Branding and a dangling TODO are hard-coded in public code

**Severity (caller scale):** Low
**File:** `src/lecture_study/site/app.py:197-209,310,548`, `src/lecture_study/site/quiz.py:238,289`, `src/lecture_study/site/sessions.py:38`, `src/lecture_study/site/catalog.py:300`
**Issue:** The site's titles, Open Graph metadata and cookie names embed the private programme ("Pós em Veterinária", `pos_` prefix). It is not sensitive, but it contradicts the README claim of a general pipeline and leaks the operator's context. `catalog.py:300` has `TODO(brief §7 item 2)`, referring to a document that is not in the repository.
**Fix:** Move the site title to a constant or setting with a neutral default; drop or rewrite the TODO so it points at a tracked document.

### IN-05: CI hygiene

**Severity (caller scale):** Low
**File:** `.github/workflows/ci.yml:23,40,70-72`
**Issue:** Pinned action SHAs are correct but lag the current majors (`checkout` v4 against v7, `setup-uv` v6 against v10); no job sets `timeout-minutes` (default 360 minutes on a public repository); `setup-uv` does not pin the uv version; there is no Dependabot configuration, so the SHA pins will never be refreshed.
**Fix:** Add `timeout-minutes` to each job, pin the uv version, and add `.github/dependabot.yml` for `github-actions` and `pip`/`uv`.

### IN-06: No `SECURITY.md` or contribution policy for a repository that ships an authentication layer

**Severity (caller scale):** Low
**File:** repository root (absent)
**Issue:** A public repository with a login, session and CSRF implementation has no `SECURITY.md`, `CONTRIBUTING.md` or issue template, and WR-03 means external contributions cannot pass CI.
**Fix:** Add a minimal `SECURITY.md` (private report channel, supported scope: demo and site code only) and a short `CONTRIBUTING.md` stating the maintainer-only denylist check.

### IN-07: Documented design trade-off lets an anonymous flood deny password sign-ins

**Severity (caller scale):** Low
**File:** `src/lecture_study/site/throttle.py:44-47`, `src/lecture_study/site/app.py:388-431`
**Issue:** At most 2 password checks run concurrently with 8 queued, and the global waiting room holds 24 attempts. An unauthenticated client can keep those slots full, so legitimate password logins get HTTP 429 while the flood lasts (cookie sessions are unaffected). ADR 0039 accepts this explicitly as load shedding, and there is no lockout by design, so this is noted for completeness rather than as a defect.
**Fix:** None required. Mention the behavior in `docs/operations.md` next to the single-worker note, since an operator will otherwise misread 429s during a flood as a bug.

## Claims in README and docs checked

- Stage map, ADR numbering gap (0017 to 0019), "superseded by ADR 0038" for ADRs 0005, 0006, 0007, 0013, 0014, and ADR 0047 decision numbers: accurate.
- `make docs-check`: runs and passes (21 command lines, 0 findings); its stated limits match its behavior.
- Measured-results figures in the README match `docs/results/demo-results.md` (243.6 s, 620 words, 2.3%/0.5%, 100.0% and 87.5% pass rates, recovery counts).
- "Container image copies only the site package and the domain models": true (`Dockerfile.site:24-26`).
- "Remote adapter refused before the executable is resolved": true (`llm/factory.py:36-45`).
- The README states the CI `test` job additionally installs `poppler` and `gitleaks`: true (`ci.yml:73`).
- `Walkthrough verification` section reports what was and was not executed; consistent with the Phase 8 evidence. The two demo steps it says were not re-executed (`make demo-video`, provisioning) were also not re-run in this review.

---

_Reviewed: 2026-10-16_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_


## Re-review of corrected diff

**Reviewed:** 2026-10-16
**Scope:** `git diff main..HEAD` on branch `gsd/phase-09-pre-public-gate` (4 commits: `9329b57`, `f38fef0`, `a83f410`, `df1f150`), 8 files: `src/lecture_study/site/app.py`, `sessions.py`, `quiz.py`, `tests/test_site_app.py`, `tests/test_site_quiz.py`, `README.md`, `docs/operations.md`, `.gitignore`.
**Depth:** deep on the access path (call chains of `identify`, `_parse_basic_auth`, `_parse_token`, `_read_bounded_body`, `parse_submitted_answers`), standard on the documentation.
**Verdict:** CR-01 is fixed. No authentication regression found. Two Warnings and three Info items are new; none blocks the fix.

### Checks executed (evidence)

- Old source versus new tests: `git archive main` into a scratch directory with the new tests copied over it, `pytest -k "Malformed or ascii"`: 4 tests fail on the old source (Basic `\xe9\xe9`, the 5,000-digit expiry cookie, the 5,000-digit `Content-Length`, and the quiz non-ASCII index). The same tests pass on HEAD. The regression tests therefore do detect the original crash for those four inputs.
- HEAD: `tests/test_site_app.py`, `tests/test_site_quiz.py`, `tests/test_site_sessions.py` 168 passed; full suite 3001 passed, 1 skipped; `ruff check`, `ruff format --check`, `mypy src scripts/check_documentation.py` clean; `scripts/check_documentation.py` 0 findings.
- Direct probe of the parsers on HEAD (no network, no server): `_parse_token` returns `None` for superscript, Arabic-Indic digit, 5,000-digit, empty and extra-segment tokens; `_parse_basic_auth` returns `None` for non-ASCII payload, bad padding, `Basic`, `Basic =` and lower-case `basic`; `_read_bounded_body` still returns `None` for a 10,000-byte body under every malformed `Content-Length` tried (`abc`, byte `\xb2`, 5,000 digits, `-5`, leading space) and still returns the 10-byte body for a small one.

### Fix verification

- **Sessions (`sessions.py:84-104`)**: `_parse_expiry` requires `isascii() and isdigit()` and wraps `int()`. Every non-conforming expiry returns `None`, so `_parse_token` returns `None`, so `verify` returns `None`, so the gate denies (401 or the login redirect). Malformed input stays unauthorized. The signature is still computed over the integer value and compared in constant time, unchanged. A very long ASCII digit string below the CPython limit still parses and then fails the signature check; a longer one is refused by the `ValueError` guard. No path turns a parse failure into success.
- **Basic auth (`app.py:903-916`)**: `except ValueError` is correct and sufficient: `binascii.Error` and `UnicodeDecodeError` both subclass `ValueError`, and so does the error `b64decode` raises for non-ASCII `str`. The `binascii` import was removed (no residual use; ruff clean). Acceptance set is unchanged (non-strict base64 as before), so no previously valid credential is rejected and no previously rejected one is accepted.
- **Content-Length (`app.py:874-899`)**: a malformed header now declares nothing and the streamed count enforces the bound (probe above). This is a deliberate relaxation of the pre-check, not of the limit; the limit is still applied before the body is parsed.
- **Quiz (`quiz.py:200-211`)**: same pattern, equivalent; a non-ASCII or oversize index is ignored, never graded.
- Side effect that tightens security: the old code accepted a non-ASCII Unicode digit expiry (for example Arabic-Indic) as a valid integer; a token written that way would have verified, because the signature covers the integer value. The new code rejects it. Leading zeros (`0012`) are still accepted and map to the same integer; that is token malleability, not forgery, and unchanged.
- Other `int()`/`isdigit()` sites in `src/lecture_study/site` were swept: `access.py:87` parses the stored hash inside `except (ValueError, TypeError)`; `sessions.py:45` and `generator.py:340` take trusted input. None remain unguarded.

### Documentation claims verified

- Third-party license table, checked against the installed package metadata and `uv.lock`: `phonemizer-fork` 3.3.2 carries the GPLv3+ classifier; `espeakng-loader` 0.2.4 declares no license and ships `libespeak-ng` plus `espeak-ng-data` inside the package; `mlx-audio` 0.5.4 declares MIT; `misaki` 0.9.4 declares the Apache classifier and the Apache license text. The Qwen2.5-14B Apache-2.0 claim is stated in `scripts/01_provision_engines.sh:31` and in ADR 0046 (revision and license verified 2026-10-14 per that ADR). The Kokoro model id matches `tts/generator.py:64`. The table's "not recorded in this repository" rows for Kokoro, Whisper and llama.cpp are accurate: no license is stated in the tree.
- README quick-start claims: the example sets `default_adapter: codex` (`config/llm-gateway.example.yaml:21`); the override variable exists (`llm/config.py:39`); the remote adapter is refused for a lecture not flagged before the executable is resolved (`llm/factory.py:36-45`, `llm/gateway.py:95`). Accurate.
- `operations.md`: `Dockerfile.site` builds `FROM python:3.12-slim` with five version-pinned packages. Accurate.
- `.gitignore`: `.claude/` now matches `.claude/settings.local.json` (`git check-ignore -v`), nothing under `.claude/` is tracked.
- Resolution of earlier findings: CR-01 resolved (all three sites, with tests). WR-01 resolved in substance (see IN-R2 for residual wording). IN-01 half resolved (`.claude/` added; the omitted media extensions are not). WR-06 only partly resolved (see WR-R2). WR-05 only the digest item is documented; TLS note, `HEALTHCHECK` and hash-pinned install remain open. CR-02, WR-02, WR-03, WR-04, IN-02 to IN-07 are not touched by this diff and remain as reported above.

### New Warnings

#### WR-R1: Regression tests are partly vacuous and the Content-Length assertion is too weak to guard the behavior

**Severity (caller scale):** Medium (test reliability)
**File:** `tests/test_site_app.py:2041-2085` (class `TestMalformedCredentialsNeverRaise`), `tests/test_site_quiz.py:164-169`, `tests/test_site_sessions.py` (unchanged)
**Issue:** Run against the old source, only the `9 * 5000` expiry, the `9 * 5000` Content-Length, `Basic éé` and the quiz case fail. The other parameters pass before and after the fix and add no protection:
- Cookie expiry `"²"` and `"٣٣"`: the test sends the cookie as UTF-8 bytes, the server decodes headers as latin-1, so the token arrives as mojibake that is not `isdigit()`; and `int("٣٣")` does not raise. Neither reaches the fixed branch. `"-1"` and `"1_0"` are not `isdigit()` and were already rejected.
- Content-Length `"²"` and `"abc"` fail the old `isdigit()` guard and were already ignored.
- The Content-Length test asserts only `status_code < 500`, so it accepts 200, 401 or 413 alike. It would not detect a regression that lets a malformed length skip the body limit, and the oversize-plus-malformed-length combination (the behavior the new docstring promises) has no test. `_parse_expiry` and `_declares_more_than` have no direct unit tests (`test_a_non_numeric_expiry_is_rejected` in `tests/test_site_sessions.py` covers only a plain non-numeric expiry), so the `"²"` case is never exercised at the function level where it does crash.
**Fix:** Add unit tests in `tests/test_site_sessions.py` calling `verify_session_token`/`_parse_token` with `"x.².y"` and the 5,000-digit token, expecting `None`. Assert exact statuses in the Content-Length test (the wrong-password login's own status), and add a case sending a body larger than `_MAX_LOGIN_BODY_BYTES` with `content-length: abc` expecting the "too large" response. Drop or relabel the parameters that cannot fail.

#### WR-R2: README quick start still copies the remote-default example; the warning follows the command and misses the built-in default

**Severity (caller scale):** Medium (documentation of an egress default; mitigated by the per-lecture flag)
**File:** `README.md:159-168`, `llm/config.py:7,71`
**Issue:** The `cp` line stays in the code fence and the caution appears two paragraphs later, so a reader who runs the block verbatim ends with a configuration whose default is the remote adapter. The new text also says only "a copy of [the example] used unchanged" routes to the remote model; the built-in default is also `codex` (`llm/config.py:71`, and the example's own header), so a user who skips the copy gets the same default. The egress guard (per-lecture flag) is correctly described, but the repository's local-first stance is still reached by opt-in rather than by default.
**Fix:** Either change `default_adapter` in the example to `llama-cpp` (preferred; the demo already selects the adapter explicitly), or move the caution immediately above the code fence and state that the built-in default is also the remote adapter.

### New Info

#### IN-R1: `operations.md` records a process circumstance, not a property of the system

**Severity (caller scale):** Low
**File:** `docs/operations.md:218`
**Issue:** "this repository change was made without network access" is a statement about how one change was authored, will be stale after the next edit, and is not verifiable by a reader (the earlier pass of this review had registry and GitHub access). A public limitation note should state the condition, not the history.
**Fix:** Reword to "The base image digest is not recorded yet" and keep the mitigation steps. The remaining WR-05 items (TLS-terminating proxy requirement, `HEALTHCHECK`, hash-pinned install) still need a decision.

#### IN-R2: License table duplicates versions and states one claim its own scope rule does not support

**Severity (caller scale):** Low
**File:** `README.md:316-330`
**Issue:** (a) Exact versions (3.3.2, 0.2.4, 0.5.4, 0.9.4) are copied from `uv.lock`, while `pyproject.toml` uses `>=`; the table will drift on the next lock refresh and `make docs-check` does not cover it. (b) The section says only what repository files and installed metadata state is recorded, but "the bundled eSpeak NG library is a GPL component" is stated by neither (ADR 0032 records the vendoring, not the license; the package metadata is empty). The statement is probably right, yet by the section's own rule it belongs in the "not recorded; see upstream" wording used for the other components. (c) The MIT claim in `pyproject.toml` and the closing sentence ("not decided here") leave the distribution question open on purpose; that is honest, but a published repository with GPL dependencies would be better served by a `NOTICE` pointer than by a README footnote.
**Fix:** Drop the version column or label it "locked version in uv.lock"; reword the espeak row to "not stated by the package; upstream eSpeak NG terms to be confirmed"; optionally mirror the table in a root `THIRD_PARTY.md`.

#### IN-R3: `.gitignore` change leaves the media-extension gap from IN-01

**Severity (caller scale):** Low
**File:** `.gitignore:95-100`
**Issue:** `.claude/` is covered, but `.webm`, `.m4v`, `.aac`, `.ogg`, `.flac` and `.csv` are still not listed; protection depends on `workspace/` being ignored.
**Fix:** Add the extensions, or record that `workspace/` is the intended single guard.

### Re-review totals

New findings in the corrected diff: 0 critical, 2 warning, 3 info. Findings from the first report that this diff closes: CR-01, WR-01; partially closes WR-06, WR-05, IN-01. Status remains `issues_found` because CR-02 and the other first-pass findings are unchanged.

_Re-reviewed: 2026-10-16_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_

## Closure addendum (CR-02, 2026-10-16)

CR-02 is resolved by the same operator-approved history rewrite that closes T-09-05 in `09-SECURITY.md`: all 153 commits now carry the GitHub no-reply identity as author and committer (verified with `git log --all --format='%ae %ce'`), gitleaks and the denylist scan were re-run clean over the rewritten history, the hook tests pass (43 passed), and the hash citations in the tracked tree were updated from the filter-repo commit map (290 replacements across 51 files, no residual old object id). No critical finding remains open. The open warnings and info findings (WR-02 to WR-06 in part, IN-02 to IN-07, and the re-review residuals IN-R2, WR-R2) are unchanged and non-blocking.
