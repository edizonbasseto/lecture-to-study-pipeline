---
phase: 06-pluggable-llm-gateway
plan: 12
subsystem: documentation
tags: [adr, forward-notes, llm-gateway, egress, project-decisions]

requires:
  - phase: 06-pluggable-llm-gateway
    provides: "06-02 llama adapter flags, 06-04 egress policy and the OPEN-2 rule, 06-08 podcast behavior, 06-09 orchestrator wiring, ADR 0046 (06-11)"
provides:
  - "Forward notes to ADR 0046 in ADRs 0020, 0022, 0034, 0035, 0036 and 0037 (additions only, zero deleted lines)"
  - "Revision notes on D-READ-01, D-ADR0038-01 and D-ADR0045-02 in .planning/PROJECT.md, including the OPEN-2 rule"
  - "GW-05 fail-closed flag clarification on the PROJECT.md GW-05 line and Egress constraint, same sentence as REQUIREMENTS.md"
affects: [phase-verification, phase-uat]

actuals:
  tokens: 1800
  tasks: 2
  commits: 2
plan_head_before: 4270584873b3de98097f0518756a3940fcbcbb8f
plan_head_after: 973abe0e8e3b2d993de1df8c1c2381a7487f4eef

tech-stack:
  added: []
  patterns:
    - "Older records receive forward notes only (ADR 0045 section 6), verified with git show --numstat"

key-files:
  created: []
  modified:
    - docs/decisions/0020-llama-cli-invocation-contract-correction.md
    - docs/decisions/0022-llama-cli-structured-output-workaround.md
    - docs/decisions/0034-retry-repeat-penalty-for-llama-cli-workers.md
    - docs/decisions/0035-codex-route-for-semantic-extraction-and-critic.md
    - docs/decisions/0036-codex-critic-repair-loop-and-instruction-calibration.md
    - docs/decisions/0037-codex-podcast-script-route.md
    - .planning/PROJECT.md

key-decisions:
  - "Every note follows the code as recorded in the 06-02, 06-04, 06-08 and 06-09 SUMMARYs; none of them reports a deviation in a flag, timeout name or default named by the notes, so the prescribed wording stood unchanged"
  - "The OPEN-2 rule in D-ADR0045-02 restates the operator answer of 06-04-SUMMARY.md (option A: configuration error at preflight, not a warning, not registry wins, a codex run stops until the duplicate entry is removed)"
  - "The Key Decisions table row for the ADR 0045 section 2 reading now ends with 'Revised; closed by ADR 0046' (punctuation added, no word removed)"

patterns-established: []

requirements-completed: [GW-05]

coverage:
  - id: D1
    description: "ADRs 0020, 0022 and 0034 point forward to the rebuilt llama contract; ADRs 0035, 0036 and 0037 point forward to the gateway-based Codex route"
    requirement: GW-05
    verification:
      - kind: automated
        ref: "rg -l 'ADR 0046' on the six files (six paths) and git show --numstat per task commit (0 deletions on all six)"
        status: pass
    human_judgment: false
  - id: D2
    description: "PROJECT.md carries revision notes on three locked decisions and the GW-05 clarification on the GW-05 line and Egress constraint"
    requirement: GW-05
    verification:
      - kind: automated
        ref: "three line-anchored rg checks from Task 2 (D-ADR0045-02, GW-05, Egress)"
        status: pass
    human_judgment: false

duration: 8min
completed: 2026-10-14
status: complete
---

# Phase 6 Plan 12: Forward notes on older ADRs and PROJECT.md revision notes Summary

**Six older ADRs and three locked PROJECT.md decisions now point forward to ADR 0046, the GW-05 fail-closed flag clarification sits on the PROJECT.md GW-05 line and Egress constraint, and no earlier sentence was removed.**

## Accomplishments

- ADRs 0020, 0022 and 0034 each gained one Status paragraph ("Rebuilt by ADR 0046" or "Carried forward by ADR 0046") describing the `LlamaCppAdapter` contract: `llama-completion` with `-n` cap and `llama-tokenize` preflight, `--single-turn --no-display-prompt --no-escape`, and the retry-only `--repeat-penalty 1.3 --repeat-last-n 256`.
- ADRs 0035, 0036 and 0037 each gained one "Amended by ADR 0046" paragraph: stages 5 and 7 reach Codex through the gateway's `CodexCliAdapter`; the repair loop keeps its script with critic-stage reviews and drafting-stage redrafts; the podcast script calls the gateway with a typed no-retry call error, an oversized prompt refused before spawning, and `--call-timeout-seconds` (default 900).
- `.planning/PROJECT.md`: revision notes on D-READ-01 (ADR 0046 closed ADR 0045 section 2), D-ADR0038-01 (local route returns as `LlamaCppAdapter` behind `LlmGateway`) and D-ADR0045-02 (flag enforcement through `remote_llm_allowed` and `remote_llm_allowed_lecture_ids`, fail closed, OPEN-2 rule, operator responsibilities retained). The Key Decisions Outcome cell and both the GW-05 and Egress lines carry the clarification.

## Task Commits

1. **Task 1 (tracer): forward notes on ADRs 0020, 0022, 0034** - `ab2a766` (docs)
2. **Task 2: forward notes on ADRs 0035, 0036, 0037 and PROJECT.md revision notes** - `973abe0` (docs)

Tracer gate: the automated verify (`rg -l "ADR 0046"` on the three files, three paths printed) and the numstat check (6/0, 6/0, 4/0) passed before Task 2.

## Deviations from Plan

None - plan executed as written. Punctuation only was added around appended text (a period after "Phase 6" and after "(GW-05)", a semicolon in the table cell) so the appended sentences read correctly; a word-level diff shows no word removed.

## Authentication Gates

None.

## Issues Encountered

None. No package or model file was downloaded.

## Known Stubs

None.

## Threat Flags

None. T-06-44 mitigated (forward notes only, zero deleted lines in the six ADRs). T-06-45 mitigated (no absolute path or private term in the diff; leak-gate and gitleaks hooks passed on both commits).

## Verification

- `rg -l "ADR 0046"` on the seven files prints seven paths
- `git show --numstat --format=` on both task commits: 0 deletions for all six ADR files
- Three line-anchored `rg` checks (D-ADR0045-02, GW-05, Egress) pass on `.planning/PROJECT.md`
- `uv run pytest -q -rs`: 2494 passed; `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`: clean

## Next Phase Readiness

All plans of Phase 6 now have a SUMMARY. The phase-level Code Review and Code Secure workflows (R0.4), phase verification and the operator's D-07 real-model run (ADR 0046 decision 10) remain with the orchestrator and the operator.

## Self-Check: PASSED

All seven modified files exist; commits `ab2a766` and `973abe0` are ancestors of HEAD; `commits: 2` measured from the persisted ledger.
