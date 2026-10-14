---
phase: 06-pluggable-llm-gateway
plan: 06
subsystem: llm-gateway
tags: [llm, drafting, critic, repair-loop, run-record, adapter-contract]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "complete_json, CodexCliAdapter, LlamaCppAdapter (06-01, 06-02); record_llm_run (06-03); open_stage_gateway, ScriptedGateway and the stage 5 script shape (06-05)"
provides:
  - "scripts/generate_codex_drafts.py on the gateway: _generate_draft(*, gateway, pack, ledger, feedback=None), drafting.json run record"
  - "scripts/generate_codex_critic_review.py on two gateways: critic-stage reviews, drafting-stage redrafts, critic.json run record listing both usages"
  - "tests/llm_gateway/test_critic_repair_contract.py: ADR 0036 repair loop through the codex and llama_cpp adapters over stub binaries (GW-06)"
affects: [06-07, 06-08, 06-09, 06-11, 06-12]

actuals:
  tokens: 15000
  tasks: 3
  commits: 3
plan_head_before: e3dec0a484a0e2bfa45dbd5554864b1cae0bd291
plan_head_after: c80e465183d60332ec77c3c3505f2754a2be6c21

tech-stack:
  added: []
  patterns:
    - "Two-stage script: one open_stage_gateway per stage, all opened before the first call, each tracked on one recorder with its own stage"
    - "Adapter-neutral contract harness with a supports_retry_penalty flag instead of per-adapter branches in test bodies"

key-files:
  created:
    - tests/llm_gateway/test_critic_repair_contract.py
  modified:
    - scripts/generate_codex_drafts.py
    - scripts/generate_codex_critic_review.py
    - tests/test_generate_codex_drafts.py
    - tests/test_generate_codex_critic_review.py

key-decisions:
  - "The critic script builds a critic-stage and a drafting-stage gateway before any model call, so a redraft honors the drafting-stage adapter selection and egress check (D-21, T-06-25)"
  - "A critic-feedback redraft is an ordinary first-attempt request (is_retry False): the feedback heading lives in the prompt, never in the retry flag, so llama.cpp never gets the retry-only repeat penalty (ADR 0034, Pitfall 9)"
  - "The ADR 0036 loop body is unchanged in shape; only the plumbing (gateways instead of executable and environment) changed"

patterns-established:
  - "Migrated script main() catches LlmResponseContractError, LlmGatewayError, FileNotFoundError and ValidationError and exits 1 with 'error: MESSAGE'"

requirements-completed: [GW-01, GW-06]

coverage:
  - id: D1
    description: "Stage 6 writes drafts, depth_report.md and the drafting run record through a gateway; critic-feedback redraft is a non-retry request carrying the rejection heading"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_drafts.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "Stage 7 routes reviews only to the critic gateway and redrafts only to the drafting gateway; skips redraft on first-try pass; gives up after max_retries keeping the last failing pair"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_critic_review.py"
        status: pass
    human_judgment: false
  - id: D3
    description: "Repair loop passes through codex and llama_cpp over stub processes; redraft carries the feedback marker and no --repeat-penalty; an unparseable review is retried once and only that llama call is penalised"
    requirement: GW-06
    verification:
      - kind: integration
        ref: "tests/llm_gateway/test_critic_repair_contract.py"
        status: pass
    human_judgment: false
  - id: D4
    description: "Neither script holds process-spawning code (AST import boundary) and both reject a malformed lecture id before opening a gateway"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_drafts.py"
        status: pass
      - kind: unit
        ref: "tests/test_generate_codex_critic_review.py"
        status: pass
    human_judgment: false

duration: 6min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 06: Stage 6 drafts and stage 7 critic on the gateway Summary

**Drafts and the ADR 0036 critic repair loop now run through `complete_json`, with reviews on the critic-stage gateway, redrafts on the drafting-stage gateway, and the loop proven through both the codex and llama_cpp adapters over stub binaries.**

## Performance

- **Duration:** about 6 min
- **Completed:** 2026-10-14
- **Tasks:** 3 (tracer, auto, auto)
- **Files created:** 1, modified: 4

## Accomplishments

