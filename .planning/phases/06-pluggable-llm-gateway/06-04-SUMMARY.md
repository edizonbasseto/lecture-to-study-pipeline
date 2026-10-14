---
phase: 06-pluggable-llm-gateway
plan: 04
subsystem: llm-gateway
tags: [llm, egress, privacy, fail-closed, local-registry, codex]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "RemoteEgressRefusedError, GatewayConfigurationError, Stage (06-01); remote_llm_allowed_lecture_ids in GatewayConfiguration (06-03)"
provides:
  - "RemoteEgressPolicy Protocol and require_remote_egress_allowed in llm/egress.py"
  - "RegistryRemoteEgressPolicy in study_materials/remote_llm_policy.py (local ids from the registry, portal ids from the YAML list)"
  - "LocalLecture.remote_llm_allowed: bool = False (persisted key, absent means refused)"
  - "scripts/register_local_lecture.py --remote-llm-allowed; a rerun may toggle the flag"
  - "RegistryRemoteEgressPolicy.require_consistent_sources for the OPEN-2 preflight check"
affects: [06-05, 06-06, 06-07, 06-08, 06-09, 06-11, 06-12]

actuals:
  tokens: 6200
  tasks: 3
  commits: 2
plan_head_before: 7f92dbe1e3fb1b6f0c5173a2eea423e2f8738805
plan_head_after: 2f9fddb4dac52bb49527dbe07ff3895d897edafe

tech-stack:
  added: []
  patterns:
    - "Policy port defined in llm/, implementation in study_materials/ so llm/ never imports the academic registry (D-02)"
    - "Each lecture id has exactly one governing egress source, classified by is_reserved_local_id"
    - "Registry loaded lazily and cached; a portal-id lookup or a construction never reads it"

key-files:
  created:
    - src/lecture_study/llm/egress.py
    - src/lecture_study/study_materials/remote_llm_policy.py
    - tests/llm_gateway/test_egress_policy.py
  modified:
    - src/lecture_study/study_materials/local_lectures.py
    - scripts/register_local_lecture.py
    - tests/test_local_lectures.py

key-decisions:
  - "OPEN-2 resolved as option A (see section below): an id in both the local registry and remote_llm_allowed_lecture_ids is a configuration error"
  - "The idempotence comparison of a registration rerun ignores both operator flags (publish, remote_llm_allowed) through one helper, so a rerun only changing the egress flag is not refused as an id collision"
  - "An unreadable registry raises GatewayConfigurationError chained from LocalLectureError; a missing file means no flags and refusal"

patterns-established:
  - "Fail-closed egress: only an operator-recorded flag (registry) or list entry (portal) makes a lecture eligible for the remote adapter"

requirements-completed: [GW-04, GW-05]

coverage:
  - id: D1
    description: "Unflagged local lecture, unregistered reserved id and missing registry are refused for codex; unreadable registry raises a typed error"
    requirement: GW-05
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_egress_policy.py"
        status: pass
    human_judgment: false
  - id: D2
    description: "Portal lecture is allowed only when listed in remote_llm_allowed_lecture_ids; the registry is never read for portal ids"
    requirement: GW-04
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_egress_policy.py"
        status: pass
    human_judgment: false
  - id: D3
    description: "OPEN-2 option A: overlap of registry and portal list is a GatewayConfigurationError naming the ids"
    requirement: GW-05
    verification:
      - kind: unit
        ref: "tests/llm_gateway/test_egress_policy.py"
        status: pass
    human_judgment: false
  - id: D4
    description: "Registration flag round trip: default false, --remote-llm-allowed true, toggle on rerun both ways, legacy rows load false"
    requirement: GW-05
    verification:
      - kind: unit
        ref: "tests/test_local_lectures.py"
        status: pass
      - kind: unit
        ref: "tests/test_llm_layering.py"
        status: pass
    human_judgment: false

duration: 12min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 04: Remote egress policy Summary

**Fail-closed remote egress decision: `LocalLecture.remote_llm_allowed` (default false) for local-registry lectures and `remote_llm_allowed_lecture_ids` for portal lectures, behind a `RemoteEgressPolicy` port in `llm/` implemented in `study_materials/`, with an id present in both sources rejected as a configuration error.**

## Performance

