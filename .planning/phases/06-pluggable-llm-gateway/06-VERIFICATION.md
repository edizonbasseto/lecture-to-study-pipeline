---
phase: 06-pluggable-llm-gateway
verified: 2026-10-14T21:30:00Z
status: human_needed
score: 4/4 roadmap success criteria verified (GW-01, GW-02, GW-04, GW-05, GW-06 satisfied; GW-03 engine run pending)
covered_files:
  - .planning/phases/06-pluggable-llm-gateway/06-01-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-01-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-02-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-02-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-03-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-03-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-04-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-04-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-05-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-05-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-06-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-06-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-07-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-07-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-08-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-08-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-09-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-09-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-10-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-10-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-11-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-11-SUMMARY.md
  - .planning/phases/06-pluggable-llm-gateway/06-12-PLAN.md
  - .planning/phases/06-pluggable-llm-gateway/06-12-SUMMARY.md
  - config/llm-gateway.example.yaml
  - docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md
  - scripts/01_provision_engines.sh
  - scripts/generate_codex_critic_review.py
  - scripts/generate_codex_drafts.py
  - scripts/generate_codex_mock_exam.py
  - scripts/generate_codex_personal_study_flashcards.py
  - scripts/generate_codex_podcast.py
  - scripts/generate_codex_semantic_ledgers.py
  - src/lecture_study/cli/llm_gateway_wiring.py
  - src/lecture_study/cli/orchestrator.py
  - src/lecture_study/llm/__init__.py
  - src/lecture_study/llm/bounded_call.py
  - src/lecture_study/llm/codex_adapter.py
  - src/lecture_study/llm/config.py
  - src/lecture_study/llm/egress.py
  - src/lecture_study/llm/factory.py
  - src/lecture_study/llm/gateway.py
  - src/lecture_study/llm/json_completion.py
  - src/lecture_study/llm/llama_cpp_adapter.py
  - src/lecture_study/llm/run_record.py
  - src/lecture_study/study_materials/local_lectures.py
  - src/lecture_study/study_materials/remote_llm_policy.py
covered_digest: "v3:sha256:fdc695af71226176fb1d9352dab568cc82d7fee73a29da4cc93cff64e8fc4883"
behavior_unverified: 0
overrides_applied: 0
human_verification:
  - test: "D-07 manual real-model run of stages 5, 6 and 7 with the llama.cpp adapter on one evidence pack (ADR 0046 decision 10), then fill the record table and mark follow-ups (a)-(e) needed or not needed"
    expected: "Stages 5, 6, 7 exit 0 with LECTURE_STUDY_LLM_ADAPTER=llama-cpp; run records show adapter llama-cpp; no Codex process; every decision-4 flag accepted at the pinned llama.cpp commit; any item marked needed is logged as a failed UAT item for /gsd-plan-phase 6 --gaps"
    why_human: "Needs built llama.cpp binaries and GGUF weights that exist neither in CI nor on the planning machine; the stub proves the argv/env/parse contract, not the engine. GW-03 stays Pending in REQUIREMENTS.md by design until this run is recorded."
  - test: "R0.4 re-run: execute /gsd-code-review and /gsd-secure-phase against the Round 3 delta (commits c40a4aa, caeab14, f16df70, 63dad05), or explicitly accept the verifier's delta read below"
    expected: "No new blocking finding in the 4 source files changed after the final review and audit (about 100 source lines, 260 lines with tests)"
    why_human: "06-REVIEW.md iteration 3 covers the diff up to c1196dc and 06-SECURITY.md states the Round 3 hardening 'followed the final audit'; the global rule R0.4 requires the affected reviews to be re-run on the corrected diff. Whether the operator accepts a verifier read in place of a formal re-run is an operator decision."
  - test: "After the push to origin, confirm the CI run on the phase-final commit is green (test job on macos-15 runs uv run pytest -q -rs, ruff check, ruff format --check, mypy src)"
    expected: "CI success, with tests/llm_gateway and tests/test_llm_layering.py included and no skips beyond the root-only permission test"
    why_human: "The branch is not pushed (no remote ref contains HEAD) and the last CI run on main predates the phase. Local macOS run is green, but criterion 3 says 'in CI'."
---

# Phase 6: Pluggable LLM gateway Verification Report

**Phase Goal:** Every LLM-backed stage (5 semantic ledgers, 6 drafts, 7 critic, 13 exam, 14 podcast, 18 personal-study flashcards) calls one `LlmGateway` port; a `CodexCliAdapter` keeps current behavior and a `LlamaCppAdapter` runs a local model with no network egress; the adapter is chosen by configuration.
**Verified:** 2026-10-14
**Status:** human_needed
**Re-verification:** No -- initial verification

