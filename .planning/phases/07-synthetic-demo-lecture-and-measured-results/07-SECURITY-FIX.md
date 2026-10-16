---
phase: 07
slug: synthetic-demo-lecture-and-measured-results
scope: uncommitted working tree on top of HEAD 2eaa505 (MD-01..MD-04 fixes plus the codex results regeneration)
status: secured-with-nonblocking-findings
verdict: SECURED
asvs_level: 2
block_on: high
threats_open: 0
threats_open_nonblocking: 2
audited_on: 2026-10-16
baseline: .planning/phases/07-synthetic-demo-lecture-and-measured-results/07-SECURITY.md (41/41 closed at 55f8665)
---

# Phase 07 - Security Verification of the Uncommitted Fixes

Verification of the uncommitted diff against the 07-SECURITY.md threat register. Scope:
`git diff HEAD -- scripts src tests docs` (15 modified files, 834 insertions, 53 deletions) plus the
untracked `src/lecture_study/demo/lecture_id.py`. `asvs_level: 2` and `block_on: high` are carried over
from 07-SECURITY.md (no `.planning/config.json`). Egress, containment and secret-location checks were traced
to L3 depth. Implementation files were not modified, nothing was committed.

## Verdict

SECURED for the gate: `threats_open: 0` (no open threat at or above `high`). Two OPEN findings below the
threshold are recorded as non-blocking (both against T-07-31, severity medium). A caller that treats any OPEN
row as failing should read the result as OPEN_THREATS (non-blocking only); neither finding reaches secret
handling, egress, containment or destructive operations.

## Independent checks run

| Check | Result |
|-------|--------|
| `parse_lecture_id` probe, 28 hostile inputs (`../x`, `a/b`, `a\b`, empty, space, `abc\n`, `\nabc`, NUL, `.`, `..`, `a.b`, Arabic-Indic digits, accented letters, U+2028, `~`, `$HOME`, `a;b`, `a$(id)`, `%2e%2e`) | all rejected; `925100`, `925101`, `A_b-9` accepted. Trailing newline is rejected (pydantic regex engine anchors `$` at end of text). Leading hyphen accepted (`-x`, `--x`), see NF-3 |
| `scripts/demo_run.py` live, `--preflight-only`: codex without flag | exit 2, `refused: ... pass --confirm-remote-egress`, before any preflight |
| same, codex with flag on unflagged 925100 | `preflight=refused ... not flagged remote_llm_allowed (ADR 0045 section 2)`, exit 1 |
| same, llama-cpp on 925100 | `preflight=ok`, exit 0 |
| `--lecture-id ../../x`, `a/b` on `demo_run.py`; `../../x` on `demo_blocked_recovery.py`; `../x` on `demo_results.py harvest`; `a/../b` on `demo_site.py check` | argparse `invalid parse_lecture_id value`, exit 2, before any path is built |
| `demo_results.py report --output-dir <scratchpad>`; `--output-dir .` | `report refused: ... is outside <repository>`, exit 1, nothing written (the root itself is refused) |
| `demo_site.py prepare-access --directory <scratchpad>`; `--directory workspace` | `ValueError ... is outside .../workspace`, exit 1, nothing written (the workspace root itself is refused) |
| local registry (gitignored `config/local-lectures.local.yaml`) | 925100 `remote_llm_allowed: false`, 925101 `remote_llm_allowed: true` |
| `git diff b8cf64c` over `src/lecture_study/{assembly,llm,study_materials}`, the five `generate_*` scripts, `pyproject.toml`, `uv.lock`, `.gitignore` | empty; the only changed file in that set is `Makefile` (phase commit, unmodified in the working tree) |
| `git diff HEAD` on `Makefile`, `.gitignore`, `pyproject.toml`, `uv.lock`, `docs/decisions` | empty |
| `pytest tests/demo` on the working tree | 215 passed, 1 skipped |
| gitleaks (`dir`, redacted) on `docs/results`, `scripts`, `src/lecture_study/demo`, `tests/demo`; and on the untracked `workspace/demo/{runs/codex,logs,recovery}` | no leaks found in any of them |
| full diff scan for `/Users/`, `/home/`, private-key blocks, `sk-` tokens, operator name or e-mail, `password =`, `token =` | no matches (one regex hit was the `@pytest.fixture` decorator) |
| `docs/results/*` scan for absolute paths, e-mail, URL, token, bearer, cookie, session, credential, password, long hex | only matches: the 40-hex llama.cpp `engine_version` (unchanged since the audited report; an inference engine revision, not a secret) and the word "authoritative" |
| every `%` line in `docs/results/demo-results.md` carries ` of N` | yes, no exceptions |
| JSON consistency: adapters `codex:925101`, `llama-cpp:925100`; `adapters_not_run: []`; recovery lecture 925100 adapter llama-cpp, both scenarios `passed: false` | consistent with the markdown ("the last run did not pass") |
| `git ls-files workspace` | 0 tracked files; `git check-ignore` matches `.gitignore:20` for the evidence, logs and video paths and `:24` for `config/site-credentials.txt` |
| `workspace/demo/site-access` modes | `drwx------`; `.env`, `cert.pem`, `key.pem`, `site-credentials.txt`, `site-session-key` all `-rw-------`; untouched by the probes (mtime 2026-10-15 17:41) |
| `~/.codex/config.toml` (single `model =` line only) | `model = "gpt-6.1-sol"`, the value the report states was restored; mode `-rw-------`; no backup created by the 2026-10-16 run |