- **Duration:** about 12 min
- **Completed:** 2026-10-14
- **Tasks:** 3 (tracer, checkpoint resolved by operator pre-answer, auto)
- **Files created:** 3, modified: 3

## Accomplishments

- `llm/egress.py` (inner module, imports only `llm.gateway`): `RemoteEgressPolicy` Protocol and `require_remote_egress_allowed`, which raises `RemoteEgressRefusedError` carrying lecture id and stage.
- `LocalLecture.remote_llm_allowed: bool = False` next to `publish`. Existing registry rows without the key load as false. The rerun comparison now goes through `_without_operator_flags`, so changing only `publish` or `remote_llm_allowed` is accepted; revoking needs no guard.
- `RegistryRemoteEgressPolicy`: reserved local ids are decided only by the registry row; any other id only by the portal list. Unregistered reserved id and absent registry file refuse. A `LocalLectureError` raises `GatewayConfigurationError` (chained), never a pass. The registry is loaded lazily and cached; construction and portal lookups never read it, so llama-only runs are unaffected.
- `register_local_lecture.py --remote-llm-allowed` (pt-BR help, states the operator verified there is no patient-identifying data).

## Task Commits

1. **Task 1 (tracer): egress decision from registered flag or portal list** - `2f1b4ce` (feat)
2. **Task 2: OPEN-2 checkpoint** - no commit; resolved by the operator's recorded answer (below), so no checkpoint was returned
3. **Task 3: OPEN-2 option A and flag round-trip tests** - `2f9fddb` (feat)

Tracer gate: `<verify>` (egress policy, local lectures and layering tests) passed after the Task 1 commit (88 passed) before expanding.

## OPEN-2 resolution

- **Question:** behavior when an id is in both `config/local-lectures.local.yaml` and `remote_llm_allowed_lecture_ids`.
- **Operator answer (2026-10-14, pre-answered in the dispatch):** option A. A lecture id present in BOTH the local registry (`LocalLecture`) and `remote_llm_allowed_lecture_ids` in the gateway YAML is a CONFIGURATION ERROR at preflight (not a warning, not "registry wins"). Egress stays fail-closed: an unflagged lecture is refused for the Codex adapter (D-15).
- **Implementation:** `RegistryRemoteEgressPolicy.require_consistent_sources()` raises `GatewayConfigurationError` naming the sorted conflicting ids and stating that local lectures carry `remote_llm_allowed` in the local registry and must not be listed in `remote_llm_allowed_lecture_ids`. Any local-id lookup runs the same check first, so the list can never grant a local lecture even when a caller forgets the preflight.
- **For 06-05, 06-07 (preflight) and 06-11/06-12:** call `require_consistent_sources()` once when the codex adapter is selected (CLI preflight and script-side validation). Cite this section in ADR 0046.
- **Consequence to document:** a run selecting codex stops until the duplicate list entry is removed, even for an unrelated lecture (the accepted con of option A).

## Deviations from Plan

### Auto-fixed Issues

None. Additions inside the plan scope:

- Public `require_consistent_sources()` so the preflight can validate the OPEN-2 rule without a lecture lookup; the plan described the check only on the first registry load. Both paths are covered by tests.
- Task 2 was not returned as a checkpoint because the operator's answer was supplied in the dispatch; Task 3 then implemented it as the plan prescribes.

## Authentication Gates

None.

## Issues Encountered

None. No model files or packages were downloaded.

## Known Stubs

None.

## Threat Flags

None. T-06-17 to T-06-19 are covered by tests: fail-closed default, portal ids only via the explicit list, unreadable registry raising a typed error, and the overlap rejected under option A. T-06-20 accepted as planned.

## Verification

- `uv run pytest tests/llm_gateway/test_egress_policy.py tests/test_local_lectures.py tests/test_llm_layering.py -q`: 97 passed
- `uv run pytest -q -rs`: 2429 passed
- `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean

## Next Phase Readiness

06-05 (factory) can build `RegistryRemoteEgressPolicy(allowed_portal_lecture_ids=config.remote_llm_allowed_lecture_ids)`, call `require_consistent_sources()` and `require_remote_egress_allowed(...)` before resolving any executable when codex is selected.

## Self-Check: PASSED

All six key files exist on disk; commits `2f1b4ce` and `2f9fddb` are ancestors of HEAD; `commits: 2` measured from the persisted ledger.
