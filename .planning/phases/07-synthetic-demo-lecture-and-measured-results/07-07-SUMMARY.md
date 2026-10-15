---
phase: 07-synthetic-demo-lecture-and-measured-results
plan: 07
subsystem: demo
tags: [codex-adapter, remote-egress, preflight, not-run, checkpoint]
requires:
  - phase: 07-06
    provides: "scripts/demo_run.py, the demo make targets, harvest tooling and load_not_run"
provides:
  - "No-egress preflight evidence for the codex adapter on 925101 and proof that the driver refuses an unconfirmed codex run"
  - "workspace/demo/runs/codex/NOT_RUN.txt: the codex adapter is reported as not run, with the precise cause"
affects: [07-08]
tech-stack:
  added: []
  patterns:
    - "a content-free probe distinguishes a provider or configuration failure from a model contract failure when the gateway keeps only a stderr hash"
key-files:
  created: []
  modified: []
key-decisions:
  - "The codex run was not worked around: the codex CLI default model sits in user-level configuration outside the repository and the adapter passes no model, so changing either is a decision for the operator"
requirements-completed: []
duration: 25 min
completed: 2026-10-15
status: complete
commits: 0
plan_head_before: 72addbcf54c99db73f37b9b5e31592519455ced3
plan_head_after: 72addbcf54c99db73f37b9b5e31592519455ced3
actuals:
  tokens: 0
  tasks: 3
  commits: 0
---

# Phase 7 Plan 07: Codex adapter run behind an egress confirmation Summary

The no-egress preflight passed and the operator confirmed remote egress, but the codex adapter could not be measured: phase 5 failed twice in under 7 seconds because the codex CLI default model is rejected for a ChatGPT account. The adapter is recorded as not run through `workspace/demo/runs/codex/NOT_RUN.txt`. No versioned file changed, so `commits` is 0 by design (`files_modified: []`).

## Tasks

| Task | Name | State |
| ---- | ---- | ----- |
| 1 | Tracer: no-egress codex preflight for 925101 and proof of the confirmation guard | complete |
| 2 | Operator confirms remote egress of the synthetic lecture 925101 | answered: confirmed |
| 3 | Codex run of 925101 to a signed-in page view and harvest, or the not-run record | NOT_RUN recorded; the run failed, no harvest |

## Task 1: preflight (`workspace/demo/logs/925101/preflight.txt`)

| Command | Exit | Output |
| ------- | ---- | ------ |
| `codex login status` | 0 | `Logged in using ChatGPT` (status line only, nothing identifying the account) |
| `make demo-preflight ADAPTER=codex LECTURE=925101 CONFIRM_REMOTE_EGRESS=yes` | 0 | `adapter=codex lecture=925101`, `preflight=ok` |
| `make demo-run ADAPTER=codex LECTURE=925101` (no confirmation variable) | 2 | `refused: the codex adapter sends lecture content to a remote provider; pass --confirm-remote-egress to confirm`, before any preflight or process |

The preflight target needs `CONFIRM_REMOTE_EGRESS=yes` because the driver refuses codex without the flag before preflighting (the 07-06 deviation 1); a preflight sends nothing. At that time `workspace/data/llm_runs/925101/` did not exist.

## Task 2: operator checkpoint response (recorded 2026-10-15)

Checkpoint `human-action`, gate `blocking-human`. The orchestrator asked the operator explicitly before this plan ran, and the answer was passed to the executor as: the operator CONFIRMED the Codex egress for lecture 925101. The operator's stated basis: the lecture is synthetic, carries no patient data and no third-party content, and goes to an external provider knowingly.

Answer: **confirmed**, 2026-10-15. The confirmation variable was passed only after this answer.

## Task 3: the codex run failed; NOT_RUN recorded

`make demo-run ADAPTER=codex LECTURE=925101 CONFIRM_REMOTE_EGRESS=yes` (run in the background and polled):

| Attempt | Phases | Result | Wall seconds of the failing call |
| ------- | ------ | ------ | -------------------------------- |
| 1 | 2, 3, 4 done (extraction 5.7 s, transcription 7.4 s, evidence under 0.1 s) | phase 5 (semantic) failed: `codex call for stage semantic failed: non-zero exit code 1` (stderr 8652 bytes, hash only) | 6.54, 1 call, 0 retries |
| 2 (the single rerun) | 2, 3, 4 already done | phase 5 failed identically (stderr 9015 bytes, hash only) | 4.75, 1 call, 0 retries |

