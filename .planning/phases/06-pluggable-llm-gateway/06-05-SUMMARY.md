---
phase: 06-pluggable-llm-gateway
plan: 05
subsystem: llm-gateway
tags: [llm, factory, composition-root, egress, semantic-ledgers, run-record, pilot-migration]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "LlmGateway port, complete_json, CodexCliAdapter, LlamaCppAdapter (06-01, 06-02); GatewayConfiguration and record_llm_run (06-03); RemoteEgressPolicy and RegistryRemoteEgressPolicy.require_consistent_sources (06-04)"
provides:
  - "llm/factory.build_gateway: adapter selection, egress check before executable resolution, executable and model validation, no spawn"
  - "cli/llm_gateway_wiring.open_stage_gateway and preflight_stage_gateways (the only join of llm/ with the registry-backed policy)"
  - "scripts/generate_codex_semantic_ledgers.py on the gateway with its semantic.json run record (pilot migration; shape for 06-06, 06-07, 06-08)"
  - "ScriptedGateway test double and make_scripted_gateway fixture in tests/conftest.py"
affects: [06-06, 06-07, 06-08, 06-09, 06-11, 06-12]

actuals:
  tokens: 14000
  tasks: 3
  commits: 3
plan_head_before: 080efaed5e4357fab9899be8f457ba908b707abb
plan_head_after: 43cc850bd1a0ae771b1d12e9659a6fbba2e35705

tech-stack:
  added: []
  patterns:
    - "Script shape: validate lecture id and load inputs, open_stage_gateway, record_llm_run, recorder.track, complete_json with the domain validator inside the retry boundary"
    - "Egress check precedes executable resolution for the remote adapter (T-06-21), pinned by a resolver spy"
    - "Composition helpers call require_consistent_sources whenever codex is selected for the stage (OPEN-2 option A)"

key-files:
  created:
    - src/lecture_study/llm/factory.py
    - src/lecture_study/cli/llm_gateway_wiring.py
    - tests/llm_gateway/test_semantic_stage_gateway.py
    - tests/llm_gateway/test_factory.py
  modified:
    - scripts/generate_codex_semantic_ledgers.py
    - tests/conftest.py
    - tests/test_generate_codex_semantic_ledgers.py

key-decisions:
  - "build_gateway passes the llama model path as the configured string (not Path) so the adapter's URL check still sees the scheme separator"
  - "The composition helper calls RegistryRemoteEgressPolicy.require_consistent_sources() when the stage selects codex, so the OPEN-2 configuration error surfaces at preflight rather than only on a local-id lookup"
  - "The script opens the gateway before creating the output directory, so a refused or misconfigured run leaves no directory, ledger or sidecar behind"

patterns-established:
  - "Migrated script main() maps LlmGatewayError, LlmResponseContractError, LlmParsingError, FileNotFoundError and ValidationError to 'error: MESSAGE' on stderr and exit 1"

requirements-completed: [GW-01, GW-02, GW-04]

coverage:
  - id: D1
    description: "Stage 5 runs end to end through configuration, policy, factory, codex stub, complete_json and run record (calls 1, adapter codex)"
    requirement: GW-01
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_semantic_stage_gateway.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "Unflagged lecture refused before the codex executable is resolved; unknown adapter and missing llama model exit 1 with no stub call"
    requirement: GW-04
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_factory.py"
        status: pass
      - kind: integration
        ref: "tests/llm_gateway/test_semantic_stage_gateway.py"
        status: pass
    human_judgment: false
  - id: D3
    description: "Traceability validation retried once inside complete_json; wrong lecture id on both attempts raises LlmParsingError after two requests"
    requirement: GW-02
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_semantic_ledgers.py"
        status: pass
    human_judgment: false
  - id: D4
    description: "Script holds no process-spawning code (AST import boundary) and rejects a malformed lecture id before opening a gateway"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_semantic_ledgers.py"
        status: pass
    human_judgment: false

duration: 8min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 05: Factory, composition helpers and stage 5 pilot migration Summary

**One adapter factory with egress-before-resolution for codex, a CLI-side composition module joining it with the registry-backed policy, and stage 5 semantic ledgers migrated onto `complete_json` with a `semantic.json` run record.**

## Performance