## Requested focus areas

### 1. Path traversal via lecture id and paths - CLOSED

- UF-1 of 07-SECURITY.md is resolved. `src/lecture_study/demo/lecture_id.py:11-19` validates against
  `SafeIdentifier` (`^[A-Za-z0-9_-]+$`, `domain/models.py:28`), the same slug type the domain models use.
- Applied at every id entry point of the demo tooling: argparse `type=` at `scripts/demo_run.py:304`,
  `scripts/demo_blocked_recovery.py:176`, `scripts/demo_results.py:231`, `scripts/demo_site.py:260`; and in the
  library at `recovery.py:87` (`default_layout`, which feeds `unlink` of `unit_*.json` and `os.utime`) and
  `harvest.py:100` (`HarvestSources.for_lecture`, which feeds `copytree`). The library-level checks keep a
  programmatic caller from bypassing the CLI. argparse also validates string defaults, so
  `DEFAULT_LECTURE_ID` is checked.
- Tests: `test_harvest_sources_refuse_an_unsafe_lecture_id`, `test_default_layout_refuses_an_unsafe_lecture_id`,
  `test_driver_rejects_an_unsafe_lecture_id`, the `demo_run` and `demo_site` equivalents.
- Path containment (`require_inside`, `site_access.py:117-127`) now refuses the root itself
  (`resolved_path == resolved_root`), resolves symlinks before comparing, and is covered by
  `test_require_inside_refuses_the_root_itself`.
- Working-directory anchoring: `os.chdir(REPOSITORY_ROOT)` at `demo_run.py:272`, `demo_blocked_recovery.py:150`,
  `demo_results.py:242`, `demo_site.py:268`, so the relative `workspace/...` roots that feed destructive
  operations always mean the repository workspace. `demo_site.WORKSPACE_ROOT` is absolute (`:60`).

### 2. Secret-writing locations - CLOSED

- Credentials, session key, TLS key and the env file are still written only by `prepare_access`
  (`demo_site.py:153-172`), confined by `require_inside(directory, workspace_root)` (`:162`) against an absolute
  workspace root, now strict (root itself refused). Mode handling (`0o700` directory, `os.open(..., 0o600)` plus
  `fchmod`, key and cert `chmod 0o600`) is unchanged; on-disk modes confirmed. All locations remain under the
  gitignored `workspace/`; nothing is tracked; gitleaks clean.
- No new write path for secrets was introduced. New writes in the diff: the recovery evidence (now confined to
  `<repo>/workspace`, `demo_blocked_recovery.py:156-161`, checked before any scenario starts), the report output
  (confined to the repository, `demo_results.py:207-212`), abort logs under gitignored `workspace/demo/logs` and
  `workspace/demo/recovery`, and the build promotion (`build_demo_lecture.py:242-246`, `replace` onto the
  existing `DEMO_VIDEO_PATH` under `workspace/demo`).
- Abort logs record `repr(error)` of `TimeoutExpired`, which includes the child argv (absolute interpreter and
  script paths, the lecture id, the whisper model path) but not the environment, so no credential reaches them.
  They stay in gitignored paths and are not read by the harvest (only `notes.txt` and the phase lines are).

### 3. Destructive operations - CLOSED, with residual noted (NF-4)

- `unit_path.unlink()` (`recovery.py` `delete_units_without_draft`), `os.utime` and the atomic unit rewrite are
  reachable only with a validated id and a layout rooted at the repository workspace. `shutil.rmtree` in
  `harvest.py:164` targets its own staging directory. `reset_build_dir` is unchanged and still guarded
  (T-07-07).
