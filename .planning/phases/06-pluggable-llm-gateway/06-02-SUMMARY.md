---
phase: 06-pluggable-llm-gateway
plan: 02
subsystem: llm-gateway
tags: [llm, gateway, llama-cpp, llama-completion, llama-tokenize, subprocess, contract-tests, no-egress]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "Plan 06-01: LlmGateway port, typed errors, CodexCliAdapter, complete_json, stub harness and contract suite"
provides:
  - "LlamaCppAdapter: one bounded llama-completion process per call, prompt through a file, exact llama-tokenize preflight, no network egress surface"
  - "StageBudget and DEFAULT_STAGE_BUDGETS for the six stages (ADR 0046 values, RESEARCH assumption A4)"
  - "bounded_call.run_adapter_process and describe_process_failure shared by both adapters"
  - "Llama stub binaries with a download/listener tripwire and the llama_stubs fixture"
  - "Contract suite parametrized over codex and llama_cpp"
affects: [06-03, 06-04, 06-05, 06-06, 06-07, 06-08, 06-09, 06-11, 06-12]

actuals:
  tokens: 9063
  tasks: 3
  commits: 3
plan_head_before: 4cc2324526bbe898ec6de544284539fdefc9cdd4
plan_head_after: 163a00d35e467ecf393b78c18fbd04eaed98aa41

tech-stack:
  added: []
  patterns:
    - "Shared bounded spawn that maps every process failure to LlmCallError raised from None with digest-only reasons"
    - "Prompt in a scratch file, never in argv; child cwd is a separate empty subdirectory of the scratch directory"
    - "Stub binaries as tripwires: exit 3 on any download, listener, inline-prompt flag, URL argument or non-offline HF_/LLAMA_ARG_ variable"
    - "Token preflight with PromptBudget before generation; refusal raises PromptTooLargeError, never truncates"

key-files:
  created:
    - src/lecture_study/llm/bounded_call.py
    - src/lecture_study/llm/llama_cpp_adapter.py
    - tests/llm_gateway/test_llama_cpp_adapter.py
  modified:
    - src/lecture_study/llm/codex_adapter.py
    - tests/llm_gateway/conftest.py
    - tests/llm_gateway/test_gateway_contract.py

key-decisions:
  - "Reasoning off is the generic --reasoning off flag on every stage and attempt; no per-family mechanism (OPEN-1, operator pre-answered)"
  - "Child cwd is a dedicated empty work subdirectory, the prompt file sits one level above it, so the shared 'fresh empty directory' contract holds for llama"
  - "Model path accepted as Path or str so the literal '://' URL check is meaningful (a Path would collapse '//')"
  - "Stage budgets must cover every Stage or construction raises GatewayConfigurationError"
  - "End-of-generation marker is removed only when it is the last non-whitespace text; output cut by the -n cap is returned unchanged"

patterns-established:
  - "Adapter-neutral gateway_harness with prepare_oversized_prompt, model_calls and prompts"
  - "StubControl.call_names() for asserting cross-binary call order"

requirements-completed: [GW-03, GW-06]

coverage:
  - id: D1
    description: "LlamaCppAdapter with the D-17 flag set, prompt-by-file, offline-only environment and validated local model path"
    requirement: GW-03
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_llama_cpp_adapter.py"
        status: pass
    human_judgment: true
    rationale: "Behaviour is proven against stub binaries only; the real llama.cpp flag acceptance, Metal needing HOME and split-GGUF loading are checked by the operator's D-07 manual run (06-11)."
  - id: D2
    description: "Exact token preflight refuses oversized prompts before llama-completion is spawned, naming stage and sizes"
    requirement: GW-03
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_llama_cpp_adapter.py#test_oversized_prompt_is_refused_before_completion_is_spawned"
        status: pass
      - kind: integration
        ref: "tests/llm_gateway/test_gateway_contract.py#test_oversized_prompt_raises_before_any_model_call"
        status: pass
    human_judgment: false
  - id: D3
    description: "Retry-only repeat penalty (1.3 / 256) per ADR 0034"
    requirement: GW-03
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_gateway_contract.py#test_llama_cpp_retry_reaches_the_completion_binary_with_repeat_penalty"
        status: pass
    human_judgment: false
  - id: D4
    description: "One contract suite passes for codex and llama_cpp through stub binaries in CI"
    requirement: GW-06
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_gateway_contract.py"
        status: pass
    human_judgment: false

duration: 5 min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 02: Local llama.cpp adapter and two-adapter contract suite Summary

**LlamaCppAdapter runs `llama-completion` once per call from a prompt file after an exact `llama-tokenize` budget check, with an offline-only environment and no download or listener flag anywhere, and the gateway contract suite now passes unchanged for both codex and llama_cpp through stub binaries.**

## Performance

- **Duration:** about 5 min of execution
- **Started:** 2026-10-14T21:59:31Z
- **Completed:** 2026-10-14T22:03:38Z (code), SUMMARY after
- **Tasks:** 3 (Task 1 tracer)
- **Files:** 3 created, 3 modified

## Accomplishments