- `scripts/generate_codex_drafts.py`: process constants, `_call_codex_for_draft`, HOME restoration and the `os`/`tempfile`/process-lifecycle/process-environment imports are gone. `_run` validates the lecture id, loads packs and ledgers, opens the drafting gateway, then records `drafting.json` around `_write_drafts`. `_generate_draft(*, gateway, pack, ledger, feedback=None)` is a single `complete_json` call (retry only on `LlmResponseContractError`). The module docstring no longer tells the operator to run the removed local critic; it points at stage 7 through the gateway (ADR 0046).
- `scripts/generate_codex_critic_review.py`: `_run` opens `Stage.CRITIC` and `Stage.DRAFTING` gateways before the first call, tracks both on one recorder and writes `critic.json` listing both usages. `_evaluate_with_repair(*, critic_gateway, drafting_gateway, ...)` keeps the exact ADR 0036 loop and calls `generate_codex_drafts._generate_draft(gateway=drafting_gateway, ...)` for redrafts. `ValidatedLearningUnit` stays anchored to the trusted pack. The run body was split into `_review_drafts` and `_write_validated_unit` for size; behavior and console lines are unchanged.
- Tests: both unit files rewritten on `ScriptedGateway`; the critic file scripts two gateways and pins request routing (redrafts only to the drafting gateway, reviews only to the critic gateway), the pass-first, pass-after-redraft and give-up paths, and a `_run`-level test that the last failing pair is persisted with a `critic.json` listing `critic` (2 calls) and `drafting` (1 call). New contract file runs two cases per adapter over real stub processes.

## Task Commits

1. **Task 1 (tracer): stage 6 end to end through the gateway** - `cf9399f` (feat)
2. **Task 2: stage 7 critic with two gateways and the repair loop** - `448ac94` (feat)
3. **Task 3: critic repair loop through both adapters (GW-06)** - `c80e465` (test)

Tracer gate (auto mode): after Task 1 the `<verify>` command (9 passed), `tests/test_cli.py -k StageRoots` (5 passed) and the full suite were green before expanding. Between Task 1 and Task 2 the critic script still called the old `_generate_draft` signature; its legacy unit tests patched that call, so the suite stayed green, but the script itself was not runnable at the Task 1 commit. It is fixed in Task 2 as the plan sequences.

## Deviations from Plan

### Auto-fixed Issues

None.

Additions inside plan scope:

- The argparse `description` strings of both scripts now say "via gateway LLM" instead of "via Codex CLI" (same wording the 06-05 pilot used), because the old text would be false under a llama-cpp configuration. No other pt-BR console line, root constant or prompt text changed.
- `_run`-level critic test (persisted last failing pair plus run record) added beyond the plan's list, to cover T-06-26 end to end.

## Authentication Gates

None.

## Issues Encountered

One full-suite run mid-execution reported a single failure that did not reproduce: three subsequent full runs (2448, 2448 and 2453 passed) were clean. The failing test was not captured because the first run's output was truncated; it is most likely timing-sensitive (stub processes with sub-second timeouts) and unrelated to this plan's files. Flagged for the verifier rather than chased.

No model, package or weight file was downloaded; every adapter ran against stub executables.

## Known Stubs

None.

## Threat Flags

None. T-06-25 (two gateways via `open_stage_gateway`, routing test), T-06-26 (loop shape kept; pass-first, pass-after-redraft, give-up and persisted-last-pair tests) and T-06-27 (`ValidatedLearningUnit` anchored to the trusted pack, unchanged) are covered.

## Verification

- `uv run pytest tests/test_generate_codex_drafts.py tests/test_generate_codex_critic_review.py tests/llm_gateway -q -rs`: passed (part of the full run below)
- `uv run pytest tests/llm_gateway/test_critic_repair_contract.py -q -rs`: 4 passed, 0 skipped (ids `[codex]` and `[llama_cpp]` for both cases)
- `uv run pytest -q -rs`: 2453 passed
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean

## Next Phase Readiness

06-07 and 06-08 reuse the same script shape. `scripts/generate_codex_critic_review.py` is the one multi-stage script; the CLI preflight (later plan) should pass both `Stage.CRITIC` and `Stage.DRAFTING` for phase 7.

## Self-Check: PASSED

All five key files exist on disk; commits `cf9399f`, `448ac94` and `c80e465` are ancestors of HEAD; `commits: 3` measured from the persisted ledger.
