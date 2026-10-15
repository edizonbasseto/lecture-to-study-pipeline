---
phase: 07
slug: synthetic-demo-lecture-and-measured-results
status: secured
verdict: SECURED
threats_total: 41
threats_closed: 41
threats_open: 0
asvs_level: 2
block_on: high
diff_base: b8cf64c
audited_head: 55f8665
audited_on: 2026-10-15
---

# Phase 07 - Security Verification

Verification of the `<threat_model>` registers of plans 07-01 to 07-08 against the
implemented code on branch `gsd/phase-07-synthetic-demo` (diff `b8cf64c..HEAD`, 29 commits).

- `threats_open` counts OPEN threats at or above `block_on: high`. No threat is open.
- No `.planning/config.json` exists; `block_on: high` is taken from the audit request and
  `asvs_level: 2` is assumed (mitigation present and placed at the correct boundary). The
  egress, credential and containment threats were traced end to end (L3 depth).
- 41 register rows = 33 unique threats (T-07-01..T-07-33) + 8 `T-07-SC` rows (one per plan).
- Implementation files were not modified by the audit.

## Evidence of independent checks

| Check | Result |
|-------|--------|
| `scripts/demo_run.py --adapter codex --lecture-id 925100 --preflight-only` (no flag) | stdout `refused: ... pass --confirm-remote-egress`, exit 2, before any preflight |
| same with `--confirm-remote-egress` on unflagged 925100 | `preflight=refused ... the lecture is not flagged remote_llm_allowed (ADR 0045 section 2)`, exit 1 |
| `--adapter llama-cpp --lecture-id 925100 --preflight-only` | `preflight=ok`, exit 0 |
| local registry (gitignored) | 925100 `remote_llm_allowed: false`; 925101 `remote_llm_allowed: true`; no other row |
| `reset_build_dir()` with `BUILD_DIR` pointed outside `workspace/demo` (probe in scratchpad) | `DemoBuildError: build directory is outside workspace/demo`; sentinel file survived |
| `gitleaks git --log-opts=b8cf64c..HEAD --redact` | 29 commits scanned, no leaks |
| range diff scan for `/Users/`, `/home/`, private-key blocks, operator name or e-mail | no matches |
| `docs/results/*` and ADR 0047: absolute paths, private keys, `DEMO_SITE_PASSWORD`, passwords, tokens | no matches |
| every `%` line in `docs/results/demo-results.md` carries ` of N` | yes (awk check, no exceptions) |
| generated site material (stat only, contents not read) | directory `drwx------`; `cert.pem`, `key.pem`, credentials, session key and the env file all `-rw-------` |
| `git ls-files` for pem, key, env, credentials, session-key | only `.env.example` (template) tracked |
| `git log --all` on `config/site-credentials.txt`, `*.pem`, `*.key`, `config/secrets`, `config/session-key` | no commit ever touched them |
| `git diff b8cf64c..HEAD` on `pyproject.toml`, `uv.lock` | empty |
| `git diff` on `src/lecture_study/assembly`, `llm`, `study_materials` and the five `generate_*` scripts | empty (only `tts/generator.py` and the new `demo/` package changed under `src/`) |

## Threat verification

Severity is the register value. Status is CLOSED unless stated.

### 07-01

| Threat | Category | Sev | Disp | Evidence | Status |
|--------|----------|-----|------|----------|--------|
| T-07-01 | Tampering (Kokoro argv) | medium | mitigate | `src/lecture_study/tts/generator.py:219-259` (`synthesize_kokoro_wav`), `:262-` `_kokoro_command` returns an argument list with narration as one `--text` element; `run_bounded_process` with `sanitized_child_process_environment()`, `_TTS_TIMEOUT_SECONDS`, output caps (`:237-244`); `tests/test_tts.py:195-225` asserts the exact argv shape (`--voice`, `--text`, `--join_audio`) and `TestSynthesizeKokoroWav` covers failure and non-wav paths. No shell construct anywhere in phase code (no `shell=True`, `os.system`, `subprocess.run/Popen`) | CLOSED |
| T-07-02 | Information disclosure (script content) | high | mitigate | `demo/lecture/script.yaml:1-12` provenance header (synthetic, no patient/owner/case data); lint `src/lecture_study/demo/script.py:182-192` (digit, abbreviation, unit, dosage-word rules) enforced at `scripts/build_demo_lecture.py:117-118`; manual scan of the file finds no digit, e-mail or URL in content; hooks installed in `.git/hooks` run `.githooks/pre-commit` (denylist + gitleaks); gitleaks over the range clean | CLOSED |
| T-07-03 | Tampering (YAML parse) | low | mitigate | `script.py:110` `load_strict_yaml`; `src/lecture_study/ops/strict_yaml.py:83-93` refuses aliases and tagged scalars; `script.py:59-60` strict, `extra="forbid"`, frozen; errors expose field locations only (`:116-122`) | CLOSED |
| T-07-SC | Tampering (installs) | low | accept | Accepted; acceptance condition holds: `pyproject.toml` and `uv.lock` unchanged in `b8cf64c..HEAD` | CLOSED (accepted) |

