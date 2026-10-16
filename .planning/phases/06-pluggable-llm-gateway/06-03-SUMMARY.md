---
phase: 06-pluggable-llm-gateway
plan: 03
subsystem: llm-gateway
tags: [llm, gateway, configuration, yaml, pydantic, provenance, run-record, atomic-write]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "Stage, AdapterName, LlmGateway, GatewayIdentity and GatewayConfigurationError from llm/gateway.py (06-01)"
provides:
  - "load_gateway_configuration: one resolver for YAML plus LECTURE_STUDY_LLM_ADAPTER / LECTURE_STUDY_LLM_CONFIG, fail-closed"
  - "GatewayConfiguration, CodexSettings, LlamaCppSettings strict frozen models with per-stage adapter_for"
  - "config/llm-gateway.example.yaml versioned example with synthetic values and relative paths"
  - "record_llm_run, RecordingGateway, LlmRunRecorder, LlmRunRecord, GatewayUsage, run_record_path, LLM_RUNS_ROOT"
affects: [06-04, 06-05, 06-06, 06-07, 06-08, 06-09, 06-11, 06-12]

actuals:
  tokens: 8879
  tasks: 2
  commits: 2
plan_head_before: 95670aacc58d019964f64a76a7d9843f0986b5f6
plan_head_after: 03ab5311f3a6dab550404798c957521013fa0e58

tech-stack:
  added: []
  patterns:
    - "Validate YAML non-strict into strict frozen pydantic models; errors summarized as field location plus error type, never input values"
    - "Custom pydantic error type (llama_cpp_section_missing) so a cross-field failure is self-describing without echoing input"
    - "Decorator gateway keeping counters only; the digest of a shared model file is computed once per run"
    - "Sidecar written to a temporary name then Path.replace, body exception re-raised after the record exists"

key-files:
  created:
    - src/lecture_study/llm/config.py
    - config/llm-gateway.example.yaml
    - tests/llm_gateway/test_configuration.py
    - src/lecture_study/llm/run_record.py
    - tests/llm_gateway/test_run_record.py
  modified: []

key-decisions:
  - "The environment adapter override is applied by re-validating the already validated file model with default_adapter replaced and stages emptied, so an invalid file still fails closed even when the override would have hidden the invalid part (D-20)"
  - "A default configuration path that exists but is not readable (for example a directory) fails closed; only an absent default path yields the built-in codex defaults"
  - "model_sha256 covers the configured model_path file only; for the split example model it identifies the first shard (cite in ADR 0046 decision 7)"
  - "The model digest of a file shared by several tracked gateways is computed once per run, because hashing a multi-gigabyte model per gateway would multiply the cost"

patterns-established:
  - "Pure inner llm/ modules (config.py, run_record.py) import only domain and gateway.py; the layering test already lists both as inner modules and passes"

requirements-completed: [GW-04, GW-01]

coverage:
  - id: D1
    description: "Configuration resolves per stage with precedence env override, stage entry, default_adapter, built-in codex"
    requirement: GW-04
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_configuration.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "Invalid or missing explicit configuration, unknown override and half-configured llama override fail closed with GatewayConfigurationError"
    requirement: GW-04
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_configuration.py"
        status: pass
    human_judgment: false
  - id: D3
    description: "Run record is metadata-only, atomic, id-validated and located outside dashboard and delivery roots"
    requirement: GW-01
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_run_record.py"
        status: pass
      - kind: unit
        ref: "tests/test_llm_layering.py"
        status: pass
    human_judgment: false

duration: 10min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 03: Gateway configuration and provenance run record Summary

**Strict YAML plus environment resolver that maps each pipeline stage to an adapter (codex by default, single-run override that clears per-stage entries), and a metadata-only atomic run record written to `workspace/data/llm_runs/LECTURE_ID/SCRIPT_STAGE.json`.**

## Performance

- **Duration:** about 10 min
- **Started / completed:** 2026-10-14
- **Tasks:** 2 (tracer + auto)
- **Files created:** 5

## Accomplishments