- **Duration:** about 8 min
- **Completed:** 2026-10-14
- **Tasks:** 3 (tracer, auto, auto)
- **Files created:** 4, modified: 3

## Accomplishments

- `llm/factory.build_gateway`: for codex it calls `require_remote_egress_allowed` first, then `resolve_trusted_executable` (override text wins over `codex.executable`); for llama-cpp it resolves both executables and lets the adapter constructor validate the model path and family. `OSError` from resolution becomes `GatewayConfigurationError` naming the stage, adapter and configured text, never any prompt. Nothing is spawned.
- `cli/llm_gateway_wiring`: `open_stage_gateway` and `preflight_stage_gateways` load the configuration once, build `RegistryRemoteEgressPolicy` from `remote_llm_allowed_lecture_ids`, and run `require_consistent_sources()` when codex is selected. The `llm` package still imports neither `cli` nor `study_materials` (layering test green).
- `scripts/generate_codex_semantic_ledgers.py`: process code, HOME restoration and the scratch-directory logic are gone (adapter owns them). `_run` validates the lecture id, loads packs, opens the gateway, then records the run; `_generate_ledger` is a single `complete_json` call with `_validate_traceability` as `validate` and `retry_on=(LlmResponseContractError, LlmParsingError)`. New `--llm-runs-root`; `--codex-executable` now defaults to None (overrides the configuration). Docstring rewritten (ADR 0046 gateway, ADR 0035 history kept, no claim of running instead of the local worker).
- Tests: `ScriptedGateway` shared double; end-to-end and refusal tests against the codex stub; factory and preflight fail-closed tests (resolver spy records zero calls for a refused lecture; llama stub call logs stay empty on construction); rewritten unit tests including the wrong-lecture-id regression and an AST source-boundary test.

## Task Commits

1. **Task 1 (tracer): end-to-end stage 5 through factory, wiring, complete_json and run record** - `dd32bc9` (feat)
2. **Task 2: fail-closed factory paths, preflight and script re-validation** - `e4dbf67` (test)
3. **Task 3: ScriptedGateway double and semantic unit tests on the gateway shape** - `43cc850` (test)

Tracer gate: `<verify>` plus the acceptance suites (`tests/test_cli.py`, `tests/test_llm_layering.py`) passed after Task 1 (94 passed) before expanding. Between Task 1 and Task 3 the legacy `tests/test_generate_codex_semantic_ledgers.py` still referenced the removed helper; it is rewritten in Task 3 as the plan sequences, and the full suite is green at the plan head.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Model path must reach the adapter as a string**
- **Found during:** Task 1 (writing `_build_llama_cpp_adapter`)
- **Issue:** wrapping `settings.model_path` in `Path(...)` collapses `://` and would defeat the adapter's URL rejection (D-09).
- **Fix:** pass the configured string through unchanged; covered by the `model-url` factory test.
- **Files modified:** `src/lecture_study/llm/factory.py`
- **Commit:** `dd32bc9`

Additions inside plan scope: `require_consistent_sources()` is invoked by the composition helper for codex stages (06-04 hand-off); the script creates its output directory only after the gateway opens.

## Authentication Gates

None.

## Issues Encountered

None. No model, package or weight file was downloaded; every adapter ran against stub executables.

## Known Stubs

None.

## Threat Flags

None. T-06-21 (egress before resolution, resolver spy), T-06-22 (script exit 1 with no call for bogus adapter, missing model and unflagged lecture), T-06-23 (`resolve_trusted_executable` on every configured executable) and T-06-24 (`validate_traceability` inside the retry boundary, regression test) are covered by tests.

## Verification

- `uv run pytest tests/test_generate_codex_semantic_ledgers.py tests/llm_gateway -q`: 168 passed
- `uv run pytest -q -rs`: 2446 passed
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean

## Next Phase Readiness

06-06, 06-07 and 06-08 copy the script shape from `scripts/generate_codex_semantic_ledgers.py` and use `ScriptedGateway` for unit tests. The CLI preflight (06-07 or later) calls `preflight_stage_gateways`.

## Self-Check: PASSED

All seven key files exist on disk; commits `dd32bc9`, `e4dbf67` and `43cc850` are ancestors of HEAD; `commits: 3` measured from the persisted ledger.