### 07-02

| Threat | Category | Sev | Disp | Evidence | Status |
|--------|----------|-----|------|----------|--------|
| T-07-04 | Tampering (process construction) | medium | mitigate | Pure argument-list builders `src/lecture_study/demo/media_build.py:71-173`; executables via `resolve_trusted_executable` (`scripts/build_demo_lecture.py:127-135`); all calls through `run_tool` -> `run_bounded_process` with sanitized env (`:147-162`); concat list rejects quotes, slashes, newlines (`media_build.py:147-152`) | CLOSED |
| T-07-05 | Information disclosure (media outputs) | medium | mitigate | Outputs fixed under `workspace/demo/` (`media_build.py:39-41`); `git check-ignore` matches `.gitignore:20` (`workspace/`) for mp4, wav, png, logs, runs; `*.mp4`, `*.wav`, `*.png` also ignored (`.gitignore:69,73,79`); no media tracked | CLOSED |
| T-07-06 | Denial of service | low | mitigate | Finite timeouts `build_demo_lecture.py:64-68`; callees bounded (`ocr_supervisor.py` `run_bounded_process`, `media_workers.py` `communicate_bounded_and_reap` with timeout) | CLOSED |
| T-07-07 | Tampering (build dir reset) | medium | mitigate | `build_demo_lecture.py:138-144`: `BUILD_DIR.resolve().is_relative_to(DEMO_WORKSPACE.resolve())` checked before `shutil.rmtree`; only `BUILD_DIR` is removed. Runtime probe confirmed refusal for an outside path | CLOSED |
| T-07-SC | Tampering (installs) | low | accept | Dependencies unchanged | CLOSED (accepted) |

### 07-03

| Threat | Category | Sev | Disp | Evidence | Status |
|--------|----------|-----|------|----------|--------|
| T-07-08 | Tampering (record parsing) | low | mitigate | `src/lecture_study/demo/run_metrics.py:69-77` and `asr_measure.py:85-95` raise typed errors with field locations only (`from None`); models strict, `extra="forbid"`, frozen (`run_metrics.py:36-39,56-59`) | CLOSED |
| T-07-09 | Repudiation (misleading rates) | medium | mitigate | `report.py:91-96` `format_rate` always prints ` of N`; `failed` rendering at `:256-258`, `:280-282`, `:323-324`; not-run adapters listed with reason; committed report shows codex under "Adapters not run" with its reason; every `%` line has ` of N` | CLOSED |
| T-07-10 | Information disclosure (report content) | medium | mitigate | `report.py:1-7` models hold counters, timings, model ids, lecture ids; `tests/demo/test_demo_report.py:274-278` asserts no absolute path; committed report scanned clean. Residual: see UF-2 | CLOSED |
| T-07-SC | Tampering (installs) | low | accept | `jiwer` not added; dependencies unchanged | CLOSED (accepted) |

### 07-04

