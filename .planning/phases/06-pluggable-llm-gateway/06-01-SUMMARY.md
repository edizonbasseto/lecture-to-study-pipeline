---
phase: 06-pluggable-llm-gateway
plan: 01
subsystem: llm-gateway
tags: [llm, gateway, port-adapter, codex-cli, subprocess, pytest, stub-executables]

requires:
  - phase: 05-semantic-ledgers
    provides: "ops.process_lifecycle, ops.process_environment and ops.llm_response_contract helpers reused by the adapter"
provides:
  - "lecture_study.llm package: raw-text LlmGateway port, request/response/identity types, typed error hierarchy"
  - "CodexCliAdapter preserving the byte-identical codex exec argv, scratch cwd and sanitized environment, with an argv budget guard (D-23)"
  - "complete_json: shared parse plus single bounded retry for any gateway"
  - "Stub-executable harness and adapter-parametrized contract suite (codex registered)"
  - "AST import-boundary test for llm/, ops/ and site/"
affects: [06-02, 06-03, 06-04, 06-05, 06-06, 06-07, 06-08, 06-09, 06-10, 06-11, 06-12]

actuals:
  tokens: 9916
  tasks: 3
  commits: 3
plan_head_before: 861094659443363e79a8783e0f9f7c9884344a5c
plan_head_after: 8e8283c7bb15a3ee48ed336c23127cd7463e1e33

tech-stack:
  added: []
  patterns:
    - "Port in the inner module, adapters outside; errors built from attributes only, never from prompt or argv"
    - "Real stub executables (shebang plus test interpreter) with a JSON-lines call log instead of patching subprocess"
    - "Failures raised from None so prompt-bearing exception chains never reach tracebacks"

key-files:
  created:
    - src/lecture_study/llm/__init__.py
    - src/lecture_study/llm/gateway.py
    - src/lecture_study/llm/codex_adapter.py
    - src/lecture_study/llm/json_completion.py
    - tests/llm_gateway/conftest.py
    - tests/llm_gateway/test_codex_adapter.py
    - tests/llm_gateway/test_json_completion.py
    - tests/llm_gateway/test_gateway_contract.py
    - tests/test_llm_layering.py
  modified: []

key-decisions:
  - "Argv budget is measured with a representative scratch path (system temp dir plus the stage prefix plus 8 placeholder characters) so the guard runs before the scratch directory exists"
  - "complete_json treats every LlmGatewayError as non-retryable even when retry_on would match it (D-22)"
  - "llm/__init__.py re-exports the port, errors and complete_json but no adapter, keeping the inner package surface adapter-free"

patterns-established:
  - "StubControl.script(name, steps) / calls(name): scripted child behaviour plus an inspectable call log, reusable by the llama adapter in 06-02"
  - "gateway_harness parametrized fixture: contract cases written once, run per adapter"

requirements-completed: [GW-01, GW-02, GW-06]

coverage:
  - id: D1
    description: "LlmGateway port, value types and typed error hierarchy in lecture_study.llm.gateway"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_json_completion.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "CodexCliAdapter with byte-identical argv, scratch cwd, sanitized environment, bounded process and argv budget guard"
    requirement: GW-02
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_codex_adapter.py"
        status: pass
    human_judgment: false
  - id: D3
    description: "complete_json shared parse and single bounded retry"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_json_completion.py"
        status: pass
    human_judgment: false
  - id: D4
    description: "Adapter-parametrized contract suite and import-boundary guard"
    requirement: GW-06
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_gateway_contract.py"
        status: pass
      - kind: unit
        ref: "tests/test_llm_layering.py"
        status: pass
    human_judgment: false

duration: 10min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 01: LLM gateway core and Codex adapter Summary

**Raw-text `LlmGateway` port with typed errors, a Codex CLI adapter that keeps today's `codex exec` call byte for byte (scratch cwd, read-only sandbox, credential-free environment, argv budget guard), a shared `complete_json` parse-plus-one-retry helper, and a parametrized contract suite with an import-boundary guard.**

