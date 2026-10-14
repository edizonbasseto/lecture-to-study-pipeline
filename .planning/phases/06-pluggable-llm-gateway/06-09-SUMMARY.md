---
phase: 06-pluggable-llm-gateway
plan: 09
subsystem: llm-gateway
tags: [llm, orchestrator, preflight, env-allowlist, success-criteria, layering]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "preflight_stage_gateways and open_stage_gateway (06-05); migrated stage 6 and 7 scripts (06-06); stage 13, 14 and 18 scripts (06-07, 06-08); ADAPTER_OVERRIDE_ENV and CONFIG_PATH_ENV (06-03)"
provides:
  - "PhaseSpec.llm_stages declared on registry rows 5, 6, 7, 13 and 14 (never derived from the phase name)"
  - "_preflight_llm_gateways called by run_all, run_phase and run_personal_study before the first phase; LlmGatewayError becomes PipelineExecutionError naming the lecture"
  - "_LLM_PHASE_ENV_KEYS (the eight existing names plus exactly LECTURE_STUDY_LLM_ADAPTER and LECTURE_STUDY_LLM_CONFIG) and _llm_phase_environment"
  - "Stub harness rules mode (rules-NAME.json, first substring match, rules not consumed) and the tripwire_codex_on_path fixture"
  - "Success criterion 1 and 2 evidence tests; central GW-01 six-script import boundary"
affects: [06-10, 06-11, 06-12]

actuals:
  tokens: 9100
  tasks: 2
  commits: 2
plan_head_before: 68167005fc5c53da26337e8e4849d21548b1fdfc
plan_head_after: 3439dda9686c27a8a88588b7ea9fdb9f0df0efc1

tech-stack:
  added: []
  patterns:
    - "Explicit per-row llm_stages field on PhaseSpec; fake registries built without it trigger no preflight"
    - "Preflight takes the ordered unique union of the selected specs' stages, independent of resume predicates"
    - "Prompt-content stub rules let one scripted llama pair answer a multi-stage, multi-process run"

key-files:
  created:
    - tests/llm_gateway/test_stages_without_codex.py
    - tests/llm_gateway/test_preflight.py
  modified:
    - src/lecture_study/cli/orchestrator.py
    - tests/llm_gateway/conftest.py
    - tests/test_cli.py
    - tests/test_llm_layering.py

key-decisions:
  - "llm_stages is a trailing PhaseSpec field set only on the real rows, so phase 12 (named flashcards) can never be mistaken for stage 18 and test registries from fake specs need no change"
  - "The preflight covers every LLM stage of the entry point's phase selection regardless of resume predicates, so a misconfiguration fails before any work; in run_phase it sits after the certificate block and before the resume check"
  - "The child allowlist grows by two explicit names with no prefix widening; run_bounded_process still re-applies its blocklist"
  - "The tracer-expansion gate was satisfied by re-running the automated verify (end-to-end, both tests green) because the verify is fully automated"

patterns-established:
  - "Integration tests drive the real orchestrator and real child scripts against stub executables, with a tripwire codex first on PATH asserting zero Codex calls"

requirements-completed: [GW-04, GW-02, GW-01, GW-06]

coverage:
  - id: D1
    description: "Stages 5, 6 and 7 run through the orchestrator on llama-cpp selected by YAML, and by LECTURE_STUDY_LLM_ADAPTER over a codex YAML, with the tripwire codex recording zero calls and every run record reporting adapter llama-cpp"
    requirement: GW-04
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_stages_without_codex.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "Unknown adapter, missing llama executable and a lecture not flagged for remote use exit 1 through cli main before any phase predicate, phase runner or process spawn"
    requirement: GW-04
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_preflight.py"
        status: pass
    human_judgment: false
  - id: D3
    description: "Preflight stage sets: run_all [semantic, drafting, critic, exams, podcast], run_phase 7 [critic, drafting], run_personal_study [exams, podcast]; row 12 has no stages"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_preflight.py"
        status: pass
      - kind: unit
        ref: "tests/test_cli.py::TestPhaseRegistry"
        status: pass
    human_judgment: false
  - id: D4
    description: "Child environment contains exactly the allowlist plus the two gateway names; OPENAI_API_KEY and HTTPS_PROXY are not forwarded; command shape, failure logs directory and timeouts unchanged"
    requirement: GW-02
    verification:
      - kind: unit
        ref: "tests/test_cli.py::TestLlmPhaseEnvironment"
        status: pass
      - kind: unit
        ref: "tests/test_cli.py::TestCodexScriptPhases"
        status: pass
    human_judgment: false
  - id: D5
    description: "All six generate_codex_* scripts import llm_gateway_wiring and none imports tempfile, subprocess, process_lifecycle or process_environment; the script list is pinned to the files on disk"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/test_llm_layering.py"
        status: pass
    human_judgment: false

duration: 14min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 09: Orchestrator preflight, gateway env forwarding and criteria 1 and 2 Summary