| Threat | Category | Sev | Disp | Evidence | Status |
|--------|----------|-----|------|----------|--------|
| T-07-11 | Information disclosure (credentials, session key, TLS key) | high | mitigate | Generated at run time with `secrets` (`src/lecture_study/demo/site_access.py:78-83`) and openssl (`:92-114`, executed via `resolve_trusted_executable` + `run_bounded_process` `scripts/demo_site.py:195-205`). Modes: dir 0o700 (`demo_site.py:161-162`), files created with `os.open(..., 0o600)` then `fchmod` (`:187-192`), key and cert `chmod 0o600` (`:206-207`); on-disk stat confirms `drwx------` and `-rw-------` for all five files. Password only in the env file (`site_access.py:86-89`); allowlist stores `email:hash` (`:72-75`); `prepare-access` prints paths only. `require_inside(directory, workspace)` (`demo_site.py:160`) confines output to `workspace/`. Ignored by `.gitignore:20` (`workspace/`), `:23` (env file), `:59-61` (`.env.*`, `*.pem`, `*.key`); none tracked; never in any ref (`git log --all`); gitleaks clean. `config/site-credentials.txt`: no code in the range writes under `config/` (grep of range diff); the served site is pointed to the workspace copies by `Makefile:65-69` (`LECTURE_STUDY_SITE_CREDENTIALS`, `LECTURE_STUDY_SITE_SESSION_KEY`); the file is absent on this machine and gitignored (`.gitignore:24`). Tests: `tests/demo/test_demo_site_access.py:32-116` | CLOSED |
| T-07-12 | Spoofing (signed-in check) | medium | mitigate | `scripts/demo_site.py:117-148` real CSRF flow (anonymous GET, form GET, token parse, POST, GET with cookie jar); `SignedInView.passed` (`:80-86`) requires anonymous not 200, login 303, page 200, notice present; 07-06 evidence `anonymous_status=401 login_status=303 page_status=200 notice_present=True`; certificate pinned (`:230`) | CLOSED |
| T-07-13 | Tampering (openssl) | low | mitigate | `certificate_command` argument list (`site_access.py:92-114`), trusted executable, bounded process with 60 s timeout and 64 KiB caps (`demo_site.py:63-64,195-205`); tested (`test_demo_site_access.py:44-57`) | CLOSED |
| T-07-14 | Tampering (`generate_site` reset in dry run) | medium | mitigate | `tests/demo/test_demo_dry_run.py:76-172` `DryRunWorkspace` rooted at `tmp_path`; `:468-475` passes `study_guides_root`, `output_dir`, `video_library_dir`, `mock_exams_dir` explicitly; registration, queue and guide script also rooted in the workspace (`:242-263`, `:415-463` with `cwd=workspace.root`). Operator path `_run_generate` (`demo_site.py:220-224`) relies on defaults that are fixed constants inside `workspace/` (`src/lecture_study/site/generator.py:55-56`), no caller-supplied directory | CLOSED |
| T-07-15 | Information disclosure (network reach) | medium | mitigate | `DEMO_SITE_URL = https://127.0.0.1:8443` (`site_access.py:34`); `Makefile:68` `--host 127.0.0.1`; certificate SAN loopback only (`site_access.py:41`), `valid_days=2` (`:93`) | CLOSED |
| T-07-SC | Tampering (installs) | low | accept | `httpx`, `python-dotenv` pre-existing; dependencies unchanged | CLOSED (accepted) |

### 07-05

| Threat | Category | Sev | Disp | Evidence | Status |
|--------|----------|-----|------|----------|--------|
| T-07-16 | Tampering (ADR 0045 gate) | high | mitigate | Empty `git diff b8cf64c..HEAD` for `src/lecture_study/assembly` (gate in `assembler.py`, `domain/errors.py`), `llm/`, `study_materials/` and the four derivative scripts plus the critic script; inductions only newer-draft (`src/lecture_study/demo/recovery.py:104-109`) and failed-review (`:112-118`); verdict `_follows_adr_0045` (`:253-262`) requires exit 1 on all four scripts and unchanged filesystem snapshot (`tree_snapshot.py`, symlinks not followed) | CLOSED |
| T-07-17 | Tampering (stale-check bypass) | medium | mitigate | `recovery.py:109` `os.utime(..., ns=...)`; `:141-144` temp file + `replace`; no plain copy in the module | CLOSED |
| T-07-18 | Information disclosure (remote egress) | high | mitigate | `scripts/demo_run.py:176-179` `child_environment` forces `LECTURE_STUDY_LLM_ADAPTER`; `scripts/demo_blocked_recovery.py:45,93` forces `llama-cpp`; `llm/config.py:93-100,165-170` override replaces every stage; all stage scripts obtain gateways through `open_stage_gateway`, the only `CodexCliAdapter` construction is `llm/factory.py:55`; 925100 unflagged (registry stat above, `Makefile:37-46`). Live probe: codex on 925100 refused | CLOSED |
| T-07-19 | Repudiation (critic record loss) | medium | mitigate | `demo_blocked_recovery.py:128-130` exits 2 unless the harvested critic record exists; `tests/demo/test_demo_recovery.py:293`; harvested record present | CLOSED |
| T-07-20 | Information disclosure (evidence content) | low | mitigate | `ScenarioEvidence` (`recovery.py:164-175`) holds step names, exit codes, `LABEL:relative_path` keys, unit file names; `tree_snapshot.py:120-123` relative paths; logs and evidence under `workspace/demo/recovery` (gitignored) | CLOSED |
| T-07-SC | Tampering (installs) | low | accept | Dependencies unchanged | CLOSED (accepted) |

