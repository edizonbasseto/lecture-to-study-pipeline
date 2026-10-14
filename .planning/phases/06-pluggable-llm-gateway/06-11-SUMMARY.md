---
phase: 06-pluggable-llm-gateway
plan: 11
subsystem: documentation
tags: [adr, llm-gateway, egress, requirements, amendment-chain, manual-run-record]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "Plans 06-01..06-10: the gateway, both adapters, configuration, egress policy, run record, six migrated scripts, orchestrator preflight and provisioning that the ADR records as built"
provides:
  - "ADR 0046 (Accepted): decisions 1-9 as built plus decision 10, the D-07 manual real-model run procedure, fillable record and follow-up list"
  - "Forward-pointing amendment notes in ADR 0038 (Status) and ADR 0045 (Status, section 2, Consequences) with zero deleted lines"
  - "REQUIREMENTS.md: GEN-07 amendment, GW-03 rewording (D-05), GW-05 fail-closed flag clarification, Out of Scope row"
  - "OPEN-1 and OPEN-2 recorded in the ADR with the operator answers"
affects: [06-12, phase-verification, phase-uat]

actuals:
  tokens: 7448
  tasks: 3
  commits: 2
plan_head_before: 976c3b58d00b99d827f2913426eaba1875021362
plan_head_after: 0bf1f1592e62e8c33cc77c66906166600aeb6b61

tech-stack:
  added: []
  patterns:
    - "Older ADRs receive forward notes only (ADR 0045 section 6): additions, no deletions, verified with git show --numstat"
    - "Status Accepted covers what is built; an unrun real-model check is a fillable record plus a follow-up list routed to the phase UAT"

key-files:
  created:
    - docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md
  modified:
    - docs/decisions/0038-orchestrator-codex-integration-and-local-llm-route-removal.md
    - docs/decisions/0045-critic-gate-for-derivative-material-egress-and-audience.md
    - .planning/REQUIREMENTS.md

key-decisions:
  - "OPEN-1 (operator answer 2026-10-14, D-25): no per-model-family mechanism; generic --reasoning off on every local call suffices; its adequacy for thinking-style models is validated in the D-07 manual run, pending the operator. Recorded in ADR 0046 decision 4 and decision 10. No checkpoint returned, no model downloaded."
  - "OPEN-2 (operator answer, option A) recorded in ADR 0046 decision 6 as in 06-04-SUMMARY: an id in both the local registry and remote_llm_allowed_lecture_ids is a preflight configuration error; the accepted consequence is that a codex run stops until the duplicate entry is removed."
  - "The ADR follows the code: stage budgets, flags, run-record fields and preflight stage sets were read from the source, not from plan text."

patterns-established:
  - "Decision 10 record table holds 'pending operator run' in every result cell, so an unfilled cell is visible at review"

requirements-completed: [GW-05]

coverage:
  - id: D1
    description: "ADR 0046 Accepted with the amendment chain to ADR 0038, ADR 0045 and GEN-07; forward notes add lines only"
    requirement: GW-05
    verification:
      - kind: other
        ref: "rg -n '^Accepted' docs/decisions/0046-*.md && rg -l 'ADR 0046' docs/decisions/0038-*.md docs/decisions/0045-*.md; git show --numstat --format= (0 deletions for 0038 and 0045)"
        status: pass
    human_judgment: false
  - id: D2
    description: "REQUIREMENTS.md GEN-07, GW-03, GW-05 and the Out of Scope row amended as specified; checkboxes and traceability table untouched"
    requirement: GW-05
    verification:
      - kind: other
        ref: "rg -n 'Amended by ADR 0046' .planning/REQUIREMENTS.md; GW-05 line carries 'Clarified by ADR 0046 ... remote_llm_allowed ... fail closed'"
        status: pass
    human_judgment: false
  - id: D3
    description: "D-07 manual real-model run of stages 5, 6 and 7 with the example model, with the five follow-ups marked needed or not needed"
    requirement: GW-03
    verification:
      - kind: manual
        ref: "docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md decision 10"
        status: pending
    human_judgment: true
    rationale: "No llama.cpp binary or GGUF model exists in CI or on the planning machine; the run is the operator's pending item. GW-03 stays Pending in the traceability table until the run is recorded."

duration: 12min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 11: ADR 0046 and the amendment chain Summary

**ADR 0046 records the gateway as built (decisions 1-9, Accepted), amends ADR 0038 and GEN-07, closes ADR 0045 section 2 with the fail-closed per-lecture egress flag, and defines the operator's D-07 manual real-model run as a fillable record with a follow-up list.**

## Performance

- **Duration:** about 12 min
- **Completed:** 2026-10-14
- **Tasks:** 3 (tracer, checkpoint pre-answered by the operator, auto)
- **Files created:** 1, modified: 3

## Accomplishments