The goal is achieved in code and in stub-backed tests. No truth failed. Status is `human_needed` because of the known accepted D-07 manual run (GW-03), one process-level item (R0.4 re-run after Round 3) and the post-push CI confirmation. None of the three is a code gap.

## Goal Achievement

### Observable Truths (ROADMAP success criteria, the contract)

| # | Truth | Status | Evidence |
| - | ----- | ------ | -------- |
| 1 | Switching the configuration from codex to llama-cpp runs stages 5, 6 and 7 on a lecture with no Codex invocation | VERIFIED | `tests/llm_gateway/test_stages_without_codex.py` drives `run_phase` for phases 5, 6, 7 through the real generation scripts as child processes. `llama-completion` and `llama-tokenize` are scripted stubs; a tripwire `codex` stub sits first on `PATH`. Both cases pass: (a) YAML `default_adapter: llama-cpp`, (b) YAML with every stage `codex` overridden by `LECTURE_STUDY_LLM_ADAPTER=llama-cpp`. Assertions: ledger, draft and validated unit exist; `llm_runs/<lecture>/{semantic,drafting,critic}.json` have status `succeeded` and only adapter `llama-cpp`; `tripwire_codex_on_path.calls() == []`. Behavior-dependent truth: backed by a passing behavioral test, not by presence. Code path confirmed: `factory.build_gateway` returns `LlamaCppAdapter` and never resolves the Codex executable on that branch; no module outside `src/lecture_study/llm/` spawns Codex (`rg` over `src` and the six scripts: no `subprocess`, `run_bounded_process`, `tempfile` or `"codex"` literal in any script). |
| 2 | An unknown adapter name exits non-zero before any model call | VERIFIED | `tests/llm_gateway/test_preflight.py::test_unknown_adapter_exits_before_the_first_phase` calls `cli_main.main(["run-all", ...])` with `LECTURE_STUDY_LLM_ADAPTER=bogus`: `SystemExit(1)`, message names the variable, zero resume-predicate calls, zero phase runs, zero `run_bounded_process` calls. Same layer proven for a missing llama executable (stub call log empty) and for an unflagged lecture on codex (tripwire empty). Second layer (D-12): `test_semantic_stage_gateway.py::test_unknown_adapter_override_exits_before_any_call` and `test_configuration.py` (unknown `default_adapter`, stage adapter, keys). Wiring read: `run_all`, `run_phase`, `run_personal_study` call `_preflight_llm_gateways` before any resume predicate or spawn; `PhaseSpec.llm_stages` declares rows 5, 6, 7 (critic and drafting), 13, 14. |
| 3 | The contract tests pass for both adapters in CI | VERIFIED (local run; CI run pending, see human item 3) | `tests/llm_gateway/test_gateway_contract.py` is parametrized `params=["codex", "llama_cpp"]` over real stub processes (raw bytes, JSON object and array round trips, single bounded retry with feedback, two bad responses give exactly two calls, non-zero exit, timeout, output cap, fresh empty cwd, oversized prompt before any model call); `test_critic_repair_contract.py` runs the ADR 0036 loop through both adapters. Run by me: `uv run pytest tests/llm_gateway tests/test_llm_layering.py -q -p no:cacheprovider` gave 236 passed; full `uv run pytest -q -rs` gave 2559 passed, 1 pre-existing starlette warning. `.github/workflows/ci.yml` `test` job (macos-15) runs `uv run pytest -q -rs`, so these tests are in the CI path. No remote CI run on the phase-final diff exists yet. |
| 4 | The new ADR is Accepted, and ADR 0038 and ADR 0045 carry amendment notes pointing to it | VERIFIED | `docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md` line 5: `Accepted. 2026-10-14.` ADR 0038 line 7 `Amended by ADR 0046`; ADR 0045 line 12 `Section 2 closed by ADR 0046` plus in-body notes at lines 128 and 215. Forward notes also in ADRs 0020, 0022, 0034, 0035, 0036, 0037 and `.planning/PROJECT.md`. REQUIREMENTS.md GEN-07 carries the amendment; GW-05 carries the fail-closed flag clarification. |

**Score:** 4/4 roadmap truths verified; 0 behavior-unverified.

### Requirement-level truths (PLAN/CONTEXT, merged; no scope reduction)

