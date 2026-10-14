---
phase: 06-pluggable-llm-gateway
plan: 08
subsystem: llm-gateway
tags: [llm, podcast, gateway-migration, run-record, no-retry-call-errors, call-timeout]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "complete_json, typed gateway errors, record_llm_run (06-01, 06-03); open_stage_gateway and the stage 5 script shape (06-05); ScriptedGateway test double (06-05)"
provides:
  - "scripts/generate_codex_podcast.py on the gateway: Stage.PODCAST, podcast.json run record, --llm-runs-root, --call-timeout-seconds"
  - "_validate_script: normalization, traceability and listener guard inside the single retry boundary"
  - "D-22 behavior change: a process failure is an LlmCallError, never retried, exit 1 without the prompt on stderr"
affects: [06-09, 06-11, 06-12]

actuals:
  tokens: 9000
  tasks: 2
  commits: 2
plan_head_before: ac0598e3bb317c465b37ac4a29da7f76fb660301
plan_head_after: c37e3c0a87af562994e5bf0eaf53a6ab53cb7cd9

tech-stack:
  added: []
  patterns:
    - "Whole-lecture single call: complete_json with parse (echo strip + contract parse) and validate (normalize, traceability, listener guard) so both retry together once"
    - "Per-call timeout override validated at the argparse boundary (positive, finite) so a bad value never reaches LlmRequest as a traceback"

key-files:
  created: []
  modified:
    - scripts/generate_codex_podcast.py
    - tests/test_generate_codex_podcast.py

key-decisions:
  - "The recorder block wraps only the model call; the compiler write stays outside it, so podcast.json reports the model run and a compile failure does not masquerade as a gateway failure"
  - "--call-timeout-seconds uses a dedicated argparse type that rejects zero, negative and non-finite values, so LlmRequest's ValueError is unreachable from the command line"
  - "Module docstring known-gap paragraph rewritten: the adapters now refuse an oversized whole-lecture prompt before any call (D-08, D-23); the unchanged-size retry remains an accepted gap"

patterns-established:
  - "Main-level script tests patch the module's open_stage_gateway with a ScriptedGateway, chdir into tmp_path for the compiler's relative output root, and pass --llm-runs-root explicitly so no test writes under the repository workspace"

requirements-completed: [GW-01, GW-02]

coverage:
  - id: D1
    description: "Stage 14 end to end: critic gate, gateway, echo strip, bracket normalization, validation, compiler output with the uncertified marker, request timeout 900.0 and podcast.json run record"
    requirement: GW-01
    verification:
      - kind: integration
        ref: "tests/test_generate_codex_podcast.py::test_main_produces_the_uncertified_script_and_a_run_record"
        status: pass
    human_judgment: false
  - id: D2
    description: "Validation failure retried once with feedback then raised; typed call error makes exactly one request (D-22); main exits 1 without the prompt on stderr and records a failed run"
    requirement: GW-02
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_podcast.py"
        status: pass
    human_judgment: false
  - id: D3
    description: "PromptTooLargeError exits 1 naming the podcast stage, writes no compiled output, records status failed (D-08, D-23)"
    requirement: GW-02
    verification:
      - kind: integration
        ref: "tests/test_generate_codex_podcast.py::test_main_exits_one_when_the_whole_lecture_prompt_is_too_large"
        status: pass
    human_judgment: false
  - id: D4
    description: "--call-timeout-seconds reaches every request, defaults to 900, and rejects non-positive or non-finite values; script imports no process or scratch-directory module"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_podcast.py"
        status: pass
    human_judgment: false

duration: 12min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 08: Stage 14 podcast script on the gateway Summary

**The whole-lecture podcast script now obtains its output through `complete_json` with a `podcast.json` run record, an operator-adjustable per-call timeout (default 900 s), and uniform typed call errors that are never retried (D-22).**

## Performance

- **Duration:** about 12 min
- **Completed:** 2026-10-14
- **Tasks:** 2 (tracer, auto)
- **Files modified:** 2

## Accomplishments