- `bounded_call.run_adapter_process` and `describe_process_failure` extracted from the Codex adapter; `CodexCliAdapter` now delegates to them with unchanged behaviour (all 06-01 tests still pass).
- `LlamaCppAdapter.complete`: writes the prompt as UTF-8 to a scratch file, runs `llama-tokenize -m MODEL -f FILE --no-escape --ids --show-count`, parses `Total number of tokens: N`, checks `PromptBudget(...).fits`, raises `PromptTooLargeError(stage, required, limit, "tokens")` before spawning, then runs `llama-completion` with `-m -f -c -n --temp --single-turn --no-display-prompt --no-escape --simple-io --offline --reasoning off`. The trailing ` [end of text]` marker is removed so the bytes handed back are the pure generation.
- Child environment is the sanitized allowlist with every `LLAMA_ARG_*` and `HF_*` key removed, plus `LLAMA_ARG_OFFLINE=1`, and no `HOME`.
- Model path must be an existing regular file (no `://`, no `org/model`, no directory); it is stored resolved and absolute so `-m` never starts with `-`. Model family must match `^[a-z0-9][a-z0-9._-]*$`.
- `--repeat-penalty 1.3 --repeat-last-n 256` are appended only when `request.is_retry` is true.
- Stubs act as tripwires (exit 3 on forbidden flags, URLs or download environment) and the tripwire itself is tested against every forbidden flag so it cannot silently go dead.
- `gateway_harness` is `params=["codex", "llama_cpp"]`; every existing contract case runs for both, plus a shared oversized-prompt case and one llama-only retry-flag case.

## Task Commits

1. **Task 1 (tracer): tokenize preflight, llama-completion spawn, shared bounded call** - `5d53e2d` (feat)
2. **Task 2: no-egress guards, model-path validation, retry-only repeat penalty, size refusal tests** - `1f30046` (test)
3. **Task 3: shared contract suite over both adapters** - `163a00d` (test)

The tracer gate was satisfied by re-running Task 1's `<verify>` plus mypy, ruff and the layering test end to end before expansion.

## Files Created/Modified

- `src/lecture_study/llm/bounded_call.py` - shared bounded spawn and failure description
- `src/lecture_study/llm/llama_cpp_adapter.py` - adapter, `StageBudget`, `DEFAULT_STAGE_BUDGETS`, retry constants
- `src/lecture_study/llm/codex_adapter.py` - delegates process handling to `run_adapter_process`
- `tests/llm_gateway/conftest.py` - llama stubs, tripwire, `llama_stubs` fixture, `call_names()`
- `tests/llm_gateway/test_llama_cpp_adapter.py` - 52 adapter-specific cases
- `tests/llm_gateway/test_gateway_contract.py` - two-adapter harness

## Decisions Made

See `key-decisions`. In short: generic `--reasoning off` only (OPEN-1 pre-answered by the operator), a dedicated empty work directory so the shared empty-cwd contract holds, and a marker strip that only fires when the marker is the final text.

## Deviations from Plan

None that change behaviour. Three small notes:

- Task 1 shipped the full adapter, including the model-path validation, family validation and retry flags that the plan text lists under Task 2. Splitting them would have meant writing a deliberately incomplete adapter; Task 2 therefore carries the tests for those guards. All Task 2 acceptance criteria were checked after its commit.
- `model_path` is typed `Path | str` (plan interface says `Path`). `Path` collapses `//`, so the literal `://` URL rejection could never fire on a `Path`; the wider type keeps it meaningful and stays compatible with every `Path` caller.
- `StageBudget` validates its own values and the constructor rejects a budget table that omits a stage; neither is in the plan and both are cheap correctness guards (Rule 2).

**Total deviations:** 0 auto-fixed bugs, 2 small additions (Rule 2), 1 task-boundary shift.

## Authentication Gates

None.

## CI

`.github/workflows/ci.yml` needs no change: it runs `uv run pytest -q -rs`, and the llama stubs are created inside the test run (no binary or model is fetched).

## Verification Results

- `uv run pytest tests/llm_gateway -q -rs`: 91 passed, no skips; collected ids include both `[codex]` and `[llama_cpp]`
- `uv run pytest tests/test_llm_layering.py -q`: 8 passed (bounded_call and llama_cpp_adapter are outer modules)
- `uv run pytest -q -rs`: 2370 passed, 1 warning (pre-existing starlette deprecation)
- `uv run ruff check .` and `uv run ruff format --check .`: clean (217 files)
- `uv run mypy src`: no issues in 118 source files, no `type: ignore`
- No absolute home paths in the new or changed files.

## Follow-ups and Notes

- Proof is against stub binaries by design. The real-model run (D-07) remains the operator's pending item and should confirm: llama.cpp accepts every flag at the pinned commit, Metal works without `HOME` (RESEARCH A3, otherwise ADR 0046 records the change), split GGUF loading from the first shard (A9), and the real `Total number of tokens:` line format.
- The phase-level Code Review and Code Secure workflows (R0.4) are phase-final and belong to the orchestrator, not to this plan.
- Stage budget values are RESEARCH assumption A4; ADR 0046 is to record them.

## Known Stubs

None.

## Threat Flags

None. The surface introduced is the local process boundary already covered by T-06-06 through T-06-11, each mitigated and asserted in tests (argv, environment, model path, `--no-escape`, `-n` cap with bounded process, token preflight, `llama-completion` only).

## Self-Check: PASSED

- `src/lecture_study/llm/bounded_call.py`, `src/lecture_study/llm/llama_cpp_adapter.py` and `tests/llm_gateway/test_llama_cpp_adapter.py` exist.
- Commits `5d53e2d`, `1f30046` and `163a00d` are ancestors of HEAD; measured commit count 3.