- `llm/config.py`: `GatewayConfiguration` (default adapter, per-stage mapping keyed by the six `Stage` values, `remote_llm_allowed_lecture_ids` as `SafeIdentifier` tuple, `codex` and `llama_cpp` settings). A model validator rejects `llama-cpp` selection without a `llama_cpp` section. `LECTURE_STUDY_LLM_CONFIG` picks the file (missing explicit path is an error, absent default path yields defaults); `LECTURE_STUDY_LLM_ADAPTER` replaces the default and clears all stage entries (D-20), rejects unknown names listing `"codex"` and `"llama-cpp"`, and re-validates. Validation messages carry field paths and error types only (a test proves an input value never appears). Nothing is spawned and only one YAML file is read.
- `config/llm-gateway.example.yaml`: documented keys, precedence and both environment variables; synthetic values, repository-relative paths only (`model_path` names shard 1 of the split example model); loads cleanly through the real loader in a test. `git check-ignore config/llm-gateway.local.yaml` succeeds through the existing `config/*.local.yaml` rule.
- `llm/run_record.py`: `RecordingGateway` counts calls, retry calls and call seconds (failed calls included) and stores no prompt or response. `record_llm_run` validates the lecture id with `TypeAdapter(SafeIdentifier)` before building any path, writes status `succeeded` or `failed` on exit, re-raises body exceptions after the record exists, and writes through `<name>.json.tmp` plus `Path.replace`. The nine top-level keys are pinned by a test; marker strings for prompt and response are proven absent from the file.

## Task Commits

1. **Task 1 (tracer): configuration resolution end to end** - `7d335ac` (feat)
2. **Task 2: metadata-only atomic run record** - `03ab531` (feat)

Tracer gate: `<verify>` re-run end to end after the Task 1 commit (26 passed, local copy ignored) before expanding.

## Findings for ADR 0046

- **Digest scope (decision 7).** `GatewayUsage.model_sha256` is the SHA-256 of the configured `model_path` file only. For the split three-shard example model that file is shard 1, so the digest identifies the first shard; all shards are integrity-checked at provisioning by their pinned per-shard digests (06-10). The scope is stated in the `Field(description=...)` and asserted by a test. Shard discovery was deliberately not added.
- **Location safety (D-18).** `scripts/generate_progress_dashboard.py` counts only the fixed `_STAGES` directories (lines 28-38) under `DATA_ROOT/<stage>/<lecture_id>/` (lines 110-114); `llm_runs` is not among them (test). `src/lecture_study/quality/delivery.py` fingerprints only explicit roots: `DELIVERY_MANIFESTS_ROOT`, `QUALITY_CERTIFICATES_ROOT`, `CANONICAL_DOCUMENTS_ROOT`, `METADATA_ROOT` (lines 61-64) and `DELIVERY_OUTPUT_ROOTS` (lines 127-133). A search for `glob`, `rglob`, `iterdir`, `os.walk` and `scandir` in that file found no hit, and no generic walk of `workspace/data` exists. A test asserts `workspace/data/llm_runs` equals none of those roots and sits under none of them.
- **Environment names.** `LECTURE_STUDY_LLM_ADAPTER` and `LECTURE_STUDY_LLM_CONFIG` match none of the sensitive names, prefixes or suffixes of `ops/process_environment.py` (reviewed against lines 22-46), so neither is stripped as a credential.

## Deviations from Plan

### Auto-fixed Issues

None. Additions inside the plan scope, not deviations:

- A hash-once-per-run memo (and its test) for gateways sharing one model file; the plan did not specify it, and per-gateway hashing of a multi-gigabyte model would multiply the cost.
- Failed-call accounting, unreadable-model warning (path only) and delivery-root assertion tests beyond the enumerated list.
- `track` returns `RecordingGateway` (a subtype of the `LlmGateway` return in the interface block) so `usage()` stays reachable.

## Authentication Gates

None.

## Issues Encountered

None. The operator pre-answers (OPEN-1, OPEN-2) concern later plans (CLI flag and egress preflight) and did not touch this plan. No model files were downloaded.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-06-12 to T-06-16 are covered by tests: `safe_load` with strict extra-forbid models and typed errors, metadata-only record with absent-marker test, `SafeIdentifier` check before path construction, temporary file plus replace, and override validation that clears stage entries and fails on a llama selection without settings.

## Verification

- `uv run pytest tests/llm_gateway tests/test_llm_layering.py -q` passes
- `uv run pytest -q -rs`: 2409 passed
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean

## Next Phase Readiness

06-04 onward can import `load_gateway_configuration` for the preflight and the script-side validation, and `record_llm_run` for the sidecar. `remote_llm_allowed_lecture_ids` is parsed but not yet enforced (the egress gate and the factory are later plans).

## Self-Check: PASSED

All five key files exist on disk; commits `7d335ac` and `03ab531` are ancestors of HEAD; `commits: 2` measured from the persisted ledger.