| Requirement | Status | Evidence |
| ----------- | ------ | -------- |
| GW-01 port; six stages call it | SATISFIED | All six scripts import `open_stage_gateway` and `complete_json` (`rg` hits in each); none imports a process-spawning or scratch module. `tests/test_llm_layering.py` (AST) enforces the six-script boundary, `site/` and `ops/` never importing `llm/`, and inner modules importing no adapter. |
| GW-02 Codex adapter preserves behavior | SATISFIED | `codex_adapter.py` argv `exec --skip-git-repo-check --sandbox read-only -C SCRATCH -o SCRATCH/response.txt PROMPT` asserted against a real stub in `test_codex_adapter.py`; scratch cwd outside the repo; 900 s and 16 MiB defaults; typed errors without prompt; argv-size guard. Failure capture under `tmp/codex_phase_failures` is still owned by the orchestrator (`_CODEX_PHASE_FAILURE_LOGS_DIR`). |
| GW-03 LlamaCppAdapter, no egress | PARTIAL, by design | Contract and no-egress shown by construction and tests: `llama-completion` plus `llama-tokenize` (never `llama-cli`), prompt via file, `--offline --single-turn --no-display-prompt --no-escape --simple-io --reasoning off`, retry-only `--repeat-penalty 1.3 --repeat-last-n 256`, env = sanitized allowlist minus `LLAMA_ARG_*`/`HF_*` plus `LLAMA_ARG_OFFLINE=1`, model path must be an existing regular file and not a URL, tripwire stubs reject forbidden flags, URLs and download variables, exact token count before generation raises `PromptTooLargeError`. The engine has never run: REQUIREMENTS.md keeps GW-03 Pending (confirmed, line 253). Human item 1. |
| GW-04 configuration selection, fail closed | SATISFIED | `llm/config.py` strict YAML + `LECTURE_STUDY_LLM_ADAPTER`/`LECTURE_STUDY_LLM_CONFIG`; default codex without a file; env override clears per-stage entries (D-20); orchestrator preflight plus per-script re-validation; egress refusal before the Codex executable is resolved. `config/llm-gateway.example.yaml` versioned, local copy ignored through `config/*.local.yaml` in `.gitignore`. |
| GW-05 ADR amends 0038, closes 0045 section 2 | SATISFIED | See truth 4. Egress rule enforced by code: `RegistryRemoteEgressPolicy` (registry flag `remote_llm_allowed`, portal list, OPEN-2 conflict is a configuration error). 20 egress-policy tests pass, including duplicate rows, repeated keys, unparseable YAML and unreadable registry all refusing. |
| GW-06 same contract tests, stub binary in CI | SATISFIED | See truth 3. |

### Required Artifacts

| Artifact | Status | Details |
| -------- | ------ | ------- |
| `src/lecture_study/llm/` (10 modules, 1681 lines in `llm/`, wiring, policy) | VERIFIED | Substantive (adapter 332 lines, run record 355 lines), wired through `cli/llm_gateway_wiring.py` into the six scripts and the orchestrator. |
| `config/llm-gateway.example.yaml` | VERIFIED | Synthetic values; documents precedence and strictness; keys match `config.py` models (tested by loading it in `test_configuration.py`). |
| `scripts/01_provision_engines.sh` | VERIFIED | `bash -n` ok, `shellcheck` clean, `tests/test_provision_engines.py` green (179 passed across provisioning, CLI and registry tests). Example model download is opt-in with per-shard SHA-256. |
| `docs/decisions/0046-...md` | VERIFIED | Accepted; decision 10 holds the D-07 record table (all cells `pending operator run`, which is the honest state). |
| `tests/llm_gateway/` (13 modules + conftest) | VERIFIED | All run, no skips apart from the root-only permission case. |

### Key Link Verification

| From | To | Via | Status |
| ---- | -- | --- | ------ |
| six scripts | `LlmGateway` | `open_stage_gateway` then `complete_json(gateway, ...)` | WIRED |
| orchestrator | gateway validation | `_preflight_llm_gateways` in `run_all`/`run_phase`/`run_personal_study` before resume predicates | WIRED |
| orchestrator | child scripts | exactly two new env names forwarded (`LECTURE_STUDY_LLM_ADAPTER`, `LECTURE_STUDY_LLM_CONFIG`); pinned by `tests/test_cli.py` | WIRED |
| factory | egress policy | `require_remote_egress_allowed` before `resolve_trusted_executable` on the codex branch | WIRED |
| scripts | run record | `workspace/data/llm_runs/<lecture>/<stage>.json`, outside dashboard and delivery roots (asserted by test) | WIRED |
| `llm/` | `site/` | none (layering test) | CORRECTLY ABSENT |

### Data-Flow Trace (Level 4)

The end-to-end stub tests carry real data through: evidence pack on disk, prompt built by the script, stubbed binary stdout, `complete_json` parse and validation, ledger/draft/unit written, run record written. No static or hollow source found.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
| -------- | ------- | ------ | ------ |
| Targeted gateway and layering tests | `uv run pytest tests/llm_gateway tests/test_llm_layering.py -q -p no:cacheprovider` | 236 passed | PASS |
| Full suite | `uv run pytest -q -rs -p no:cacheprovider` | 2559 passed, 1 warning | PASS |
| Lint, format, types | `uv run ruff check .`, `ruff format --check .`, `mypy src` | clean; 235 files formatted; 126 source files | PASS |
| Provisioning script syntax and lint | `bash -n`, `shellcheck scripts/01_provision_engines.sh` | ok | PASS |
| ADR/doc checks of VALIDATION rows 06-11-01, 06-11-03, 06-12-01, 06-12-02 | the `rg` commands in 06-VALIDATION.md | all match | PASS |