### 07-06

| Threat | Category | Sev | Disp | Evidence | Status |
|--------|----------|-----|------|----------|--------|
| T-07-21 | Information disclosure (egress of local-run lecture) | high | mitigate | (1) 925100 registered without the flag: `Makefile:37-46`, registry on disk `remote_llm_allowed: false`. (2) Adapter forced: `demo_run.py:176-179,266`. (3) Codex requires the flag, checked first in `main`: `demo_run.py:259-265` (exit 2), `Makefile:10,51,57` default empty and only `CONFIRM_REMOTE_EGRESS=yes` adds it; `test_demo_run.py:102-115` asserts preflight is not reached. (4) Preflight of all six `Stage` members before any process: `demo_run.py:182-193,267-268`; policy `study_materials/remote_llm_policy.py:45-48,70-84` fails closed on unflagged, duplicate or unreadable registry; `llm/factory.py:43-44` consults the policy before resolving the executable. Live probes confirm exit 2 and exit 1 | CLOSED |
| T-07-22 | Tampering (local registry) | medium | mitigate | No code in the range references the registry path (grep of range diff); only `scripts/register_local_lecture.py` via `Makefile:40-46`; `local_lectures.py:346-357` transactional and idempotent; registry holds exactly the two expected rows | CLOSED |
| T-07-23 | Information disclosure (logs, harvest) | medium | mitigate | `harvest.py:57-58` `workspace/demo/runs`, `workspace/demo/logs`; `git check-ignore` confirms; the range commits under `docs/` only the three expected files (ADR 0047, report md and json) | CLOSED |
| T-07-24 | Denial of service (hung step) | low | mitigate | `demo_run.py:50-54,201-208` timeouts and caps per step; `demo_blocked_recovery.py:60-61,97-104` | CLOSED |
| T-07-25 | Information disclosure (site exposure) | medium | mitigate | `Makefile:65-69` loopback bind with the two-day certificate; 07-06 SUMMARY records the server stopped afterwards | CLOSED |
| T-07-SC | Tampering (installs) | low | accept | Dependencies unchanged | CLOSED (accepted) |

### 07-07

| Threat | Category | Sev | Disp | Evidence | Status |
|--------|----------|-----|------|----------|--------|
| T-07-26 | Information disclosure (lecture text to provider) | high | mitigate | Synthetic content with provenance header (`script.yaml:1-4`); registry flag only on 925101 (verified on disk); blocking human-action checkpoint answered and recorded (`07-07-SUMMARY.md` "Task 2", confirmed 2026-10-15) before the confirmation variable was passed; driver refuses codex without the flag (`demo_run.py:259-265`; live probe exit 2). Provider terms remain a procedural operator check (ADR 0045 section 2) | CLOSED |
| T-07-27 | Elevation of privilege (codex on unflagged lecture) | high | mitigate | Egress policy refuses before any call: live probe of codex with confirmation on 925100 returned `preflight=refused ... not flagged remote_llm_allowed`, exit 1; `llm/factory.py:43-44`; preflight covers all six stages first (`demo_run.py:186-187`) | CLOSED |
| T-07-28 | Repudiation (egress decision) | medium | mitigate | Answer and date recorded in `07-07-SUMMARY.md` (checkpoint section); `NOT_RUN.txt` present, non-empty, and rendered in `docs/results/demo-results.md` "Adapters not run" via `harvest.py:184-193` | CLOSED |
| T-07-29 | Information disclosure (account details) | low | mitigate | `workspace/demo/logs/925101/preflight.txt` holds only the status line of the login check; `codex-diagnostic.txt` holds the content-free probe result; both under gitignored `workspace/`. See UF-3 | CLOSED |
| T-07-SC | Tampering (installs) | low | accept | Dependencies unchanged; no versioned file changed in 07-07 | CLOSED (accepted) |

### 07-08