- `docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md` (Context, ten decisions, Alternatives considered, Consequences). Content was taken from the code and the 06-01..06-10 summaries: port and layering rules enforced by `tests/test_llm_layering.py`; configuration keys, precedence and the two environment variables; the Codex adapter contract with the 900 KiB argument budget; the `llama-completion` flag set, the exact `llama-tokenize` budget check, the stage budget table (context, cap, temperature) and the offline environment; what "no network egress" does and does not mean; the example model with repository, pinned revision, Apache-2.0 license and the three shard SHA-256 values; the egress rule with OPEN-2 option A; the run record fields, the first-shard-only `model_sha256` scope and why the record is not beside stage outputs; the D-22 and other behavior changes; the carried independence question.
- ADR 0038 gained one Status note; ADR 0045 gained a Status note, a section 2 note and a Consequences note. `git show --numstat` on the first commit shows 4/0 and 11/0 (additions/deletions): no pre-existing line changed.
- `.planning/REQUIREMENTS.md`: GEN-07 amendment appended, GW-03 reworded (`llama-completion`, `llama-tokenize`, rebuilt from ADRs 0020, 0022, 0034, D-05) keeping its source parenthesis, GW-05 clarification appended after its parenthesis with no word removed, Out of Scope row now says "is amended by ADR 0046 (GW-05)". No checkbox or traceability row changed (GW-03 stays Pending).
- Decision 10: procedure, a 17-row record table where every result cell reads "pending operator run", and the follow-up table.

## Task Commits

1. **Task 1 (tracer): ADR 0046 Accepted and the amendment chain** - `5e78649` (docs)
2. **Task 2: OPEN-1 checkpoint** - no commit; resolved by the operator's recorded answer, so no checkpoint was returned
3. **Task 3: OPEN-1 wording and the D-07 run record** - `0bf1f15` (docs)

Tracer gate: Task 1 `<verify>` (four rg checks) re-run end to end and green before Task 3; the checks are fully automated, so no human checkpoint applies.

## OPEN-1 and OPEN-2

- **OPEN-1 (answered 2026-10-14, D-25):** option-a in the plan's terms. No per-model-family mechanism; `--reasoning off` on every call suffices. The D-07 record keeps an optional row for a thinking-style model, filled only if the operator supplies one. No model was downloaded.
- **OPEN-2 (option A, D-26):** recorded in ADR 0046 decision 6 exactly as in 06-04-SUMMARY (configuration error at preflight; accepted consequence stated).

## D-07 follow-ups

Open items pending the operator's manual run (ADR 0046 decision 10 holds the fillable record and the same list). No code change is made by this plan. Each item is marked "needed" or "not needed" after the run; a "needed" item is logged as a failed item in the phase UAT by `/gsd-verify-work`, routed to `/gsd-plan-phase 6 --gaps`, and the gap plan adds an amendment note to ADR 0046 decision 4.

| Item | Follow-up if the run requires it | State |
| ---- | -------------------------------- | ----- |
| (a) | D-17 fallback: `-no-cnv` plus an adapter-rendered family envelope, if the GGUF chat template fails with `--single-turn` | open, pending run |
| (b) | `HOME` added to the llama child environment if the child fails to start or Metal fails without it (RESEARCH A3) | open, pending run |
| (c) | Model-path or adapter change if the first shard does not load the split model (RESEARCH A9) | open, pending run |
| (d) | Adapter change if the real `llama-tokenize` count line differs from the `Total number of tokens:` format the adapter parses | open, pending run |
| (e) | Argument change for any flag of decision 4 that the pinned llama.cpp commit does not accept | open, pending run |

Items (a)-(c) are the three code changes named by the plan; (d) and (e) were added at the operator's instruction and are verification items that can also turn into changes.

The D-07 human check ("Run the procedure in ADR 0046 section 10 on one evidence pack with the example model and fill the record table") is the item the phase UAT must list.

## Deviations from Plan

### Auto-fixed Issues

None.

Additions inside scope:

- The Alternatives section lists research options O1 and O3-O5 with their rejection reasons taken from the RESEARCH table, rather than a single sentence.
- Follow-ups (d) and (e) were added to the plan's three items (operator note).
- The `requirements mark-complete` step was not run: the plan forbids ticking checkboxes (phase verification does that) and GW-03 must stay Pending until the D-07 run is recorded.

## Authentication Gates

None.

## Verification

- `rg -n "^Accepted" ... && rg -l "ADR 0046" ADR 0038 ADR 0045 && rg -n "Amended by ADR 0046" REQUIREMENTS.md && GW-05 clarification regex`: all succeed
- `rg -n "Manual real-model run"`, `rg -n "reasoning"`, `rg -n "Follow-ups from this run"` on ADR 0046: all succeed
- ADR 0046 contains `llama-completion`, `--no-escape`, `remote_llm_allowed`, `workspace/data/llm_runs`, `LECTURE_STUDY_LLM_ADAPTER` and the three shard digests (matched against `scripts/01_provision_engines.sh`); no absolute home path in any changed file
- `uv run pytest -q -rs`: 2494 passed (no code changed)
- `uv run ruff check .` clean; `uv run mypy src` clean (124 files)
- Leak-gate and gitleaks hooks passed on both commits

## Known Stubs

None. The "pending operator run" cells in ADR 0046 decision 10 are a deliberate fillable record, not stubs wired into any behavior; they are listed above as the D-07 follow-ups.

## Threat Flags

None. T-06-41 (decision 4 states what "no network egress" does and does not mean, including no OS sandbox and a trusted pinned binary), T-06-42 (repository-relative paths only; denylist and gitleaks hooks passed; no absolute home path) and T-06-43 (decision 6 states the operator verifies absence of patient-identifying data and the provider-terms check stays procedural) are mitigated.

## Next Phase Readiness

06-12 and phase verification can cite ADR 0046 as the phase's decision record. The operator's manual run is the remaining item before GW-03 can be ticked; the phase UAT must list it.

## Self-Check: PASSED

`docs/decisions/0046-pluggable-llm-gateway-and-local-adapter.md` exists; commits `5e78649` and `0bf1f15` are ancestors of HEAD; `commits: 2` measured from the persisted ledger.