### Probe Execution

No `scripts/*/tests/probe-*.sh` and no probe declared in any PLAN: not applicable.

### Anti-Patterns Found

None. `rg` for TBD/FIXME/XXX/TODO/HACK/PLACEHOLDER over every non-planning, non-docs file changed on the branch returned no match. No stub returns, no console-only handlers.

### Review and Security Evidence (read, not trusted)

- `06-REVIEW.md` iteration 3: 0 critical, 2 warnings, 9 info. WR-01 (single displaced-success slot in the run record) is accepted and documented in ADR 0046 section 7. WR-02 (macOS EPERM during process-group cleanup) was fixed in `c40a4aa` after the review.
- `06-SECURITY.md`: 47 register entries, 0 open, status verified.
- Round 3 delta read by the verifier (`git diff c1196dc 63dad05 -- src`): `config.py` widens the YAML exception tuple; `llama_cpp_adapter.py` maps scratch and prompt-file `OSError` to `LlmCallError` with type-name-only reasons; `process_lifecycle.py` signals the leader directly after a refused group kill and tolerates EPERM in the post-reap sweep; `local_lectures.py` maps `ValueError`/`RecursionError` to a typed error. Each narrows an escape path and fails closed; no new spawn, network or environment surface. This is the verifier's read, not a formal R0.4 re-run (human item 2).

### Deferred Items

None filtered. GW-03's engine confirmation is not deferred to a later phase; Phase 7's measured results will consume it but do not own it.

### Human Verification Required

1. **D-07 manual real-model run (accepted open item).**
   **Test:** `scripts/01_provision_engines.sh --with-example-llm-model`; copy `config/llm-gateway.example.yaml` to `config/llm-gateway.local.yaml`; with `LECTURE_STUDY_LLM_ADAPTER=llama-cpp` run stage 5 (`--pack-sequence 1`), 6 and 7 on one lecture; fill the ADR 0046 decision 10 record; mark follow-ups (a)-(e).
   **Expected:** exit 0 for each stage, run records report `llama-cpp`, no Codex process, every decision-4 flag accepted at the pinned commit; `HOME`, chat-template (`--single-turn` versus `-no-cnv`), first-shard load and token-count line format answered. Any item marked needed becomes a failed UAT item for `/gsd-plan-phase 6 --gaps`.
   **Why human:** no llama.cpp build or GGUF weights are available; GW-03 stays Pending until recorded. Optional thinking-style model row for OPEN-1 (D-25).

2. **R0.4 re-run on the Round 3 delta.**
   **Test:** `/gsd-code-review` and `/gsd-secure-phase` on `c1196dc..HEAD` (or the phase-final diff), or an explicit operator acceptance of the read above.
   **Expected:** no new blocking finding.
   **Why human:** the final review and audit predate four corrective commits; the global rule requires a re-run on the corrected diff, and accepting a substitute is the operator's call. Judged a WARNING, not a BLOCKER: the delta is small, tested, fully green and fail-closed.

3. **CI on the pushed phase-final commit.**
   **Test:** after the push, read the `ci` run for the commit.
   **Expected:** all jobs green, including `test` on macos-15.
   **Why human:** the branch is unpushed, so criterion 3 "in CI" is evidenced by workflow configuration plus an equivalent local macOS run, not by a CI result.

### Gaps Summary

No gaps. All four roadmap success criteria and requirements GW-01, GW-02, GW-04, GW-05 and GW-06 hold in the code, with the behavior-dependent truths (criteria 1 and 2) backed by passing behavioral tests rather than symbol presence. GW-03 is intentionally Pending: its contract is verified, its engine run is the operator's pending item.

Minor stale documentation, not goal-affecting: `.planning/PROJECT.md` line 35 still shows `- [ ] GW-05` unchecked while REQUIREMENTS.md marks it Complete (the VALIDATION regex accepts both); the ROADMAP checkbox for Phase 6 stays unchecked pending phase completion. `06-VALIDATION.md` was updated from this evidence: all 30 task rows green or resolved, Wave 0 items ticked except the D-07 manual-run record, OPEN-1 and OPEN-2 recorded as resolved (D-25, D-26), `status: validated`, `nyquist_compliant: false` (PARTIAL) until the manual run is recorded.

---

_Verified: 2026-10-14_
_Verifier: Claude (gsd-verifier)_