- `build_demo_lecture.py` now runs the acceptance gate on `BUILD_DIR/<partial>` and promotes only after
  acceptance (`:130-131`); a rejected build no longer overwrites the consumed video. Tests
  `test_a_rejected_video_is_never_promoted`, `test_an_accepted_video_is_promoted`.
- On a script abort the recovery driver leaves the lecture in the induced state; this is disclosed on stderr
  (`_aborted_warning`) and the exit code is 1. The prior behavior was an unhandled traceback with the same
  residual state, so the diff improves it. The lecture is the local synthetic 925100 in the gitignored workspace;
  no confidentiality effect.

### 4. Subprocess handling - CLOSED

- All children still run through `run_bounded_process` with argument lists, `start_new_session=True`,
  sanitized environment, finite timeouts and output caps; no `shell=True`, `os.system` or `subprocess.run/Popen`
  was added (the only `subprocess` imports added are for the exception types in `_STEP_ABORTS` and
  `_SCRIPT_ABORTS`).
- The new `except` clauses (`demo_run.py:216`, `demo_blocked_recovery.py:118`) catch exceptions raised after
  `run_bounded_process` has already terminated and reaped the process group
  (`process_lifecycle.py:203-206`, `_terminate_and_reap_sync` with `killpg` SIGTERM then SIGKILL), so a timeout
  does not leave an orphan holding the LLM adapter.
- Adapter forcing is untouched: `child_environment` (`demo_run.py:185`) and the recovery runner environment
  (`demo_blocked_recovery.py:105`) still set `ADAPTER_OVERRIDE_ENV`; the codex confirmation check still runs
  first in `main` and the six-stage preflight still precedes any process. Live probes above confirm.

### 5. docs/results contain no secrets, credentials or PII - CLOSED

- The codex additions (`docs/results/demo-results.md`, `.json`) hold counters, timings, planted-term counts,
  lecture id `925101`, adapter and model labels (`codex-cli`), and one free-text note. No absolute path, e-mail,
  token, cookie, credential, account identifier or URL; gitleaks clean; every percentage has a denominator.
- The free-text note flows verbatim from the gitignored `workspace/demo/runs/codex/logs/notes.txt`
  (UF-2 channel). Its content states two model identifiers, that the provider rejected one for "a ChatGPT
  account" (plan type, not an identifier), and that the user-level codex configuration was temporarily changed and
  restored. It is not a credential or PII (UF-3 class, informational). The restoration claim was checked: the
  `model =` line of the user-level configuration equals the original value named in the note.

## Threat re-verification (register rows affected by the diff)

| Threat | Sev | Status | Evidence in the working tree |
|--------|-----|--------|------------------------------|
| T-07-11 secrets at rest | high | CLOSED | section 2; strict `require_inside`; modes and ignore rules verified |
| T-07-07 build dir reset | medium | CLOSED | `reset_build_dir` unchanged; promotion now post-acceptance |
| T-07-16 / T-07-32 ADR 0045 gate | high | CLOSED | empty diff over gate sources; the only gate-adjacent edits are fail-safe abort handling and an id check |
| T-07-17 stale-check bypass | medium | CLOSED | `os.utime(ns=...)` and atomic rewrite unchanged; id validated before use |
| T-07-18 / T-07-33 egress during run and recovery | high | CLOSED | `ADAPTER_OVERRIDE_ENV` forced at `demo_run.py:185`, `demo_blocked_recovery.py:105`; live probes |
| T-07-21 / T-07-27 codex on unflagged lecture | high | CLOSED | live probes (exit 2, exit 1); registry flags verified |
| T-07-20 / T-07-23 logs and evidence content | low / medium | CLOSED | content-free evidence; paths gitignored; abort logs under `workspace/` |
| T-07-24 hung step | low | CLOSED | timeouts and caps unchanged; abort now reported as a failed step instead of a traceback |
| T-07-19 critic record guard | medium | CLOSED | guard still precedes everything (`demo_blocked_recovery.py:151-154`); evidence containment follows it, before any induction |
| T-07-30 docs/results disclosure | high | CLOSED | section 5 |
| T-07-09 misleading rates | medium | CLOSED | every `%` line has ` of N`; codex rows carry denominators |
| T-07-31 overclaiming | medium | OPEN, non-blocking | NF-1 and NF-2 |
| T-07-26 / T-07-28 lecture text to provider, egress decision record | high / medium | CLOSED, residual NF-5 | original 2026-10-15 confirmation stands; flagged synthetic lecture only |

## Open findings (non-blocking, below `block_on: high`)

