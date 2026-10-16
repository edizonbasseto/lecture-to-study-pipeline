---
phase: 06-pluggable-llm-gateway
plan: 07
subsystem: llm-gateway
tags: [llm, gateway, mock-exam, flashcards, critic-gate, student-facing-guard, run-record]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "LlmGateway port and complete_json (06-01); record_llm_run (06-03); open_stage_gateway and the ScriptedGateway double (06-05)"
provides:
  - "scripts/generate_codex_mock_exam.py on the gateway (Stage.EXAMS) with an exams.json run record"
  - "scripts/generate_codex_personal_study_flashcards.py on the gateway (Stage.FLASHCARDS), standalone, with a flashcards.json run record"
  - "_request_questions and _request_cards: parse plus student-facing guard plus count check inside the single bounded retry"
affects: [06-08, 06-09, 06-11, 06-12]

actuals:
  tokens: 15450
  tasks: 2
  commits: 2
plan_head_before: 7a6e32353ee0c7e5d2e49884a5b416b68e6e2568
plan_head_after: a7df02844cd2cd01ed0a497964c2300913bd1dc0

tech-stack:
  added: []
  patterns:
    - "Critic gate (load_critic_approved_drafts) and input loading first, then open_stage_gateway, then record_llm_run around generation"
    - "Domain guards passed as the validate callable of complete_json so they share the one bounded retry"
    - "Deck validation (_build_deck) runs inside the run-record block, outside the retry boundary, so a deck failure is recorded as failed"

key-files:
  created: []
  modified:
    - scripts/generate_codex_mock_exam.py
    - tests/test_generate_codex_mock_exam.py
    - scripts/generate_codex_personal_study_flashcards.py
    - tests/test_generate_codex_personal_study_flashcards.py

key-decisions:
  - "Semantic ledgers are loaded before the gateway opens (not model-related), so a missing-ledger run builds no gateway and writes no run record"
  - "_run split into _run plus _generate_all_questions / _generate_all_cards (and _write_deck_atomically for stage 18) to keep functions small; helper names and kwargs used by tests are unchanged"
  - "Stage 18 relies on open_stage_gateway (reads os.environ and the default configuration file) as its entire D-12 validation; no separate preflight call"

patterns-established:
  - "Standalone stage script: unknown adapter surfaces as LlmGatewayError (GatewayConfigurationError) mapped to 'error: ...' on stderr and exit 1, with no output directory created"

requirements-completed: [GW-01, GW-04]

coverage:
  - id: D1
    description: "Stage 13 runs end to end through the gateway: two approved units produce an uncertified exam.json and an exams.json run record whose call count equals the requests made"
    requirement: GW-01
    verification:
      - kind: integration
        ref: "tests/test_generate_codex_mock_exam.py::TestRunThroughTheGateway"
        status: pass
    human_judgment: false
  - id: D2
    description: "A lecture blocked by the critic gate exits 1 without opening a gateway, writing an artifact or a run record (stages 13 and 18)"
    requirement: GW-04
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_mock_exam.py::TestRunUsesOnlyCriticApprovedUnits"
        status: pass
      - kind: unit
        ref: "tests/test_generate_codex_personal_study_flashcards.py::test_main_blocks_the_lecture_when_one_unit_failed_the_critic"
        status: pass
    human_judgment: false
  - id: D3
    description: "Student-facing guard and count check retried once inside complete_json; two failures raise after two requests"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_mock_exam.py (test_request_questions_*)"
        status: pass
      - kind: unit
        ref: "tests/test_generate_codex_personal_study_flashcards.py (test_request_cards_*)"
        status: pass
    human_judgment: false
  - id: D4
    description: "Stage 18 with an unknown adapter exits 1 naming the valid adapters, writing no deck and no run record"
    requirement: GW-04
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_personal_study_flashcards.py::test_main_with_an_unknown_adapter_exits_1_before_any_call_or_deck"
        status: pass
    human_judgment: false
  - id: D5
    description: "Both scripts import no process or scratch-directory module (AST source boundary)"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/test_generate_codex_mock_exam.py::test_script_imports_no_process_or_scratch_directory_module"
        status: pass
      - kind: unit
        ref: "tests/test_generate_codex_personal_study_flashcards.py::test_script_imports_no_process_or_scratch_directory_module"
        status: pass
    human_judgment: false

