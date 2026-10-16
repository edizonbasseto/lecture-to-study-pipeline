---
phase: 07-synthetic-demo-lecture-and-measured-results
plan: 05
subsystem: demo
tags: [adr-0045, critic-gate, recovery, tree-snapshot, pydantic]
requires:
  - phase: 07-01
    provides: "lecture_study.demo package"
provides:
  - "lecture_study.demo.tree_snapshot: TreeEntry, TreeSnapshot, TreeDifference, snapshot_tree, difference"
  - "lecture_study.demo.recovery: stale and rejected inductions, delete_units_without_draft, run_recovery_scenario, RecoveryEvidence"
  - "scripts/demo_blocked_recovery.py: harvest-guarded driver that writes workspace/demo/recovery/evidence.json"
affects: [07-08]
tech-stack:
  added: []
  patterns:
    - "process runner injected as a Protocol; the script supplies the real runner"
    - "evidence built unjudged, then judged by one named predicate"
key-files:
  created:
    - src/lecture_study/demo/tree_snapshot.py
    - src/lecture_study/demo/recovery.py
    - scripts/demo_blocked_recovery.py
    - tests/demo/test_demo_tree_snapshot.py
    - tests/demo/test_demo_recovery.py
  modified: []
decisions:
  - "Symlinks are recorded through a link_target field instead of overloading the sha256 field"
  - "An empty scenario list never counts as passed"
metrics:
  duration: 14 min
  completed: 2026-10-15
status: complete
commits: 2
plan_head_before: dbaec70f1067f25841c09e1a09cbb35175b5a0ca
plan_head_after: bdfa37dbceec57174ae6fb1e7d3bd64a85e6f75e
actuals:
  tokens: 14500
  tasks: 2
  commits: 2
requirements: [DEMO-04]
---

# Phase 7 Plan 05: Blocked-lecture recovery tooling Summary

The ADR 0045 blocked-lecture recovery is now one scripted, typed and tested command: it induces a stale draft or a rejected review, proves by a filesystem snapshot that the four derivative scripts exit 1 and write nothing, runs the critic and requires all four outputs to be refreshed afterwards.

## Tasks

| Task | Name | Commit |
| ---- | ---- | ------ |
| 1 | Tracer: induced stale and rejected units block the real gate; snapshot proves nothing changed | d031f68 |
| 2 | Scenario logic, evidence model and the recovery driver with its harvest guard | bdfa37d |

## What was built

- `tree_snapshot.py`: frozen strict models and `snapshot_tree` / `difference`. Directories, symlinks (not followed) and absent roots are entries; files carry a chunked SHA-256 and `st_mtime_ns`. A root that appears after being absent is reported as a modification of `LABEL:.`.
- `recovery.py`: `RecoveryLayout` and `default_layout` (output roots exam, podcast, flashcards, study_guide; extra roots podcast_audio and llm_runs); `induce_stale_draft` (draft times set with `os.utime` in nanoseconds, two seconds past the unit); `induce_rejected_review` (atomic rewrite with `review.passed` false, unit stays newer than its draft); `delete_units_without_draft`; `ScriptRunner` Protocol; `ScenarioEvidence`, `RecoveryEvidence`; `run_recovery_scenario`. A scenario passes only when the four blocked scripts exit 1, the tree is unchanged, the critic exits 0, the four recovered scripts exit 0 and all four output roots gained a created or modified entry.
- `scripts/demo_blocked_recovery.py`: refuses to start (exit 2) while `workspace/demo/runs/llama-cpp/llm_runs/critic.json` is absent; forces `LECTURE_STUDY_LLM_ADAPTER=llama-cpp`; runs each script through `run_bounded_process` (3600 s, 32 MiB caps, no check); logs to `workspace/demo/recovery/SCENARIO-STEP.log`; writes `evidence.json`; exits 0 only when every scenario passed.

## Verification

- `uv run pytest tests/demo/test_demo_recovery.py tests/demo/test_demo_tree_snapshot.py -q`: 25 passed. The real `load_critic_approved_drafts` raises `UnresolvedReviewError` for both induced states; a blocked gate call leaves the tree identical; the fake pipeline in the scenario tests applies the real gate to fixtures.
- `ruff check .` clean; `ruff format --check` clean on this plan's files; `mypy src` (strict) clean.
- `git diff --stat HEAD -- src/lecture_study/assembly` prints nothing; no `subprocess` or `run_bounded_process` text in the two `src` modules; the script contains the three required literals.
- The live demonstration (real scripts, real adapter) was not run here; it belongs to 07-08 after the 07-06 harvest.

## Deviations from Plan

**1. [Rule 2 - Missing detail] Optional `workspace_root` on `default_layout`**
- The plan lists `default_layout(lecture_id)`; a keyword `workspace_root` (default `.`) lets tests build fixtures under `tmp_path`. Behavior with the default is as specified.

**2. [Rule 2 - Missing detail] `link_target` field on `TreeEntry`**
- The plan stores a symlink's target text "instead of a digest" without naming a field; a dedicated `link_target: str | None` keeps `sha256` meaning only a digest.

**3. Scenario evidence judged in two steps**
- `ScenarioEvidence` is first built with `passed=False`, then `_follows_adr_0045(evidence, expected_outputs=...)` decides and the result is copied in, keeping the predicate at two arguments.

`ruff format --check .` repo-wide reported `report.py` and `wer.py` (plan 07-03 files being written concurrently); out of scope and left to their owner.

## Known Stubs

None.

## Threat Flags

None. T-07-16 (gate untouched, documented inductions, exit codes and tree diff instead of messages), T-07-17 (`os.utime` in nanoseconds, atomic rewrite, no plain copy), T-07-18 (adapter forced to llama-cpp), T-07-19 (harvest guard) and T-07-20 (evidence holds step names, exit codes and relative paths) are covered by the code and tests above.

## Self-Check: PASSED

All five created files exist; commits d031f68 and bdfa37d are on `gsd/phase-07-synthetic-demo`. `plan_head_after` is HEAD at write time and includes interleaved commits of the concurrent plans 07-02, 07-03 and 07-04; `commits: 2` counts only this plan's commits.