| ID | Threat | Sev | Finding | Where | Suggested closure |
|----|--------|-----|---------|-------|-------------------|
| NF-1 | T-07-31 | medium | An aborted recovery run writes a partial `evidence.json` over the previous complete one. `RecoveryEvidence.passed()` is `bool(scenarios) and all(...)`, so a run where scenario 1 passed and scenario 2 aborted yields `passed() == True`, and `report.py` `_recovery_block` would print "the last run passed (ADR 0045)" for an incomplete run and drop the aborted scenario. The process exit code (1) and the stderr warning protect the operator, not the artifact the report consumes. No test covers "scenario 1 passes, scenario 2 aborts" | `scripts/demo_blocked_recovery.py:164-167,204-235`; `src/lecture_study/demo/recovery.py` (`RecoveryEvidence.passed`); `src/lecture_study/demo/report.py:352-353` | Write the aborted evidence to a separate file (or skip the write), or add a `complete` field to `RecoveryEvidence` and require it in `passed()` |
| NF-2 | T-07-31 | medium | The uncommitted report contradicts the committed ADR 0047. The ADR still states that the Codex run "could not be run", that the adapter is listed under "Adapters not run", that "the codex configuration was not edited", and that the Codex measurement is open (`docs/decisions/0047-demo-lecture-measurement-method.md:10,89-96,236-238`). The new report states a codex measurement exists and that the user-level codex configuration was temporarily changed. Committing the results alone would publish two incompatible statements about the same run | `docs/decisions/0047-...md` vs `docs/results/demo-results.md` | Update ADR 0047 (status, decision 4 paragraph, Consequences) in the same commit as the results |

## Unregistered observations (informational or low, not threats in the register)

| ID | Sev | Observation | Disposition |
|----|-----|-------------|-------------|
| NF-3 | low | The `SafeIdentifier` alphabet admits a leading hyphen (`-x`, `--help`). The id is spliced as a positional argument into child argv (`script_command`, `pipeline_steps`) without a `--` separator, so `--lecture-id=--help` (equals form, accepted by the parent) would reach a child CLI as an option. Impact is limited to operator-supplied bare flags on operator-run tooling; no flag combination found that carries a value (the alphabet has no `=` or space) | suggest `lecture_id` to reject a leading `-` or add `--` before the positional |
| NF-4 | low | An aborted recovery scenario leaves the lecture in the induced stale or rejected state; recovery needs a manual critic rerun (stderr warning says so). Also `except (ScriptAborted, OSError)` at `demo_blocked_recovery.py:217` reports any filesystem fault of induction or snapshot as "aborted" | informational; local synthetic lecture |
| NF-5 | info | The 2026-10-16 codex run relies on the 2026-10-15 egress confirmation of the 07-07 checkpoint. No dated decision record of the rerun, nor of the temporary edit of `~/.codex/config.toml`, exists in the repository other than the report note (a search of the tree for the model identifiers finds only `docs/results/*`). The content sent was the synthetic 925101 lecture, flagged `remote_llm_allowed: true` | informational; T-07-28 evidence is the original record |
| NF-6 | info | `Makefile` recipes interpolate `$(LECTURE)` unquoted (lines 49, 54, 72, 77); shell metacharacters are expanded before the Python validators run. The variable is operator-supplied in the operator's own shell, so there is no privilege boundary; carried over from 07-REVIEW-FIX IN-03 | informational |
| NF-7 | info | `demo_results.py report --output-dir` is confined to the whole repository, not to `docs/` or `workspace/`; only the two fixed file names (`demo-results.md`, `.json`) can be written. `os.chdir(REPOSITORY_ROOT)` before `parse_args` re-anchors user-supplied relative paths to the repository root. `prepare-access` with a refused directory ends in an uncaught `ValueError` traceback | informational; carried over from 07-REVIEW-FIX WR-04, IN-01 |
| NF-8 | info | Integrity, not security: a rejected rebuild still leaves the previous video at the consumed path while `reset_build_dir` removes its build summary; `promote_video` can raise `OSError` outside `BUILD_FAILURES` after the summary is written (07-REVIEW-FIX WR-03). The abort log keeps no child output, so measured wall times of an aborted step are lost (WR-02) | informational |

Carried over unchanged from 07-SECURITY.md: UF-2 (free-text channels reach committed documents), UF-3, UF-4
(`is_relative_to` inclusive in `reset_build_dir`), UF-5, UF-6.

## Accepted risks

T-07-SC (no package installed): acceptance condition still holds, `pyproject.toml` and `uv.lock` have an empty
diff against `HEAD` and against `b8cf64c`.

## Result

`threats_open: 0` at `block_on: high`. Verdict SECURED. Recommended before the commit of the results: close NF-2
(ADR 0047 text) and, if the recovery is rerun, NF-1.