The gateway keeps only a hash of the child stderr, so the cause was not in the run records. A content-free diagnostic (the prompt "Reply with the single word ok", same `codex exec --skip-git-repo-check --sandbox read-only` shape, no lecture content) was run once from a scratch directory and exited 1 with:

`The 'gpt-6.1-sol' model is not supported when using Codex with a ChatGPT account.` (HTTP 400)

The model comes from the user-level codex configuration (`~/.codex/config.toml`, key `model`), outside the repository, and `CodexCliAdapter` passes no model argument. This is a provider or configuration failure, not a model contract failure, and no model output was ever produced. Per the rule not to work around a missing prerequisite, the executor did not edit the codex configuration, add a model argument to the adapter, or retry further. Because the failure precedes any model output, no first-attempt schema pass rate, critic rate or wall time exists for the codex adapter, and no harvest was made.

Evidence (gitignored, `workspace/demo/logs/925101/`): `preflight.txt`, `run-all.attempt-1.log`, `run-all.log`, `attempt-1/semantic-run-record.json`, `attempt-1/semantic-run-record-rerun.json`, `attempt-1/phase-5-failure.log`, `codex-diagnostic.txt`. The failed run record also remains at `workspace/data/llm_runs/925101/semantic.json` (adapter_name `codex`, status `failed`); the extraction, transcription and evidence outputs of 925101 exist under `workspace/data/`.

Result files: `workspace/demo/runs/codex/NOT_RUN.txt` exists with the reason above; `workspace/demo/runs/codex/harvest.json` does not (exactly one of the two, as required). The plan's automated verify passes through `NOT_RUN.txt`. The page check was not run for 925101 (no study material exists for it), and the local page 925100 was not regenerated, so it was not re-checked either.

Egress actually performed: stage 2-4 are local; one codex call per attempt (stage semantic) sent lecture-derived text to the provider and was rejected by it with the model error before processing, plus the content-free probe. No other stage ran.

## How to complete the codex measurement later

1. The operator sets a model the ChatGPT account supports in the codex configuration (or decides that the adapter should pass an explicit model, a code change outside this plan).
2. Remove `workspace/demo/runs/codex/NOT_RUN.txt`, and keep or archive the failed records in `workspace/data/llm_runs/925101/` (the harvest refuses an existing destination, so a leftover failed record would be harvested as a failed run).
3. `make demo-run ADAPTER=codex LECTURE=925101 CONFIRM_REMOTE_EGRESS=yes`, then the site, the signed-in check and `make demo-harvest ADAPTER=codex LECTURE=925101` as in Task 3 of the plan.

## Deviations from Plan

**1. [Not a rule deviation] Preflight passed the confirmation variable.** Explained under Task 1 (inherited from 07-06).

**2. [Provider or configuration failure] Task 3 ended in the not-run record without an operator decline.** The plan defines NOT_RUN for a decline; here it records an unavailable model. The text of `NOT_RUN.txt` states the real reason, and the report reads it through `load_not_run`. Reported to the orchestrator for a decision on whether the operator wants to fix the configuration and rerun.

**3. [Diagnostic] One content-free codex call** was made to identify the cause, within the same provider the operator confirmed but outside the lecture content the confirmation described. It is disclosed here.

## Known Stubs

None.

## Threat Flags

None. T-07-26: the confirmation was recorded before the confirmed run; the registry flag stayed only on 925101. T-07-27: no unflagged lecture was touched. T-07-28: the answer and date are above and the not-run reason is in `NOT_RUN.txt`. T-07-29: only the status line of `codex login status` is kept.

## Self-Check: PASSED

`workspace/demo/logs/925101/preflight.txt` shows `preflight=ok`; `workspace/demo/runs/codex/NOT_RUN.txt` exists and is non-empty; `workspace/demo/runs/codex/harvest.json` is absent; the working tree had no versioned change (`git rev-list --count` of the plan range is 0).