| Threat | Category | Sev | Disp | Evidence | Status |
|--------|----------|-----|------|----------|--------|
| T-07-30 | Information disclosure (`docs/results`, ADR 0047) | high | mitigate | Reproduced the verify greps over `docs/results/*` and ADR 0047: no absolute path, private key block, password variable or token; whole-range diff scan for operator path/name/e-mail clean; gitleaks clean; hooks installed. Report models in `report.py:59-88` hold counters, timings, ids (plus free-text notes, see UF-2). The 40-hex `engine_version` in the report is the inference engine revision, not a secret | CLOSED |
| T-07-31 | Repudiation (overclaiming) | medium | mitigate | Every rate has ` of N` (`report.py:91-96`; awk check clean); codex listed as not run with reason; recovery section states "did not pass" and lists all five attempts; ADR 0047 section 10 "Limits" (`:183-` ) states one evidence pack, deck-floor deviation, single machine, not a benchmark | CLOSED |
| T-07-32 | Tampering (ADR 0045 gate during demo) | high | mitigate | Same empty diffs as T-07-16 over the final range; failures reported (`demo-results.md` recovery "did not pass"), nothing worked around | CLOSED |
| T-07-33 | Information disclosure (egress during recovery) | high | mitigate | `demo_blocked_recovery.py:45,93-96` forces `llama-cpp` in the child environment of every script; default lecture 925100 unflagged (`:43`); `test_demo_recovery.py` runner tests | CLOSED |
| T-07-SC | Tampering (installs) | low | accept | Dependencies unchanged | CLOSED (accepted) |

## Accepted risks log

| Threat | Plans | Risk accepted | Acceptance condition | Verified |
|--------|-------|---------------|----------------------|----------|
| T-07-SC | 07-01 to 07-08 | No package is installed by the phase; supply-chain tampering through new dependencies is out of scope | `pyproject.toml` and `uv.lock` unchanged | yes, empty diff over `b8cf64c..HEAD` |

## Unregistered flags

SUMMARY `## Threat Flags` sections of all eight plans declare none. The audit found these items
without a register entry. None meets the blocking threshold.

| ID | Sev | Observation | Files | Disposition |
|----|-----|-------------|-------|-------------|
| UF-1 | low | The operator-supplied `--lecture-id` is not validated against the identifier pattern before it joins filesystem paths. In `demo_blocked_recovery.py` it reaches `default_layout` and then `unit_path.unlink()` of `unit_*.json` files without a draft (`recovery.py:121-133`) and `os.utime` / atomic rewrite of a unit; in `demo_run.py` it reaches the log path (`:221-226`). No containment check precedes these writes (the adapter is forced local, so there is no egress effect). Local, operator-run tooling | `scripts/demo_blocked_recovery.py`, `scripts/demo_run.py`, `src/lecture_study/demo/recovery.py` | non-blocking; suggest `SafeIdentifier` validation of `--lecture-id` |
| UF-2 | low | Free-text channels render verbatim into committed documents: adapter `notes`, `NotRunAdapter.reason` (from `NOT_RUN.txt`) and `recovery_notes` (`report.py:59-88`; `demo_results.py:170-174`; `harvest.py:331-335`). The no-absolute-path test uses fixture data, so a future note holding a path would depend on the manual greps and hooks. Current committed content is clean | `src/lecture_study/demo/report.py`, `scripts/demo_results.py`, `src/lecture_study/demo/harvest.py` | non-blocking; suggest a path/secret guard in the renderer |
| UF-3 | info | The committed report states the operator's configured codex model id and that the provider rejected it for a "ChatGPT account". This is plan-type information, not a credential. Separately, 07-07 ran one content-free direct `codex exec` diagnostic outside the gateway; disclosed as deviation 3 in `07-07-SUMMARY.md`, no lecture content sent | `docs/results/demo-results.md`, `07-07-SUMMARY.md` | informational |
| UF-4 | info | `reset_build_dir` accepts a `BUILD_DIR` that resolves to `workspace/demo` itself (`is_relative_to` is inclusive) and has no unit test for the refusal branch; `BUILD_DIR` is a module constant, so unreachable in practice. `check_signed_in_view` treats any non-200 anonymous status as refused (observed 401) | `scripts/build_demo_lecture.py:138-144`, `scripts/demo_site.py:80-86` | informational |
| UF-5 | info | `config/site-credentials.txt` is absent on this machine, so "never overwritten" is shown by code-path analysis (no write path outside `workspace/`, confined by `require_inside`) and not by a before/after comparison | n/a | informational |
| UF-6 | info | The leak-gate hooks depend on a private denylist outside the repository. It exists and is non-empty (contents not read); whether each commit ran the hook cannot be proven from the repository. Independent gitleaks and regex scans of the range were clean | `.githooks/` | informational |

## Verdict

SECURED. 41 of 41 register rows are closed (33 mitigated, 8 accepted), `threats_open: 0`.
