---
gsd_state_version: "1.0"
milestone: v1.0
current_phase: 6
current_phase_name: Pluggable LLM gateway
status: verifying
stopped_at: Completed 06-12-PLAN.md
last_updated: "2026-10-14T22:51:31.979Z"
last_activity: 2026-10-14
last_activity_desc: Phase 6 execution started
state_head: 38d3801012e211985c1c7337c5d0be12068ba2a7
progress:
  total_phases: 9
  completed_phases: 5
  total_plans: 12
  completed_plans: 12
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-10-14)

**Core value:** No text the critic rejected or never reviewed reaches a reader of the study material, and everything a reader sees is labelled as uncertified.
**Current focus:** Phase 6 — Pluggable LLM gateway

## Current Position

Phase: 6 (Pluggable LLM gateway) — EXECUTING
Plan: 12 of 12
Status: Phase complete — ready for verification
Last activity: 2026-10-14 — Phase 6 execution started
Next action: `/gsd-plan-phase 6`

Progress: [██████░░░░] 56% (phases, 5 of 9; Phases 1-5 are complete baseline built before GSD adoption, no GSD plans yet)

## Performance Metrics

**Velocity:**
- Total plans completed: 0 (baseline predates GSD)
- Average duration: n/a
- Total execution time: n/a

**Baseline checks (2026-10-14, commit 4bfbe46, working tree clean apart from untracked .planning/ and .serena/):**
- pytest: 2262 passed, 1 warning (38 s)
- ruff check: all checks passed; ruff format --check: 204 files formatted
- mypy src (strict): no issues in 112 source files

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1-5 (baseline) | 0 | - | - |
**Per-Plan Metrics:**

| Plan | Duration | Tasks | Files |
|------|----------|-------|-------|
| Phase 06 P01 | 10 min | 3 tasks | 9 files |
| Phase 06 P10 | 2 min | 2 tasks | 2 files |
| Phase 06 P02 | 5 min | 3 tasks | 6 files |
| Phase 06 P03 | 10min | 2 tasks | 5 files |
| Phase 06 P04 | 12min | 3 tasks | 6 files |
| Phase 6 P05 | 8min | 3 tasks | 7 files |
| Phase 06 P06 | 6min | 3 tasks | 5 files |
| Phase 6 P07 | 6min | 2 tasks | 4 files |
| Phase 06 P08 | 12min | 2 tasks | 2 files |
| Phase 06 P09 | 14min | 2 tasks | 6 files |
| Phase 06 P11 | 12min | 3 tasks | 4 files |
| Phase 06 P12 | 8min | 2 tasks | 7 files |

## Accumulated Context

### Decisions

Full locked-decision log (42 ADRs plus three operator readings) lives in PROJECT.md `<decisions>`.
Recent decisions affecting current work:

- [Ingest]: ADR 0045 section 2 egress rule accepted as written; no local-LLM phase planned from it (revised by the restructure below)
- [Ingest]: ADR 0023 sections 15, 16, the runtime subsection, the implementation-status list and GGUF-bound section 6 are not planning targets
- [Ingest]: Uncertified personal-study mode usable by the allowlisted audience; "no standing toward certification" stays in force
- [Roadmap]: Built baseline modeled as Phases 1-5 (Complete); v2 holds conditional, ADR 0023 remainder and codebase-map candidates
- [Restructure 2026-10-14]: The repository is a public portfolio artifact; v1.0 open work is Phases 6-9 (pluggable LLM gateway, synthetic demo with measured results, portfolio README, pre-public gate) and ends with the switch to public
- [Restructure 2026-10-14]: Former ROLL-01 and ROLL-02 left the milestone (private source deployment, tracked outside this repository); former ROLL-03 and ROLL-04 absorbed by DEMO-02 and DEMO-04
- [Restructure 2026-10-14]: The local adapter of the LLM gateway is planned (Phase 6); a new ADR amends ADR 0038 and closes ADR 0045 section 2 (GW-05)
- [Process 2026-10-14]: Canonical R0.4 mechanism is `/gsd-code-review` plus `/gsd-secure-phase` on the phase-final diff, re-run after material fixes
- [Phase 06]: 06-01: argv budget guard measures cost with a representative scratch path so it runs before the scratch directory exists; complete_json never retries LlmGatewayError
- [Phase 06]: Example LLM model download pinned to Hugging Face revision b466e1f8c07172155743e8e1307507d8a4f91fbd (Apache-2.0), HTTPS-only, per-shard SHA-256, opt-in via --with-example-llm-model
- [Phase 06]: 06-02: reasoning off is generic --reasoning off on every stage and attempt (OPEN-1); child cwd is a dedicated empty work subdirectory; llama adapter model path accepted as Path or str so URL rejection is meaningful
- [Phase 06]: 06-03: model_sha256 covers the configured model_path file only (first shard of the split model); env adapter override re-validates a cleared per-stage map
- [Phase 06]: OPEN-2 option A: an id in both the local registry and remote_llm_allowed_lecture_ids is a GatewayConfigurationError at preflight; egress stays fail-closed (D-15)
- [Phase 6]: 06-05: composition helpers call require_consistent_sources when codex is selected (OPEN-2 option A surfaces at preflight)
- [Phase 6]: 06-05: llama model path reaches the adapter as the configured string so the URL check stays effective
- [Phase 06]: 06-06: critic builds critic-stage and drafting-stage gateways before any call so redrafts honor the drafting-stage adapter and egress check (D-21)
- [Phase 06]: 06-06: critic-feedback redraft is a non-retry request; the repeat penalty stays retry-only (ADR 0034)
- [Phase 6]: 06-07: semantic ledgers load before the gateway opens in stages 13 and 18; stage 18 relies on open_stage_gateway as its whole D-12 validation
- [Phase 06]: 06-08: podcast process failure is a typed LlmCallError, never retried (D-22); --call-timeout-seconds validated at the parser, default 900
- [Phase 06]: 06-09: PhaseSpec.llm_stages is an explicit per-row field; orchestrator preflights the declared gateway stages in all three entry points and forwards exactly LECTURE_STUDY_LLM_ADAPTER and LECTURE_STUDY_LLM_CONFIG to children
- [Phase 06]: OPEN-1 (D-25): no per-model-family reasoning mechanism; generic --reasoning off suffices, adequacy for thinking-style models validated in the operator's D-07 manual run (recorded in ADR 0046)
- [Phase 06]: OPEN-2 (D-26, option A): an id in both the local registry and remote_llm_allowed_lecture_ids is a preflight configuration error (recorded in ADR 0046 decision 6)
- [Phase 06]: Plan 06-12: older ADRs 0020/0022/0034/0035/0036/0037 and PROJECT.md decisions D-READ-01/D-ADR0038-01/D-ADR0045-02 point forward to ADR 0046 by additive notes only; OPEN-2 rule restated from 06-04-SUMMARY

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 6]: The llama.cpp text route was deleted by ADR 0038; GW-03 restores it from the history before that record, so the restored code must be adapted to the current shared response contract and bounded-process discipline
- [Phase 6]: The local GGUF model to be used by `LlamaCppAdapter` and its provisioning are not yet decided
- [Phase 7]: Stages 8 and 9 have never run on lecture data (ADR 0024, ADR 0045); the demo lecture is their first run in this repository; expect first-run defects
- [Phase 7]: The recovery path after a re-draft is manual: the orchestrator does not rerun the critic (ADR 0045 Consequences); DEMO-04 exercises it
- [Ingest]: Egress rule is procedural and the provider's retention and training terms are unrecorded; the ADR texts of 0023 and 0024 were not amended to match the operator readings
- [Map]: CONCERNS.md and CONVENTIONS.md disagree on the count of broad exception handlers (about 38 lines versus 9); re-count before HARD-02 is planned

## Deferred Items

Items acknowledged and deferred at roadmap creation (detail in REQUIREMENTS.md v2):

| Category | Item | Status | Deferred At | Milestone |
|----------|------|--------|-------------|-----------|
| ADR conditional | COND-01..06 (podcast size check, re-transcription, re-extraction, critic rerun, script coupling, artifact provenance check) | Deferred | 2026-10-14 | v1.0 |
| ADR 0023 target | TARGET-01..05 (completeness manifests and conceptual units, atomic ledger, DAG, layout migration, complementary documents) | Needs operator decision | 2026-10-14 | v1.0 |
| Codebase map | HARD-01..08 (module splits, exception handlers, image pins, secret validation, catalog TODO, test gaps, tool versions, workspace root) | Candidate | 2026-10-14 | v1.0 |

## Session Continuity

Last session: 2026-10-14T22:51:31.962Z
Stopped at: Completed 06-12-PLAN.md
Resume file: None