- `scripts/generate_codex_podcast.py`: `_run` keeps lecture id validation and the critic gate first, then `open_stage_gateway(Stage.PODCAST, ...)` and `record_llm_run(... schema_name="PodcastScript")`. `_generate_script(*, gateway, lecture_id, evidence_pack, unit_ids, timeout_seconds)` is a single `complete_json` call; `_parse_script` keeps `_strip_execution_mode_echo`; `_validate_script` runs normalization, traceability and the listener guard in that order, so all three share the one retry.
- Removed `_call_codex_for_script`, `_describe_subprocess_failure`, `_SUBPROCESS_TIMEOUT_SECONDS`, `_MAXIMUM_PROCESS_OUTPUT_BYTES`, the HOME restoration and the `os`, `subprocess`, `tempfile`, process-lifecycle and process-environment imports. Prompt, pt-BR console text, compiler call and uncertified marker are unchanged.
- CLI: `--codex-executable` defaults to None, new `--llm-runs-root`, new `--call-timeout-seconds` (float, default 900.0, pt-BR help). `main()` adds `LlmGatewayError` to the generic error tuple.
- Tests (33): all pure tests kept; end-to-end tracer; retry-once, recovery on retry, no retry on `LlmCallError`; timeout reaching every request; `main()` exit 1 on call error with neither the instruction text nor the unit body on stderr; `PromptTooLargeError` exit 1 naming `podcast` and sizes, no `workspace` output, run record `failed`; blocked and invalid lectures never open a gateway; AST source-boundary test.

## Task Commits

1. **Task 1 (tracer): stage 14 end to end through the gateway** - `84e2453` (feat)
2. **Task 2: typed call errors without retry, oversized prompt refusal, timeout override** - `c37e3c0` (test)

Tracer gate: `uv run pytest tests/test_generate_codex_podcast.py -q` passed (23 tests at the Task 1 commit) before the Task 2 tests landed; the same tests were re-run end to end through `main()` with the scripted gateway.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing critical functionality] Validate the timeout flag at the parser**
- **Found during:** Task 1 (adding `--call-timeout-seconds`)
- **Issue:** `LlmRequest` raises `ValueError` for a non-positive or non-finite timeout; with a plain `type=float` a value such as `0` or `nan` would surface as a traceback instead of `error:` exit 1.
- **Fix:** `_positive_seconds` argparse type rejects them with a usage error (exit 2); parametrized test covers `0`, `-5`, `nan`, `inf`, `abc`.
- **Files modified:** `scripts/generate_codex_podcast.py`, `tests/test_generate_codex_podcast.py`
- **Commit:** `84e2453`, `c37e3c0`

Otherwise the plan executed as written. The module docstring was updated beyond the process-failure mention to also reflect that the adapters now refuse an oversized prompt.

## Authentication Gates

None.

## Issues Encountered

None. No model, package or weight file was downloaded; no adapter was started (scripted gateway only). Full-suite run: no failures, so no flaky test needed classification.

## Known Stubs

None.

## Threat Flags

None. T-06-31 (oversized prompt refused, exit 1 with stage and sizes, no output) and T-06-32 (stderr carries only the typed message; test asserts neither the instruction text nor a unit body appears) and T-06-33 (`_strip_execution_mode_echo` kept inside `parse`; compiled output carries the uncertified marker) are covered by tests.

## Verification

- `uv run pytest tests/test_generate_codex_podcast.py -q`: 33 passed
- `uv run pytest -q -rs`: 2473 passed
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean
- Committed diff scanned for absolute home paths: none; leak-gate and gitleaks hooks passed on both commits

## Next Phase Readiness

All six LLM-backed scripts (stages 5, 6, 7, 13, 14, 18) are on the gateway. ADR 0046 (06-11) should record the D-22 behavior change (podcast process failure no longer wrapped as a contract error nor retried) and the `--call-timeout-seconds` override with its unchanged 900 s default.

## Self-Check: PASSED

Both key files exist on disk; commits `84e2453` and `c37e3c0` are ancestors of HEAD; `commits: 2` measured from the persisted ledger.