**The orchestrator now declares the gateway stages of each phase, preflights them in all three entry points before the first phase, forwards exactly two new environment names to the child scripts, and proves success criteria 1 and 2 end to end.**

## Performance

- **Duration:** about 14 min
- **Completed:** 2026-10-14
- **Tasks:** 2 (tracer, auto)
- **Files created:** 2, modified: 4

## Accomplishments

- `orchestrator.py`: `PhaseSpec.llm_stages` (rows 5 semantic, 6 drafting, 7 critic plus drafting, 13 exams, 14 podcast); `_preflight_llm_gateways(specs, lecture_id)` builds the ordered unique stage union and converts `LlmGatewayError` into `PipelineExecutionError` chained from the error; wired into `run_all` (whole registry, right after lecture-id validation), `run_phase` (single spec, after the certificate block and before the resume check) and `run_personal_study` (specs in `PERSONAL_STUDY_PHASE_NUMBERS`). The module docstring now describes the gateway port and the preflight. `_CODEX_PHASE_FAILURE_LOGS_DIR`, the timeout, the output budget and the child command are untouched.
- Environment: `_CODEX_PHASE_ENV_KEYS` became `_LLM_PHASE_ENV_KEYS` with `ADAPTER_OVERRIDE_ENV` and `CONFIG_PATH_ENV` added by name; `_codex_phase_environment` became `_llm_phase_environment`. An exact-set test pins the ten names, and a negative test shows `OPENAI_API_KEY` and `HTTPS_PROXY` are not forwarded.
- Stub harness: rules mode (`rules-NAME.json`, first substring match on the `-f` prompt file or the last argv element, rules not consumed, exit 98 when nothing matches) and the `tripwire_codex_on_path` fixture.
- Criterion 1: `test_stages_without_codex.py` runs phases 5, 6 and 7 through the real orchestrator and the real child scripts. One test selects llama-cpp by YAML; the other selects codex in YAML and overrides it with `LECTURE_STUDY_LLM_ADAPTER=llama-cpp`, which only works because the override and the configuration path reach the children. Ledger, draft and unit files exist, `semantic.json`, `drafting.json` and `critic.json` all report adapter llama-cpp, and the tripwire codex has zero calls.
- Criterion 2: `test_preflight.py` drives `cli main run-all` with an unknown adapter, a missing llama executable, and a reserved local id with an empty registry under the default codex adapter; each exits 1 with zero resume-predicate calls, zero runner calls and zero spawn calls, and the last logs `remote_llm_allowed`.
- Layering: `test_llm_layering.py` gains a test parametrized over the six `generate_codex_*` scripts (gateway wiring imported, no process-spawning or scratch-directory module) and a test that pins the script list to the files on disk.

## Task Commits

1. **Task 1 (tracer): orchestrator preflight, env forwarding and stages 5-7 without codex** - `387b4ea` (feat)
2. **Task 2: criterion 2 through cli main and the six-script boundary** - `3439dda` (test)

Tracer gate: the Task 1 `<verify>` and the acceptance suites (`tests/test_personal_study_mode.py`, `tests/test_cli.py`, `tests/llm_gateway`, `tests/test_llm_layering.py`) passed (268 tests) before Task 2. Two mutation checks confirmed the tests are not vacuous: removing the two names from the allowlist failed both integration tests, and removing the `run_all` preflight call failed four of the criterion 2 tests.

## Deviations from Plan

None - plan executed as written. The `tests/test_cli.py` renames and the new environment and registry tests landed in the Task 1 commit instead of Task 2, because renaming the constant would otherwise have left the suite red between the two commits.

## Authentication Gates

None.

## Issues Encountered

None. Full-suite run: 2494 passed, no failures, so no flaky test needed classification. No model, package or weight file was downloaded; llama.cpp and Codex were stub executables only. The committed diff contains no absolute home path.

## Known Stubs

None.

## Threat Flags

None. T-06-34 (explicit two-name allowlist plus negative forwarding test), T-06-35 (preflight in all three entry points, criterion 2 tests through `cli main`), T-06-36 (explicit `llm_stages`, row 12 pinned empty) and T-06-37 (failure capture and `tmp/codex_phase_failures/` unchanged, existing failure tests kept) are covered by tests.

## Verification

- `uv run pytest tests/llm_gateway -q -rs`: passed, including the integration tests
- `uv run pytest -q -rs`: 2494 passed
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean
- Leak-gate and gitleaks hooks passed on both commits

## Next Phase Readiness

06-10 and 06-11 can rely on `PhaseSpec.llm_stages` and the two forwarded names; ADR 0046 (06-11) should record that the orchestrator preflights the stages declared per phase row and forwards exactly `LECTURE_STUDY_LLM_ADAPTER` and `LECTURE_STUDY_LLM_CONFIG` to children. Stage 18 remains outside the orchestrator and keeps its script-level validation.

## Self-Check: PASSED

All six key files exist on disk; commits `387b4ea` and `3439dda` are ancestors of HEAD; `commits: 2` measured from the persisted ledger.