duration: 6min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 07: Stages 13 and 18 gateway migration Summary

**Mock exam (stage 13) and personal-study flashcards (stage 18) now obtain every model output through `complete_json` on the configured gateway, with the critic gate still first, the student-facing guard and count check inside the single retry, and `exams.json` / `flashcards.json` run records.**

## Performance

- **Duration:** about 6 min
- **Completed:** 2026-10-14
- **Tasks:** 2 (tracer, auto)
- **Files modified:** 4

## Accomplishments

- Both scripts lost their process constants, `_run_codex_for_*`, `_call_codex_for_*`, HOME restoration and the `os`, `tempfile`, process-lifecycle and process-environment imports (adapter owns them, ADR 0046).
- `_request_questions` / `_request_cards` call `complete_json` with `parse` and a `validate` that runs the ADR 0027 guard then the count check, so both are retried once with trusted feedback, as before.
- `_run` order: `load_critic_approved_drafts`, ledger loading, `open_stage_gateway(Stage.EXAMS | Stage.FLASHCARDS, ...)`, `record_llm_run`. A blocked, malformed or ledger-less lecture builds no gateway and writes no artifact or record (tests assert `open_stage_gateway` never called).
- Stage 18 validates its own configuration through `open_stage_gateway` (T-06-28): `LECTURE_STUDY_LLM_ADAPTER=bogus` exits 1 naming the valid adapters with no deck and no run record.
- New `--llm-runs-root` (default `LLM_RUNS_ROOT`); `--codex-executable` now defaults to None (overrides the configured executable). Stage 18 keeps the `tmp_path.replace(output_path)` atomic deck write and the `ValidationError` wrap in `_build_deck`.
- Tests rewritten on `ScriptedGateway`: scratch-cwd tests removed, source-boundary tests added, tracer test (two approved units, uncertified marker, run-record calls equal requests), gateway-failure test (record `failed`, no exam), guard-retry tests.

## Task Commits

1. **Task 1 (tracer): stage 13 mock exam on the gateway behind the critic gate** - `3f8d7c9` (feat)
2. **Task 2: stage 18 personal-study flashcards on the gateway, standalone validation included** - `a7df028` (feat)

Tracer gate: Task 1 `<verify>` plus `tests/test_cli.py` and `tests/test_llm_layering.py` passed (92 passed) before expanding to Task 2.

## Deviations from Plan

None - plan executed as written. Within scope: ledgers are loaded between the critic gate and the gateway (not model-related), and `_run` was split into small helpers (`_generate_all_questions`, `_generate_all_cards`, `_write_deck_atomically`) per clean-code; names and kwargs used by tests are unchanged.

## Authentication Gates

None.

## Issues Encountered

None. The full suite passed on the first run (2461 passed); no flaky test failure was observed. No model, package or weight file was downloaded.

## Known Stubs

None.

## Threat Flags

None. T-06-28 (stage 18 own validation, bogus-adapter test), T-06-29 (critic gate first, `open_stage_gateway` never called for blocked or malformed lectures) and T-06-30 (guard and count check as `validate` inside the retry boundary, guard tests kept) are covered by tests.

## Verification

- `uv run pytest tests/test_generate_codex_personal_study_flashcards.py tests/test_generate_codex_mock_exam.py -q`: 31 passed
- `uv run pytest -q -rs`: 2461 passed
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean

## Next Phase Readiness

Student-facing generators except the podcast are on the gateway. Stage 18 has no orchestrator preflight, so any later CLI preflight does not need to cover it; stage 13 can be included in `preflight_stage_gateways`.

## Self-Check: PASSED

All four modified files exist on disk; commits `3f8d7c9` and `a7df028` are ancestors of HEAD; `commits: 2` measured from the persisted ledger.