## Performance

- **Duration:** about 10 min of execution, plus a pause at the first commit while the protected-branch guard was resolved by the orchestrator
- **Started:** 2026-10-14
- **Completed:** 2026-10-14
- **Tasks:** 3
- **Files created:** 9

## Accomplishments

- Port and errors (`gateway.py`): `Stage`, `AdapterName`, `LlmRequest` (prompt excluded from repr, positive finite timeout enforced), `LlmResponse`, `GatewayIdentity`, `LlmGateway` Protocol, and `LlmGatewayError` with four subclasses whose messages derive from stored attributes only.
- `CodexCliAdapter`: argv `exec --skip-git-repo-check --sandbox read-only -C SCRATCH -o SCRATCH/response.txt PROMPT` asserted exactly against a real stub process; the child cwd is a fresh temporary directory distinct from the repository root; environment is the sanitized allowlist plus `HOME`; timeout, output-cap, start-failure and missing-response failures all map to `LlmCallError` raised `from None`; an argv plus environment cost above the budget (default 900 KiB) raises `PromptTooLargeError(unit="bytes")` before any scratch directory or process exists.
- `complete_json`: first attempt parses and validates; a `retry_on` failure triggers exactly one retry with the original prompt plus a `Retry feedback:` section (bounded to 2 KiB, never replaying model output) and `is_retry=True`; gateway errors propagate without retry; the second failure propagates.
- Test harness: `write_stub_executable`, `StubControl`, `codex_stub`; 10 adapter tests, 8 `complete_json` tests, 9 contract cases, 8 layering tests.

## Task Commits

1. **Task 1 (tracer): port and Codex adapter end to end** - `c02383b` (feat)
2. **Task 2: failure modes and argv budget guard tests** - `c57c477` (test)
3. **Task 3: complete_json, contract suite, layering guard** - `8e8283c` (feat)

## Verification

- `uv run pytest tests/llm_gateway tests/test_llm_layering.py -q`: 35 passed
- `uv run pytest -q -rs`: 2297 passed, no existing test changed
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean
- Tracer verified end-to-end before expansion (auto verify re-run green)

## Deviations from Plan

### Auto-fixed Issues

None.

### Scope notes

- **Task 1/2 split.** The complete adapter (all failure mappings and the argv budget guard) was written during Task 1, so commit `c02383b` contains the production code described by Task 2 and `c57c477` contains only Task 2's tests. The coordinator approved this split. The 7 Task 2 behaviours are all asserted (10 adapter tests in total, Task 1 contributed 3).
- **Extra stub log key.** The stub call log also records `cwd_entries` (directory listing at start) so the contract suite can prove the child starts in an empty directory; the plan listed four keys.
- **Extra tests.** A missing-executable case (`OSError` start failure) and a zero-exit-without-response-file case were added to cover two reason strings the plan specifies but did not enumerate as tests; scanner self-tests were added to the layering file so the guard cannot pass vacuously.

## Authentication Gates

None.

## Issues Encountered

- The first commit was blocked by the protected-branch guard (work started on `main`, no `git.allow_default_branch_commits` override). The orchestrator created and switched to branch `gsd/phase-06-pluggable-llm-gateway`; all three commits are on that branch. Nothing was pushed.

## Known Stubs

None.

## Threat Flags

None. The trust boundaries in the plan threat model (T-06-01 to T-06-05) are covered by tests: prompt-free error messages and suppressed exception chain, credential-free child environment, scratch cwd outside the repository, argv budget guard before spawn, and single bounded retry that never replays model bytes.

## Next Phase Readiness

Plan 06-02 can register a `llama_cpp` param in `gateway_harness` and reuse `StubControl`; the port, errors, `complete_json` and layering test are in place. No script was touched.

## Self-Check: PASSED

All nine key files exist on disk; commits `c02383b`, `c57c477` and `8e8283c` are ancestors of HEAD; `commits: 3` measured from the persisted ledger.
